#!/usr/bin/env python3
"""004 T014: the live actions (D13, D14, FR-002, FR-012a, FR-019, FR-027, FR-028, FR-029).

    python3 -m unittest test_screen_live -v

The gate holds a mail as `screening` (pre_hold), the loop acts on it (screener._screen_session), and the escalation
notice reaches the boss and the plugin sink. The fake backend answers; no model call.
"""
import argparse
import contextlib
import copy
import io
import json
import os
import shutil
import sqlite3
import unittest
import unittest.mock as mock

import cli_screen
import mail_gate
import plugins
import rookery
import rookery_identity as RI
import screener
import screener_backend as SB
import screener_config
import screener_t0
from test_hooks import Base
from test_screen_run import FIXTURE, block

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET = "SECRET-BODY-TEXT"
BANDS = {"accept": 0.8, "review": 0.5}


class Live(Base):
    def setUp(self):
        super().setUp()
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.cfg["checks"]["t1"] = "live"
        self.cfg["bands"]["t1"] = BANDS
        self.fake_path = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake_path)
        self.backend = SB.fake(self.fake_path)
        self.sid = self.alpha["session_id"]
        self.conn.row_factory = sqlite3.Row
        self.write_config({"checks": {"t1": "live"}})
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)

    def calls(self):
        p = self.fake_path + ".calls"
        if not os.path.exists(p):
            return 0
        with open(p) as fh:
            return len(fh.read().splitlines())

    def row(self, mid):
        return self.conn.execute("SELECT * FROM inbox WHERE id=?", (mid,)).fetchone()

    def recs(self, mid):
        return self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id=? ORDER BY id", (mid,)).fetchall()

    def write_config(self, screener_block):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": BANDS}, **screener_block}}, fh)

    def submit(self, say):
        r = mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                             body=block(say=say + " " + SECRET), topic="T-7")
        self.assertEqual(r["outcome"], "sent", r)
        return r["msg_id"]

    def passes(self):
        return screener._screen_session(self.conn, self.sid, "alpha", self.cfg, self.backend, "me:1:x",
                                        rookery.now() - self.cfg["lease_s"], [])

    def state(self, mid):
        return self.conn.execute("SELECT * FROM screen_mail WHERE mail_id=?", (mid,)).fetchone()

    def notices(self, to=None):
        return [r for r in self.conn.execute("SELECT * FROM inbox WHERE sender='rookery' AND body NOT LIKE '{%'")
                if to is None or r["recipient"] == to]

    def boss(self):
        self.conn.execute("INSERT INTO up_agents VALUES(?,?,?,?,?,?,?,?)",
                          (self.me["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()

    def test_pre_holds_with_screening_and_deliver_releases(self):
        mid = self.submit("CASE-DELIVER")
        self.assertEqual((self.row(mid)["status"], self.row(mid)["reason"]), ("held", "screening"))
        self.assertEqual(self.passes(), 1)
        r = self.row(mid)
        self.assertEqual((r["status"], r["verdict"], r["decided_by"]), ("pending", "release", "screener:tev1:4b"))
        (rec,) = self.recs(mid)
        self.assertEqual((rec["mode"], rec["action"], rec["label"]), ("live", "release", "deliver"))
        s = self.state(mid)
        self.assertEqual((s["state"], s["final_label"]), ("decided", "deliver"))

    def test_each_verdict_maps_to_its_action(self):   # FR-027, FR-002
        RI.new_identity(self.conn, self.sid, "counsel")
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": "roster:", "label": "counsel", "confidence": 0.8},
                              {"match": "CASE-DONE", "label": "task-already-done", "confidence": 0.9},
                              {"match": "CASE-WRONG", "label": "wrong-recipient", "confidence": 0.9},
                              {"match": "CASE-UNNEEDED", "label": "unneeded", "confidence": 0.9}]}, fh)
        want = {"CASE-DONE": "task-already-done", "CASE-WRONG": "wrong-recipient", "CASE-UNNEEDED": "unneeded"}
        mids = {case: self.submit(case) for case in want}
        reasons = []
        real = rookery.quarantine
        with mock.patch.object(rookery, "quarantine",
                               lambda c, m, reason, *a, **k: (reasons.append(reason), real(c, m, reason, *a, **k))[1]):
            self.passes()
        for case, label in want.items():
            r = self.row(mids[case])
            self.assertEqual((r["status"], r["reason"], r["verdict"]), ("quarantined", label, "quarantine"), case)
            self.assertEqual(self.recs(mids[case])[0]["action"], "quarantine")
        self.assertEqual(sorted(reasons), sorted(want.values()))   # never a loop reason
        bounces = [json.loads(r[0]) for r in self.conn.execute("SELECT body FROM inbox WHERE sender='rookery'")]
        (bounce,) = [b for b in bounces if b["msg_id"] == mids["CASE-WRONG"]]
        self.assertEqual((bounce["reason"], bounce["suggest"]), ("wrong-recipient", "counsel"))

    def test_failure_escalates_and_only_a_later_answer_releases(self):   # FR-012a
        self.boss()
        mid = self.submit("CASE-TIMEOUT")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"], r["verdict"]), ("escalated", "timeout", "escalate"))
        s = self.state(mid)
        self.assertEqual(s["state"], "escalated")
        self.assertGreater(s["retry_at"], rookery.now())
        self.assertEqual(self.recs(mid)[0]["action"], "escalate")
        n = len(self.notices(self.me["agent_id"]))
        self.assertEqual(n, 1)
        self.assertEqual(self.passes(), 0)   # the retry is not due: no call, no release
        self.assertEqual(self.calls(), 1)
        self.conn.execute("UPDATE screen_mail SET claimed_at=0 WHERE mail_id=?", (mid,))   # an old time is no release
        self.conn.commit()
        self.assertEqual((self.passes(), self.row(mid)["status"]), (0, "escalated"))
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": "CASE-TIMEOUT", "label": "deliver", "confidence": 0.9}]}, fh)
        self.conn.execute("UPDATE screen_mail SET retry_at=0 WHERE mail_id=?", (mid,))
        self.conn.commit()
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.row(mid)["status"], "pending")
        self.assertEqual([r["attempt"] for r in self.recs(mid)], [1, 2])
        self.assertEqual(self.state(mid)["state"], "decided")
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)   # the release sent no notice

    def test_failed_retry_stays_escalated_with_one_notice(self):
        self.boss()
        mid = self.submit("CASE-TIMEOUT")
        self.passes()
        self.conn.execute("UPDATE screen_mail SET retry_at=0 WHERE mail_id=?", (mid,))
        self.conn.commit()
        self.passes()
        s = self.state(mid)
        self.assertEqual((self.row(mid)["status"], s["state"], s["attempts"]), ("escalated", "escalated", 2))
        self.assertGreater(s["retry_at"], rookery.now())
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(screener.retry_delay(self.cfg, 2), 120)
        self.assertEqual(screener.retry_delay(self.cfg, 99), self.cfg["timeout_s"])

    def test_a_person_who_decided_first_wins(self):
        mid = self.submit("CASE-DELIVER")
        real = screener.screen_one

        def late(conn, m, cfg, backend):
            out = real(conn, m, cfg, backend)
            rookery.quarantine(conn, m, "unneeded", "human:ann", bounce=False)
            return out
        with mock.patch.object(screener, "screen_one", late):
            self.passes()
        self.assertEqual((self.row(mid)["status"], self.row(mid)["decided_by"]), ("quarantined", "human:ann"))
        self.assertEqual(self.state(mid)["state"], "decided")

    def test_rookery_hold_mail_gets_shadow_records_only(self):
        os.environ["ROOKERY_HOLD"] = "1"
        ok, bad = self.submit("CASE-DELIVER"), self.submit("CASE-TIMEOUT")
        self.assertEqual((self.row(ok)["status"], self.row(ok)["reason"]), ("held", None))
        self.passes()
        for mid in (ok, bad):
            self.assertEqual(self.row(mid)["status"], "held")
            self.assertEqual([(r["mode"], r["action"]) for r in self.recs(mid)], [("shadow", "none")])
        self.assertEqual((self.state(ok)["state"], self.state(bad)["state"]), ("decided", "failed"))
        self.assertEqual(self.notices(), [])

    def test_post_mode_delivers_at_once_and_a_reject_label_sends_the_notice(self):
        self.write_config({"checks": {"t1": "live"}, "review_mode": {"agents": {"researcher": "post"}}})
        self.boss()
        ok, bad, fail = self.submit("CASE-DELIVER"), self.submit("CASE-DONE"), self.submit("CASE-TIMEOUT")
        for mid in (ok, bad, fail):
            self.assertEqual(self.row(mid)["status"], "pending")
        self.passes()
        for mid in (ok, bad, fail):
            self.assertEqual(self.row(mid)["status"], "pending")   # a delivered mail is not taken back
            self.assertEqual([(r["mode"], r["action"]) for r in self.recs(mid)], [("live", "none")])
        self.assertEqual((self.state(ok)["state"], self.state(bad)["state"], self.state(fail)["state"]),
                         ("decided", "decided", "failed"))
        bodies = [n["body"] for n in self.notices(self.me["agent_id"])]
        self.assertEqual(len(bodies), 2)
        want = "Rookery delivered mail %d from swe to researcher. The screener says %s. Use rookery screen review to check it."
        self.assertEqual(sorted(bodies), sorted([want % (bad, "task-already-done"), want % (fail, "no answer (timeout)")]))

    def test_notice_text_reaches_boss_with_no_body_and_fires_the_sink(self):
        self.boss()
        mid = self.submit("CASE-TIMEOUT")
        with mock.patch.object(plugins, "notify", return_value=0) as notify:
            self.passes()
        (n,) = self.notices(self.me["agent_id"])
        self.assertEqual(n["body"], "Mail %d from swe to researcher waits for a decision. Reason: timeout. "
                                    "Use rookery screen release or rookery screen quarantine." % mid)
        self.assertNotIn(SECRET, n["body"])
        (call,) = notify.call_args_list
        self.assertEqual(call.args[0], "escalated")
        self.assertNotIn(SECRET, repr(call))
        self.assertEqual(call.kwargs, {"mail_id": mid, "sender": "swe", "recipient": "researcher",
                                       "reason": "timeout"})

    def test_no_boss_means_only_the_sink_event(self):   # FR-029
        mid = self.submit("CASE-TIMEOUT")
        with mock.patch.object(plugins, "notify", return_value=0) as notify:
            self.passes()
        self.assertEqual(self.notices(), [])
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(self.row(mid)["status"], "escalated")

    def test_pre_hold_answers(self):
        res = self.conn.execute("SELECT * FROM identities WHERE agent_id=?", (self.researcher["agent_id"],)).fetchone()
        self.assertEqual(screener_t0.pre_hold(self.conn, res, self.cfg), ("held", "screening"))
        self.assertIsNone(screener_t0.pre_hold(self.conn, None, self.cfg))
        for mode in ("shadow", "off"):
            self.cfg["checks"]["t1"] = mode
            self.assertIsNone(screener_t0.pre_hold(self.conn, res, self.cfg))
        self.cfg["checks"]["t1"] = "live"
        self.cfg["review_mode"]["agents"]["researcher"] = "post"
        self.assertIsNone(screener_t0.pre_hold(self.conn, res, self.cfg))
        self.cfg["review_mode"]["agents"] = {}
        os.environ["ROOKERY_HOLD"] = "1"
        self.assertIsNone(screener_t0.pre_hold(self.conn, res, self.cfg))

    def test_shadow_mode_acts_on_nothing(self):
        self.cfg["checks"]["t1"] = "shadow"
        self.write_config({"checks": {"t1": "shadow"}})
        mid = self.submit("CASE-DONE")
        self.assertEqual(self.row(mid)["status"], "pending")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.recs(mid)[0]["mode"]), ("pending", "shadow"))

    # --- fixes of the wave-8 audit ---

    def fake(self, *rules):
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": m, "label": lab, "confidence": c} for m, lab, c in rules]}, fh)

    def test_only_the_accept_band_acts(self):   # D11
        self.boss()
        for band, conf in (("accept", 0.9), ("review", 0.6), ("escalate", 0.3), ("unset", 0.9)):
            for label in ("deliver", "unneeded"):
                with self.subTest(band=band, label=label):
                    self.cfg["bands"]["t1"] = None if band == "unset" else BANDS
                    case = f"CASE-{band}-{label}"
                    self.fake((case, label, conf))
                    mid = self.submit(case)
                    self.passes()
                    r, (rec,), s = self.row(mid), self.recs(mid), self.state(mid)
                    self.assertEqual(rec["band"], band)
                    if band == "accept":
                        self.assertEqual((r["status"], rec["action"]),
                                         ("pending", "release") if label == "deliver" else ("quarantined", "quarantine"))
                        continue
                    self.assertEqual((r["status"], r["reason"], rec["action"]), ("escalated", f"low-confidence {label}", "escalate"))
                    self.assertEqual((s["state"], s["retry_at"], s["final_label"]), ("escalated", None, label))
                    self.assertEqual(len([n for n in self.notices() if f"low-confidence {label}" in n["body"]
                                          and f"Mail {mid} " in n["body"]]), 1)

    def test_an_exception_on_the_act_path_escalates_never_fails(self):
        self.boss()
        self.fake(("CASE-X", "deliver", 0.95))
        mid = self.submit("CASE-X")
        with mock.patch.object(rookery, "_decide_from_open", side_effect=RuntimeError("database is locked")):
            self.passes()
        r, s = self.row(mid), self.state(mid)
        self.assertEqual((r["status"], r["reason"], s["state"]), ("escalated", "screening-failed", "escalated"))
        self.assertGreater(s["retry_at"], rookery.now())
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)

    def test_when_escalating_fails_too_the_claim_is_left_to_expire(self):
        self.boss()
        self.fake(("CASE-X", "deliver", 0.95))
        mid = self.submit("CASE-X")
        with mock.patch.object(rookery, "_decide_from_open", side_effect=RuntimeError("busy")), \
                mock.patch.object(rookery, "_decide", side_effect=RuntimeError("busy")):
            self.passes()
        self.assertEqual((self.row(mid)["status"], self.state(mid)["state"]), ("held", "claimed"))
        self.assertEqual(self.notices(), [])

    def test_the_attempt_cap_escalates_a_screening_hold(self):
        self.boss()
        mid, other = self.submit("CASE-A"), self.submit("CASE-B")
        os.environ["ROOKERY_HOLD"] = "1"   # this one is the user's hold, not the screener's
        shadow = self.submit("CASE-C")
        for m in (mid, shadow):
            self.conn.execute("INSERT INTO screen_mail(mail_id,session_id,state,owner,claimed_at,attempts) "
                              "VALUES(?,?,'claimed','dead',1,3)", (m, self.sid))
        self.conn.commit()
        self.assertFalse(screener._claim(self.conn, mid, self.sid, "me:1:x", rookery.now() - 10, self.cfg))
        r, s = self.row(mid), self.state(mid)
        self.assertEqual((r["status"], r["reason"], s["state"], s["retry_at"]), ("escalated", "screening-failed", "escalated", None))
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)
        self.assertFalse(screener._claim(self.conn, shadow, self.sid, "me:1:x", rookery.now() - 10, self.cfg))
        self.assertEqual((self.row(shadow)["status"], self.state(shadow)["state"]), ("held", "failed"))
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)
        self.assertEqual(self.row(other)["status"], "held")

    def cli(self, fn, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(screener, "backend_path", return_value="stdlib"):   # no 6 s promptchain import
            fn(argparse.Namespace(**kw))
        return out.getvalue(), err.getvalue()

    def test_leaving_live_escalates_the_mail_the_screener_holds(self):
        self.boss()
        a, b = self.submit("CASE-A"), self.submit("CASE-B")
        os.environ["ROOKERY_HOLD"] = "1"
        mine = self.submit("CASE-C")   # held by the user, no reason
        os.environ.pop("ROOKERY_HOLD")
        for mode in ("shadow", "off"):
            self.write_config({"checks": {"t1": "live"}})
            if mode == "off":   # a second round on fresh held mail
                a, b = self.submit("CASE-D"), self.submit("CASE-E")
            with mock.patch.object(cli_screen, "_host_answers", return_value=True):
                out, _ = self.cli(cli_screen.cmd_mode, check="t1", mode=mode)
                self.assertIn("Escalated 2 mails", out)
                for m in (a, b):
                    self.assertEqual((self.row(m)["status"], self.row(m)["reason"]), ("escalated", "screening-stopped"))
                self.assertEqual(self.row(mine)["status"], "held")
                st, _ = self.cli(cli_screen.cmd_status, session=None, json=False)
                self.assertIn("held_screening=0", st)
        self.assertEqual(len(self.notices(self.me["agent_id"])), 4)
        out, _ = self.cli(cli_screen.cmd_mode, check="t1", mode="off")   # not live now: nothing to stop
        self.assertNotIn("Escalated", out)

    def test_a_hand_edit_away_from_live_escalates_on_the_next_pass(self):
        self.boss()
        mid = self.submit("CASE-DELIVER")
        self.assertEqual(self.row(mid)["reason"], "screening")
        self.cfg = {**self.cfg, "checks": {**self.cfg["checks"], "t1": "shadow"}}   # config.json edited by hand
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "screening-stopped"))
        self.assertEqual(self.recs(mid)[0]["mode"], "shadow")
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)

    def test_status_counts_the_mail_held_for_screening(self):
        self.submit("CASE-A"), self.submit("CASE-B")
        with mock.patch.object(cli_screen, "_host_answers", return_value=True):
            out, _ = self.cli(cli_screen.cmd_status, session=None, json=True)
            text, _ = self.cli(cli_screen.cmd_status, session=None, json=False)
        (sess,) = [x for x in json.loads(out)["sessions"] if x["session"] == "alpha"]
        self.assertEqual(sess["held_screening"], 2)
        self.assertIn("held_screening=2", text)

    def test_a_person_who_released_first_gets_the_post_notice_and_the_record_says_none(self):
        self.boss()
        self.fake(("CASE-R", "unneeded", 0.9))
        mid = self.submit("CASE-R")
        fail = self.submit("CASE-TIMEOUT")
        real = screener.screen_one

        def late(conn, m, cfg, backend):
            out = real(conn, m, cfg, backend)
            rookery.release(conn, m, "human:ann")
            return out
        with mock.patch.object(screener, "screen_one", late):
            self.passes()
        for m in (mid, fail):
            self.assertEqual(self.row(m)["status"], "pending")
            self.assertEqual([r["action"] for r in self.recs(m)], ["none"])
            self.assertEqual(self.state(m)["state"], "decided")
        self.assertEqual(sorted(n["body"] for n in self.notices(self.me["agent_id"])), sorted([
            "Rookery delivered mail %d from swe to researcher. The screener says unneeded. "
            "Use rookery screen review to check it." % mid,
            "Rookery delivered mail %d from swe to researcher. The screener says no answer (bad-answer). "
            "Use rookery screen review to check it." % fail]))

    def test_the_suggest_lookup_is_scoped_to_the_attempt(self):
        mid = self.submit("CASE-A")
        for peer in ("counsel", "none"):
            screener._write(self.conn, self.row(mid), stage="suggest", check_name="suggest", mode="live",
                            decided_by="screener:tev1:4b", label=peer)
        self.assertEqual(screener._last(self.conn, mid, "suggest", 1)["label"], "counsel")
        self.assertEqual(screener._last(self.conn, mid, "suggest", 2)["label"], "none")

    def test_finish_logs_a_lost_lease(self):
        mid = self.submit("CASE-A")
        self.assertTrue(screener._claim(self.conn, mid, self.sid, "a:1:x", rookery.now() - 10, self.cfg))
        with mock.patch.object(screener, "_log") as log:
            screener._finish(self.conn, mid, "b:2:y", self.cfg, "deliver", None)
        self.assertEqual(log.call_count, 1)
        self.assertIn("b:2:y", log.call_args.args[2])
        self.assertEqual(self.state(mid)["state"], "claimed")

    def test_a_failed_notice_does_not_fail_screen_escalate(self):
        os.environ["ROOKERY_HOLD"] = "1"
        mid = self.submit("CASE-A")
        with mock.patch.object(screener_t0, "notify_escalated", side_effect=RuntimeError("sink down")):
            out, err = self.cli(cli_screen.cmd_escalate, mail_id=mid, reason="why", by="ann")
        self.assertEqual(self.row(mid)["status"], "escalated")
        self.assertIn("Escalated mail", out)
        self.assertIn("sink down", err)

    def test_held_for_screening_still_counts_for_the_loop_breaker(self):   # FR-039
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": 2, "loop_pair_limit": 50, "screener": {"bands": {"t1": BANDS},
                                                                           "checks": {"t1": "live"}}}, fh)
        ids = []
        for i in range(3):
            ids.append(mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x",
                                        to="researcher", body=block(say=f"msg {i}"), topic="T-7"))
        self.assertEqual([(r["outcome"], r["reason"]) for r in ids],
                         [("sent", "screening"), ("sent", "screening"), ("loop-held", "loop")])
        self.assertEqual([self.row(r["msg_id"])["reason"] for r in ids], ["screening", "screening", "loop"])

    # --- final audit fixes ---

    def test_a_person_who_escalates_during_decide_is_not_overridden(self):   # audit 1
        mid = self.submit("CASE-DELIVER")
        real = self.backend.decide

        def late(*a, **k):
            out = real(*a, **k)
            rookery.escalate(self.conn, mid, "human:x", "mine")
            return out
        with mock.patch.object(self.backend, "decide", late):
            self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["decided_by"]), ("escalated", "human:x"))
        (rec,) = self.recs(mid)
        self.assertEqual(rec["action"], "none")
        self.assertEqual(self.state(mid)["state"], "decided")

    def test_a_lost_claim_changes_nothing(self):   # audit 6
        mid = self.submit("CASE-DELIVER")
        real = screener.screen_one

        def stolen(conn, m, cfg, backend, **kw):
            out = real(conn, m, cfg, backend, **kw)
            conn.execute("UPDATE screen_mail SET owner='other:1:y' WHERE mail_id=?", (m,))
            conn.commit()
            return out
        with mock.patch.object(screener, "screen_one", stolen):
            self.passes()
        self.assertEqual((self.row(mid)["status"], self.row(mid)["reason"]), ("held", "screening"))
        self.assertEqual(self.recs(mid)[0]["action"], "none")
        self.assertEqual(self.state(mid)["owner"], "other:1:y")

    @property
    def gate_log(self):
        return os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl")

    def gate_lines(self, event):
        with open(self.gate_log) as fh:
            return [json.loads(x) for x in fh if event in x]

    def test_an_invalid_value_in_the_config_turns_the_gate_checks_off(self):   # audit 2
        for block_ in ({"checks": {"gates": "on"}}, {"ask_budget": "20"}):
            self.write_config(block_)
            before = len(self.gate_lines("config-invalid")) if os.path.exists(self.gate_log) else 0
            r = mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x",
                                 to="researcher", body=block(say="x"), topic="T-7")
            self.assertEqual(r["outcome"], "sent", r)
            self.assertEqual(self.row(r["msg_id"])["status"], "pending")   # no pre_hold either
            self.assertEqual(len(self.gate_lines("config-invalid")), before + 1)   # one line for each send
        self.assertTrue(all(x["error"] for x in self.gate_lines("config-invalid")))

    def test_live_without_bands_does_not_hold_and_run_escalates_held_mail(self):   # audit 3
        held = self.submit("CASE-DELIVER")
        self.assertEqual(self.row(held)["reason"], "screening")
        self.write_config({"checks": {"t1": "live"}, "bands": {"t1": None}})
        r = mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                             body=block(say="later"), topic="T-7")
        self.assertEqual(self.row(r["msg_id"])["status"], "pending")
        self.boss()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(screener.run(once=True), 2)
        row = self.row(held)
        self.assertEqual((row["status"], row["reason"], row["decided_by"]),
                         ("escalated", "screening-stopped", "screener:tev1:4b"))
        self.assertEqual(len(self.notices(self.me["agent_id"])), 1)
        self.assertEqual(self.calls(), 0)

    def test_screening_mail_is_screened_whatever_its_age(self):   # audit 4
        mid = self.submit("CASE-DELIVER")
        self.conn.execute("UPDATE inbox SET created_at=? WHERE id=?", (rookery.now() - 90000, mid))
        self.conn.commit()
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.row(mid)["status"], "pending")

    def test_live_retries_stop_after_three_failed_retries(self):   # audit 9
        self.boss()
        mid = self.submit("CASE-TIMEOUT")
        self.passes()
        for _ in range(screener.MAX_RETRIES):
            self.conn.execute("UPDATE screen_mail SET retry_at=0 WHERE mail_id=?", (mid,))
            self.conn.commit()
            self.assertEqual(self.passes(), 1)
        self.assertEqual(self.calls(), 1 + screener.MAX_RETRIES)
        s = self.state(mid)
        self.assertEqual((self.row(mid)["status"], s["state"], s["retry_at"]), ("escalated", "escalated", None))
        self.assertEqual(self.passes(), 0)   # nothing is due and nothing is asked again
        self.assertEqual(self.calls(), 1 + screener.MAX_RETRIES)
        self.assertEqual(len(self.notices()), 1)

    def test_the_hook_path_never_imports_the_screener(self):
        for name in ("screener_t0.py", "mail_gate.py"):
            with open(os.path.join(HERE, name)) as fh:
                src = fh.read()
            for bad in ("import screener\n", "import screener_backend", "import promptchain"):
                self.assertNotIn(bad, src, name)


if __name__ == "__main__":
    unittest.main()
