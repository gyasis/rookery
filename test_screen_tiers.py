#!/usr/bin/env python3
"""004 T018: the second opinion of hunch (D11, D14, FR-009, FR-029).

    python3 -m unittest test_screen_tiers -v

T1 answers in a band. The band `review` asks hunch the same route question. Agreement acts, disagreement escalates.
The fake backend answers both kinds from one file (key `t1`, key `hunch`); no model call.
"""
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock as mock

import mail_gate
import rookery
import rookery_identity as RI
import screener
import screener_backend as SB
import screener_config
import turn_block
from test_hooks import Base
from test_screen_run import block

HERE = os.path.dirname(os.path.abspath(__file__))
BANDS = {"accept": 0.8, "review": 0.5}


class Tiers(Base):
    def setUp(self):
        super().setUp()
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.cfg["checks"].update(t1="live", hunch="live")
        self.cfg["bands"]["t1"] = BANDS
        self.fake_path = os.path.join(self.tmp.name, "fake.json")
        self.sid = self.alpha["session_id"]
        self.conn.row_factory = sqlite3.Row
        self.t1 = SB.fake(self.fake_path, "t1")
        self.hunch = SB.fake(self.fake_path, "hunch")
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)
        self.conn.execute("INSERT INTO up_agents VALUES(?,?,?,?,?,?,?,?)",
                          (self.me["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)   # the gate reads the file: hold as `screening`
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": BANDS}, "checks": {"t1": "live", "hunch": "live"}}}, fh)

    def fake(self, t1, hunch=()):
        rules = lambda rs: [{"match": m, **({"fail": x} if x in ("timeout", "bad-answer") else {"label": x, "confidence": c})}
                            for m, x, c in rs]
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": rules(t1), "hunch": rules(hunch)}, fh)

    def calls(self, kind):
        try:
            with open(self.fake_path + ".calls") as fh:
                return len([ln for ln in fh if json.loads(ln)["kind"] == kind])
        except OSError:
            return 0

    def submit(self, say):
        r = mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                             body=block(say=say), topic="T-7")
        self.assertEqual(r["outcome"], "sent", r)
        return r["msg_id"]

    def passes(self, hunch="default"):
        return screener._screen_session(self.conn, self.sid, "alpha", self.cfg, self.t1, "me:1:x",
                                        rookery.now() - self.cfg["lease_s"], [],
                                        self.hunch if hunch == "default" else hunch)

    def row(self, mid):
        return self.conn.execute("SELECT * FROM inbox WHERE id=?", (mid,)).fetchone()

    def recs(self, mid, stage=None):
        return [r for r in self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id=? ORDER BY id", (mid,))
                if stage in (None, r["stage"])]

    def notices(self, mid):
        return [r for r in self.conn.execute("SELECT * FROM inbox WHERE sender='rookery' AND body NOT LIKE '{%'")
                if f"Mail {mid} " in r["body"]]

    def test_accept_acts_with_no_hunch_call(self):
        self.fake([("CASE-A", "deliver", 0.9)])
        mid = self.submit("CASE-A")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.calls("hunch")), ("pending", 0))
        self.assertEqual(self.recs(mid, "hunch"), [])

    def test_review_asks_hunch_once_and_agreement_releases(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.row(mid)["verdict"]), ("pending", "release"))
        self.assertEqual((self.calls("t1"), self.calls("hunch")), (1, 1))
        (h,) = self.recs(mid, "hunch")
        self.assertEqual((h["check_name"], h["decided_by"], h["label"], h["confidence"], h["mode"], h["failure"]),
                         ("hunch", "hunch:vote6:qwen2.5:3b", "deliver", 0.7, "live", None))
        self.assertEqual(self.recs(mid, "t1")[0]["action"], "release")
        self.assertEqual(self.notices(mid), [])

    def test_agreement_on_a_reject_label_quarantines(self):
        self.fake([("CASE-R", "unneeded", 0.6)], [("CASE-R", "unneeded", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual(self.row(mid)["status"], "quarantined")

    def test_disagreement_escalates_with_a_notice(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "unneeded", 0.9)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "t1 and hunch disagree"))
        self.assertEqual(self.recs(mid, "t1")[0]["action"], "escalate")
        self.assertEqual(len(self.notices(mid)), 1)

    def test_a_hunch_failure_escalates_and_is_recorded(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "timeout", 0)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "hunch timeout"))
        (h,) = self.recs(mid, "hunch")
        self.assertEqual((h["label"], h["failure"], h["decided_by"]), (None, "timeout", "hunch:vote6:qwen2.5:3b"))
        self.assertEqual(len(self.notices(mid)), 1)

    def test_unset_bands_and_the_escalate_band_never_call_hunch(self):
        self.fake([("CASE-U", "deliver", 0.9), ("CASE-E", "deliver", 0.3)], [("CASE-U", "deliver", 1), ("CASE-E", "deliver", 1)])
        for case, bands in (("CASE-U", None), ("CASE-E", BANDS)):
            self.cfg["bands"]["t1"] = bands
            mid = self.submit(case)
            self.passes()
            self.assertEqual((self.row(mid)["status"], self.row(mid)["reason"]), ("escalated", "low-confidence deliver"))
        self.assertEqual(self.calls("hunch"), 0)

    def test_hunch_in_shadow_writes_the_record_and_the_mail_escalates(self):
        self.cfg["checks"]["hunch"] = "shadow"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "low-confidence deliver"))
        self.assertEqual([(h["mode"], h["label"]) for h in self.recs(mid, "hunch")], [("shadow", "deliver")])
        self.assertEqual(len(self.notices(mid)), 1)

    def test_hunch_off_makes_no_call(self):
        self.cfg["checks"]["hunch"] = "off"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.calls("hunch"), self.recs(mid, "hunch")), ("escalated", 0, []))

    def test_no_hunch_backend_escalates_at_review(self):
        self.fake([("CASE-R", "deliver", 0.6)])
        mid = self.submit("CASE-R")
        self.passes(hunch=None)
        self.assertEqual((self.row(mid)["reason"], self.recs(mid, "hunch")), ("low-confidence deliver", []))

    def test_t1_shadow_writes_the_same_records_and_acts_on_nothing(self):
        self.cfg["checks"]["t1"] = "shadow"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")   # t1 was live at submit time: the gate holds it
        self.conn.execute("UPDATE inbox SET status='pending', reason=NULL WHERE id=?", (mid,))
        self.conn.commit()
        self.passes()
        self.assertEqual(self.row(mid)["status"], "pending")
        self.assertEqual([(r["stage"], r["mode"], r["action"]) for r in self.recs(mid)],
                         [("t1", "shadow", "none"), ("hunch", "live", "none")])

    def test_hunch_runs_with_six_votes_on_the_stdlib_path(self):   # HUNCH_VOTE=6, through the real backend class
        log = os.path.join(self.tmp.name, "hunch.log")
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        env = {"PATH": os.path.join(HERE, "fixtures", "bin") + os.pathsep + os.environ["PATH"],
               "ROOKERY_FAKE_HUNCH": self.fake_path, "ROOKERY_FIXTURE_LOG": log, "ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}
        mid = self.submit("CASE-R")
        with mock.patch.dict(os.environ, env):
            os.environ.pop("ROOKERY_SCREENER_FAKE", None)
            self.passes(hunch=SB.hunch_vote(self.cfg))
        with open(log) as fh:
            (call,) = [json.loads(ln) for ln in fh]
        self.assertEqual((call["HUNCH_VOTE"], call["HUNCH_MODEL"]), ("6", "qwen2.5:3b"))
        self.assertEqual(self.row(mid)["status"], "pending")


class ShadowHunch(Tiers):
    def test_F9_a_shadow_hunch_record_never_acts_by_agreement(self):
        self.fake([("CASE-DELIVER", "deliver", 0.6)], [("CASE-DELIVER", "deliver", 0.9)])
        self.cfg["checks"]["hunch"] = "shadow"
        mid = self.submit("CASE-DELIVER")
        self.passes()
        self.assertEqual([r["mode"] for r in self.recs(mid, "hunch")], ["shadow"])
        self.cfg["checks"]["hunch"] = "live"
        self.assertTrue(screener._act_pre(self.conn, mid, "me:1:x", self.cfg, "{}"))
        self.assertEqual(self.row(mid)["status"], "escalated")
        self.assertTrue(self.row(mid)["reason"].startswith("low-confidence"))


class T2(Tiers):
    """T2 (D15): a configured command decides escalated mail. No model, no call: the fixtures are scripts."""

    def setUp(self):
        super().setUp()
        self.cfg["checks"]["t2"] = "live"

    def command(self, name="t2_ok.py"):
        self.cfg["t2"]["command"] = [sys.executable, os.path.join(HERE, "fixtures", "screener", name)]

    def escalate(self, *cases):
        """Submit one mail for each case and let T1 escalate it (an answer in the band escalate)."""
        self.fake([(c, "deliver", 0.3) for c in cases])
        ids = [self.submit(c) for c in cases]
        self.passes()
        for mid in ids:
            self.assertEqual(self.row(mid)["status"], "escalated")
        return ids

    def test_no_command_leaves_it_escalated_and_calls_nothing(self):
        (mid,) = self.escalate("CASE-DELIVER")
        with mock.patch("subprocess.Popen") as run:
            self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)
        run.assert_not_called()
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")), ("escalated", []))

    def test_good_script_decides_two_of_three_and_the_third_keeps_a_failure(self):
        a, b, c = self.escalate("CASE-DELIVER", "CASE-UNNEEDED", "CASE-OTHER")
        self.command()
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 3)
        self.assertEqual((self.row(a)["status"], self.row(a)["decided_by"]), ("pending", "t2:t2_ok"))
        self.assertEqual((self.row(b)["status"], self.row(b)["reason"]), ("quarantined", "unneeded"))
        self.assertEqual(self.row(c)["status"], "escalated")
        (ra,), (rb,), (rc,) = self.recs(a, "t2"), self.recs(b, "t2"), self.recs(c, "t2")
        self.assertEqual((ra["check_name"], ra["label"], ra["mode"], ra["action"], ra["decided_by"]),
                         ("t2", "deliver", "live", "release", "t2:t2_ok"))
        self.assertEqual((rb["label"], rb["action"]), ("unneeded", "quarantine"))
        self.assertEqual((rc["label"], rc["failure"], rc["action"]), (None, "bad-answer", "none"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_calls").fetchone()[0], 0)   # only a person writes calls
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 1)   # a mail with a label is not sent again; c is tried again
        self.assertEqual((len(self.recs(a, "t2")), len(self.recs(c, "t2"))), (1, 2))
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)   # F3: three failures, then it waits for a person
        self.assertEqual(len(self.recs(c, "t2")), 3)

    def test_batch_size_limits_the_stdin_lines(self):
        ids = self.escalate("CASE-A", "CASE-B", "CASE-C")
        self.cfg["t2"]["batch"] = 2
        self.command()
        real = screener._t2_run
        seen = []
        with mock.patch("screener._t2_run", side_effect=lambda c, text, t: (seen.append(text), real(c, text, t))[1]):
            self.assertEqual(screener.t2_pass(self.conn, self.cfg), 2)
        (text,) = seen
        self.assertEqual([json.loads(ln)["mail_id"] for ln in text.splitlines()], ids[:2])   # oldest first
        self.assertEqual(self.recs(ids[2], "t2"), [])

    def test_bad_script_keeps_all_escalated(self):
        ids = self.escalate("CASE-DELIVER", "CASE-UNNEEDED")
        self.command("t2_bad.py")
        screener.t2_pass(self.conn, self.cfg)
        for mid in ids:
            self.assertEqual(self.row(mid)["status"], "escalated")
            (r,) = self.recs(mid, "t2")
            self.assertEqual((r["failure"], r["label"]), ("bad-answer", None))

    def test_a_timeout_and_a_missing_command_keep_escalated(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.command()
        with mock.patch("subprocess.Popen", return_value=mock.Mock(pid=2**22 + 1, communicate=mock.Mock(
                side_effect=subprocess.TimeoutExpired("x", 1)))):
            screener.t2_pass(self.conn, self.cfg)
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")[0]["detail"]), ("escalated", "timeout"))
        (m2,) = self.escalate("CASE-DELIVER2")
        self.cfg["t2"]["command"] = ["/no/such/t2-command"]
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual((self.row(m2)["status"], self.recs(m2, "t2")[0]["failure"]), ("escalated", "bad-answer"))

    def test_shadow_records_and_changes_no_status(self):
        self.cfg["checks"]["t2"] = "shadow"
        (mid,) = self.escalate("CASE-DELIVER")
        self.command()
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(self.row(mid)["status"], "escalated")
        self.assertEqual([(r["mode"], r["label"], r["action"]) for r in self.recs(mid, "t2")], [("shadow", "deliver", "none")])

    def test_an_answer_for_a_mail_outside_the_batch_is_ignored(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.command()
        out = json.dumps({"mail_id": 99999, "label": "deliver", "reason": "x"}) + "\n"
        with mock.patch("screener._t2_run", return_value=(out, None)):
            screener.t2_pass(self.conn, self.cfg)
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")[0]["failure"]), ("escalated", "bad-answer"))

    def test_an_exception_never_escapes(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.command()
        with mock.patch("subprocess.Popen", side_effect=RuntimeError("boom")):
            self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)
        self.assertEqual(self.row(mid)["status"], "escalated")

    # --- wave 12 audit fixes ---

    def test_F1_a_human_escalation_is_never_sent(self):
        self.fake([("CASE-DELIVER", "deliver", 0.99)])
        mid = self.submit("CASE-DELIVER")
        self.assertEqual(self.row(mid)["status"], "held")
        rookery.escalate(self.conn, mid, "human:x", "held for a person")
        self.command()
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")), ("escalated", []))
        # and _t2_act re-checks inside its transaction
        screener._t2_act(self.conn, self.row(mid), 1, "deliver", "t2:x")
        self.assertEqual(self.row(mid)["status"], "escalated")

    def test_F2_a_mail_with_a_retry_pending_is_not_sent(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.conn.execute("UPDATE screen_mail SET retry_at=? WHERE mail_id=?", (rookery.now() + 60, mid))
        self.conn.commit()
        self.command()
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)
        self.assertEqual(self.row(mid)["status"], "escalated")

    def test_F7_a_failed_screen_is_not_sent_and_a_low_confidence_one_is(self):
        self.fake([("CASE-FAIL", "timeout", 0)])
        fail = self.submit("CASE-FAIL")
        self.passes()
        self.assertEqual(self.row(fail)["status"], "escalated")
        self.assertFalse(self.row(fail)["reason"].startswith("low-confidence"))
        (low,) = self.escalate("CASE-DELIVER")
        self.conn.execute("UPDATE screen_mail SET retry_at=NULL")
        self.conn.commit()
        self.command()
        self.assertEqual([r["id"] for r in screener._t2_batch(self.conn, self.cfg, self.sid)], [low])
        self.conn.execute("UPDATE inbox SET reason='envelope-too-large' WHERE id=?", (low,))
        self.conn.commit()
        self.assertEqual(screener._t2_batch(self.conn, self.cfg, self.sid), [])

    def test_F3_one_failed_run_then_a_good_run_decides_the_mail(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.command("t2_bad.py")
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(self.row(mid)["status"], "escalated")
        self.command()
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 1)
        self.assertEqual(self.row(mid)["status"], "pending")
        self.assertEqual([r["label"] for r in self.recs(mid, "t2")], [None, "deliver"])

    def test_F4_decided_by_is_the_script_name_or_t2_name(self):
        (a,) = self.escalate("CASE-DELIVER")
        self.cfg["t2"]["name"] = "my judge"
        self.command()
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(self.row(a)["decided_by"], "t2:my judge")
        t = screener_config.t2_name
        self.assertEqual(t({"name": None, "command": ["/usr/bin/python3.12", "/x/judge.py"]}), "judge")
        self.assertEqual(t({"name": None, "command": ["bash", "run.sh"]}), "run")
        self.assertEqual(t({"name": None, "command": ["/opt/judge"]}), "judge")

    def test_F5_a_grandchild_holding_the_pipes_cannot_hold_the_pass(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.cfg["timeout_s"] = 1
        self.cfg["lease_s"] = 2
        self.command("t2_grandchild.py")
        t0 = time.monotonic()
        screener.t2_pass(self.conn, self.cfg)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")[0]["detail"]), ("escalated", "timeout"))

    def test_F5_huge_output_and_a_non_zero_exit_fail_cleanly(self):
        (mid,) = self.escalate("CASE-DELIVER")
        self.command("t2_huge.py")
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual((self.row(mid)["status"], self.recs(mid, "t2")[0]["detail"]), ("escalated", "output too large"))
        self.command("t2_exit.py")
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(self.recs(mid, "t2")[1]["detail"], "exit=3: boom reason")

    def test_F6_a_fresh_envelope_over_the_limit_is_not_sent_and_a_corrupt_one_skips_only_itself(self):
        a, b = self.escalate("CASE-DELIVER", "CASE-UNNEEDED")
        self.command()
        self.conn.execute("UPDATE screen_mail SET envelope=NULL WHERE mail_id=?", (a,))
        self.conn.execute("UPDATE screen_mail SET envelope='{not json' WHERE mail_id=?", (b,))
        self.conn.commit()
        self.cfg["envelope_max_chars"] = 10
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 0)
        self.cfg["envelope_max_chars"] = 3000
        self.assertEqual(self.recs(a, "t2")[0]["failure"], "envelope-too-large")
        self.assertEqual(self.recs(b, "t2")[0]["failure"], "bad-envelope")
        c, = self.escalate("CASE-UNNEEDED2")
        self.conn.execute("UPDATE screen_mail SET envelope='{not json' WHERE mail_id=?", (b,))
        self.conn.commit()
        self.assertEqual(screener.t2_pass(self.conn, self.cfg), 2)   # b fails again; a (now fits) and c are sent
        self.assertEqual(len(self.recs(b, "t2")), 2)
        self.assertEqual(len(self.recs(c, "t2")), 1)

    def test_F8_a_t2_deliver_does_not_approve_the_thread(self):
        self.fake([("CASE-DELIVER", "deliver", 0.3)])
        mid = self.submit("CASE-DELIVER")
        self.conn.execute("UPDATE inbox SET thread='th1' WHERE id=?", (mid,))
        self.conn.execute("INSERT INTO threads(session_id, thread, status, opened_by, first_mail, opened_at) "
                          "VALUES(?,?,'held','x',?,0)", (self.sid, "th1", mid))
        self.conn.commit()
        self.passes()
        self.assertEqual(self.row(mid)["status"], "escalated")
        self.command()
        screener.t2_pass(self.conn, self.cfg)
        self.assertEqual(self.row(mid)["status"], "pending")
        self.assertEqual(self.conn.execute("SELECT status FROM threads WHERE thread='th1'").fetchone()[0], "held")

    def beta_escalated(self, say):
        """An escalated screener mail of a second session."""
        beta = RI.new_session(self.conn, name="beta")
        a, b = (RI.new_identity(self.conn, beta["session_id"], n) for n in ("a", "b"))
        body = json.dumps({"to": "b", "task": "T", "kind": "ask", "say": say, "status": "in-progress"})
        mid = rookery.send(self.conn, a["agent_id"], b["agent_id"], body, topic="T", hold=True,
                           session_id=beta["session_id"])
        rookery._decide(self.conn, mid, "held", "escalated", "escalate", "low-confidence unneeded", "screener:tev1:4b")
        return mid, beta["session_id"]

    def test_t2_serves_only_the_sessions_of_the_process_and_one_session_a_batch(self):   # audit 5
        (a,) = self.escalate("CASE-DELIVER")
        b, beta = self.beta_escalated("CASE-DELIVER")
        log = os.path.join(self.tmp.name, "t2.log")
        self.command("t2_log.py")
        with mock.patch.dict(os.environ, {"T2_LOG": log}):
            self.assertEqual(screener.t2_pass(self.conn, self.cfg, [self.sid]), 1)
            self.assertEqual((self.row(a)["status"], self.row(b)["status"]), ("pending", "escalated"))
            self.assertEqual(self.recs(b, "t2"), [])
            self.assertEqual(screener.t2_pass(self.conn, self.cfg), 1)
        self.assertEqual(self.row(b)["status"], "pending")
        with open(log) as fh:
            self.assertEqual([json.loads(x) for x in fh], [[a], [b]])   # one run for each session

    def test_t2_two_batches_for_two_sessions_in_one_pass(self):   # audit 5
        a, b = self.escalate("CASE-DELIVER 1")[0], self.beta_escalated("CASE-DELIVER 2")[0]
        log = os.path.join(self.tmp.name, "t2.log")
        self.command("t2_log.py")
        with mock.patch.dict(os.environ, {"T2_LOG": log}):
            self.assertEqual(screener.t2_pass(self.conn, self.cfg), 2)
        with open(log) as fh:
            runs = [json.loads(x) for x in fh]
        self.assertEqual(sorted(runs), [[a], [b]])

    def test_t2_two_processes_never_send_the_same_mail(self):   # audit 5
        ids = self.escalate("CASE-DELIVER 1", "CASE-DELIVER 2", "CASE-DELIVER 3")
        log = os.path.join(self.tmp.name, "t2.log")
        self.command("t2_log.py")
        done = []

        def worker():
            c = rookery.connect(turn_block.db_path())
            try:
                done.append(screener.t2_pass(c, self.cfg))
            finally:
                c.close()
        with mock.patch.dict(os.environ, {"T2_LOG": log, "T2_SLEEP": "1.5"}):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        with open(log) as fh:
            sent = [i for x in fh for i in json.loads(x)]
        self.assertEqual(sorted(sent), sorted(ids))   # each mail once
        self.assertEqual(sorted(done), [0, 3])
        for mid in ids:
            self.assertEqual(len(self.recs(mid, "t2")), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_mail WHERE owner IS NOT NULL").fetchone()[0], 0)

    def test_no_code_names_a_t2_model(self):   # scan: a model-like name next to t2
        pat = re.compile(r"t2[^\n]{0,80}(tev1|qwen|llama|gemma|gpt|claude|mistral|phi\d|\w+:\d+b)|"
                         r"(tev1|qwen|llama|gemma|gpt|claude|mistral)[^\n]{0,80}\bt2\b", re.I)
        for name in ("screener.py", "screener_config.py", "cli_screen.py"):
            with open(os.path.join(HERE, name)) as fh:
                hits = [ln.strip() for ln in fh if pat.search(ln)]
            self.assertEqual(hits, [], name)


for _name in [n for n in vars(Tiers) if n.startswith("test_")]:
    setattr(T2, _name, None)   # T2 reuses the helpers of Tiers, not its cases
    setattr(ShadowHunch, _name, None) if _name != "test_F9_a_shadow_hunch_record_never_acts_by_agreement" else None


if __name__ == "__main__":
    unittest.main()
