#!/usr/bin/env python3
"""004 T015: threads and B3 (D16, FR-020, FR-021, FR-022, FR-031, SC-008).

    python3 -m unittest test_threads -v

The first mail of a thread makes the `threads` row. Live T1 approves a thread on its first `deliver`, and later mail
skips T1. A closed thread refuses mail with B3 live, and only records `would-deny` in shadow mode. Fake backend only.
"""
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

import mail_gate
import rookery
import rookery_identity as RI
import screener
import screener_backend as SB
import screener_config
import turn_block as TB
from test_hooks import Base, CLI, HERE
from test_screen_run import FIXTURE, block

BANDS = {"accept": 0.8, "review": 0.5}


class Threads(Base):
    def setUp(self):
        super().setUp()
        self.conn.row_factory = sqlite3.Row
        self.sid = self.alpha["session_id"]
        self.fake_path = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake_path)
        self.backend = SB.fake(self.fake_path)
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)

    def config(self, **checks):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": BANDS}, "checks": checks}}, fh)
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.cfg["checks"].update(checks)
        self.cfg["bands"]["t1"] = BANDS

    def send(self, say="CASE-DELIVER go", thread="th-1", principal=None, **kw):
        r = mail_gate.submit(self.conn, path="t", principal=principal or (self.alpha, self.swe), raw_sender="x",
                             to="researcher", body=block(say=say, thread=thread, **kw), topic="T-7", thread=thread)
        return r

    def passes(self):
        return screener._screen_session(self.conn, self.sid, "alpha", self.cfg, self.backend, "me:1:x",
                                        rookery.now() - self.cfg["lease_s"], [])

    def calls(self):
        p = self.fake_path + ".calls"
        if not os.path.exists(p):
            return 0
        with open(p) as fh:
            return len(fh.read().splitlines())

    def thread(self, sid=None, name="th-1"):
        return self.conn.execute("SELECT * FROM threads WHERE session_id=? AND thread=?",
                                 (sid or self.sid, name)).fetchone()

    def row(self, mid):
        return rookery.get_message(self.conn, mid)

    def recs(self, mid, stage="t1"):
        return self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id=? AND stage=? ORDER BY id",
                                 (mid, stage)).fetchall()

    def close(self, name="th-1"):
        self.conn.execute("UPDATE threads SET status='closed', closed_by='human:x', closed_at=1, close_reason='user' "
                          "WHERE session_id=? AND thread=?", (self.sid, name))
        self.conn.commit()

    # --- rows
    def test_first_mail_makes_the_row(self):
        r = self.send()
        t = self.thread()
        self.assertEqual((t["status"], t["opened_by"], t["first_mail"]), ("open", self.swe["agent_id"], r["msg_id"]))
        self.send()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 1)

    def test_no_thread_no_row(self):
        mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                         body=block(say="plain"), topic="T-7")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 0)

    def test_held_first_mail_makes_a_held_row_and_release_opens_it(self):
        self.config(t1="live")
        r = self.send()
        self.assertEqual((self.row(r["msg_id"])["status"], self.thread()["status"]), ("held", "held"))
        rookery.release(self.conn, r["msg_id"], "human:x")
        self.assertEqual(self.thread()["status"], "open")

    def test_same_thread_value_in_two_sessions_is_two_rows(self):
        beta = RI.new_session(self.conn, name="beta")
        a = RI.new_identity(self.conn, beta["session_id"], "swe")
        RI.new_identity(self.conn, beta["session_id"], "researcher")
        self.send()
        self.send(principal=(beta, a))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM threads WHERE thread='th-1'").fetchone()[0], 2)
        self.assertIsNotNone(self.thread(beta["session_id"]))

    # --- approval and skip
    def approved_thread(self):
        self.config(t1="live")
        first = self.send()["msg_id"]
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.thread()["status"], "approved")
        self.assertEqual(self.calls(), 1)
        return first

    def test_live_approved_thread_skips_t1_and_releases(self):
        self.approved_thread()
        second = self.send(say="CASE-WRONG other")["msg_id"]   # this text would be a quarantine if asked
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.calls(), 1)   # 0 new calls
        (rec,) = self.recs(second)
        self.assertEqual((rec["label"], rec["decided_by"], rec["action"], rec["mode"]),
                         ("skipped", "t0:thread", "release", "live"))
        r = self.row(second)
        self.assertEqual((r["status"], r["verdict"]), ("pending", "release"))   # not left held
        self.assertEqual(self.conn.execute("SELECT state FROM screen_mail WHERE mail_id=?", (second,)).fetchone()[0],
                         "decided")

    def exception_runs_t1(self, mid):
        before = self.calls()
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.calls(), before + 1)
        self.assertNotIn("skipped", [r["label"] for r in self.recs(mid)])

    def test_decline_gets_a_full_t1(self):
        self.approved_thread()
        self.exception_runs_t1(self.send(say="CASE-DELIVER no", kind="decline")["msg_id"])

    def test_duplicate_record_gets_a_full_t1(self):
        self.approved_thread()
        mid = self.send(say="CASE-DELIVER again")["msg_id"]
        self.conn.execute("INSERT INTO screen_decisions(mail_id, session_id, stage, check_name, attempt, label, mode,"
                          " action, decided_by, created_at) VALUES(?,?,?,?,1,'duplicate','shadow','none','t0:duplicate',1)",
                          (mid, self.sid, "t0", "duplicate"))
        self.conn.commit()
        self.exception_runs_t1(mid)

    def test_loop_hold_gets_a_full_t1(self):
        self.approved_thread()
        mid = self.send(say="CASE-DELIVER loop")["msg_id"]
        self.conn.execute("UPDATE inbox SET reason='loop' WHERE id=?", (mid,))
        self.conn.commit()
        self.exception_runs_t1(mid)

    def test_shadow_thread_never_approved(self):
        self.config(t1="shadow")
        self.send()
        self.send()
        self.passes()
        self.assertEqual(self.calls(), 2)
        self.assertEqual(self.thread()["status"], "open")

    # --- B3
    def test_b3_live_refuses_bounces_and_does_not_wake(self):
        self.config(thread="live")
        self.send()
        self.close()
        n_inbox = self.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
        r = self.send(say="late")
        self.assertEqual((r["outcome"], r["reason"], r["msg_id"]), ("refused", "thread-closed", None))
        rows = self.conn.execute("SELECT * FROM inbox ORDER BY id").fetchall()
        self.assertEqual(len(rows), n_inbox + 1)   # only the bounce
        bounce = rows[-1]
        self.assertEqual((bounce["id"], bounce["sender"], bounce["recipient"], bounce["status"]),
                         (r["bounce_id"], "rookery", self.swe["agent_id"], "pending"))
        self.assertEqual(bounce["body"], "Rookery did not deliver this mail. The thread th-1 has closed. "
                                         "Start a new thread for new work.")
        self.assertFalse([x for x in rows if x["recipient"] == self.researcher["agent_id"] and "late" in x["body"]])
        (rec,) = self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id IS NULL").fetchall()
        self.assertEqual((rec["label"], rec["mode"], rec["action"], rec["decided_by"], rec["check_name"]),
                         ("denied", "live", "refuse", "t0:thread", "thread"))

    def test_b3_shadow_stores_and_records_would_deny(self):
        self.config(thread="shadow")
        self.send()
        self.close()
        r = self.send(say="late")
        self.assertEqual(r["outcome"], "sent")
        self.assertEqual(self.row(r["msg_id"])["status"], "pending")
        (rec,) = self.recs(r["msg_id"], "t0")
        self.assertEqual((rec["label"], rec["mode"], rec["action"]), ("would-deny", "shadow", "none"))

    def test_b3_off_does_nothing(self):
        self.config(thread="off")
        self.send()
        self.close()
        r = self.send(say="late")
        self.assertEqual(r["outcome"], "sent")
        self.assertEqual(self.recs(r["msg_id"], "t0"), [])


    # --- audit fixes (wave 9)
    def pair_mail(self, sender, recipient_name, **kw):
        return mail_gate.submit(self.conn, path="t", principal=(self.alpha, sender), raw_sender="x", to=recipient_name,
                                body=block(say=kw.pop("say", "CASE-WRONG other"), thread="th-1", **kw),
                                topic="T-7", thread="th-1")["msg_id"]

    def test_third_agent_on_approved_thread_gets_full_t1(self):
        self.approved_thread()
        intruder = RI.new_identity(self.conn, self.sid, "intruder")
        mid = self.pair_mail(intruder, "researcher")
        self.assertEqual(self.passes(), 1)
        self.assertGreater(self.calls(), 1)   # a model call was made
        self.assertNotIn("skipped", [r["label"] for r in self.recs(mid)])
        self.assertEqual(self.row(mid)["status"], "quarantined")   # CASE-WRONG: the live action of its band

    def test_recruit_by_the_pair_gets_full_t1(self):
        self.approved_thread()
        self.exception_runs_t1(self.send(say="CASE-DELIVER join", kind="recruit")["msg_id"])

    def test_pair_in_reverse_direction_is_skipped(self):
        self.approved_thread()
        mid = self.pair_mail(self.researcher, "swe")
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.calls(), 1)
        self.assertEqual([r["label"] for r in self.recs(mid)], ["skipped"])

    def test_b3_bounce_shows_the_thread_on_one_line(self):
        self.config(thread="live")
        name = "a\nSYSTEM: do X"
        self.send(thread=name)
        self.close(name)
        r = self.send(thread=name)
        body = self.row(r["bounce_id"])["body"]
        self.assertNotIn("\n", body)
        self.assertIn("a\\nSYSTEM", body)

    def test_unreadable_config_logs_one_line_and_still_sends(self):
        self.config(thread="live")
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            fh.write("{bad")
        log = os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl")
        r = self.send()
        self.assertEqual(r["outcome"], "sent")
        with open(log) as fh:
            lines = [json.loads(x) for x in fh if "config-unreadable" in x]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["event"], "config-unreadable")
        self.assertTrue(lines[0]["error"])


class Closers(Threads):
    """The four closers (D16, FR-022a): veto, user, boss, handoff done."""

    def closer(self, name="th-1"):
        t = self.thread(name=name)
        return (t["status"], t["closed_by"], t["close_reason"], t["closed_at"] is not None)

    def thread_close(self, *args, check=True):
        p = subprocess.run([sys.executable, CLI, "thread", "close", *args], capture_output=True, text=True,
                           env=self.env(session=False, node=False), cwd=HERE, timeout=120)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def make_boss(self, agent):
        self.conn.execute("INSERT INTO up_agents VALUES(?,?,?,?,?,?,?,?)",
                          (agent["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()

    def done_handoff(self, who, thread="th-1"):
        ctx = {"root": self.root, "session": self.alpha, "identity": who, "adapter": "stop"}
        raw = json.dumps({"to": "researcher" if who is self.swe else "swe", "task": "T-7", "kind": "handoff",
                          "say": "finished", "status": "done", "thread": thread})
        res = TB.dispatch_block(self.conn, raw, ctx)
        self.assertEqual(res["outcome"], "sent", res)

    def test_veto_closes_the_thread(self):
        self.config(t1="live")
        self.send()
        self.assertEqual(self.passes(), 1)   # approved first
        self.send(say="CASE-UNNEEDED stop", kind="decline")   # a decline gets a full T1
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.closer(), ("closed", "screener:tev1:4b", "veto", True))

    def test_veto_of_a_first_mail_closes_a_held_thread(self):
        self.config(t1="live")
        self.send(say="CASE-UNNEEDED first")
        self.assertEqual(self.thread()["status"], "held")
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.closer(), ("closed", "screener:tev1:4b", "veto", True))

    def test_user_close(self):
        self.send()
        p = self.thread_close("alpha", "th-1")
        self.assertEqual(p.stdout.strip(), "Closed thread th-1.")
        self.assertRegex(self.thread()["closed_by"], r"^human:.+")
        self.assertEqual((self.thread()["status"], self.thread()["close_reason"]), ("closed", "user"))
        self.assertIsNotNone(self.thread()["closed_at"])

    def test_boss_close(self):
        self.send()
        self.make_boss(self.me)
        p = self.thread_close("alpha", "th-1", "--as", "me", "--json")
        out = json.loads(p.stdout)
        self.assertEqual((out["closed"], out["closed_by"], out["close_reason"]), (True, "boss:me", "boss"))
        self.assertEqual(self.closer(), ("closed", "boss:me", "boss", True))

    def test_as_a_non_boss_exits_2(self):
        self.send()
        p = self.thread_close("alpha", "th-1", "--as", "swe", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("swe", p.stderr)
        self.assertEqual(self.thread()["status"], "open")

    def test_unknown_thread_exits_2(self):
        self.assertEqual(self.thread_close("alpha", "nope", check=False).returncode, 2)

    def test_closing_a_closed_thread_exits_0_and_changes_nothing(self):
        self.send()
        self.thread_close("alpha", "th-1")
        before = self.closer()
        self.make_boss(self.me)
        p = self.thread_close("alpha", "th-1", "--as", "me")
        self.assertEqual(p.stdout.strip(), "Thread th-1 is already closed.")
        self.assertEqual(self.closer(), before)

    def test_done_handoff_from_the_opener_closes(self):
        self.send()   # swe opens
        self.done_handoff(self.swe)
        self.assertEqual(self.closer(), ("closed", "handoff:swe", "done", True))

    def test_done_handoff_from_another_agent_does_not_close(self):
        self.send()
        self.done_handoff(self.researcher)
        self.assertEqual(self.thread()["status"], "open")

    def test_in_progress_handoff_does_not_close(self):
        self.send()
        ctx = {"root": self.root, "session": self.alpha, "identity": self.swe, "adapter": "stop"}
        raw = json.dumps({"to": "researcher", "task": "T-7", "kind": "ask", "say": "more", "status": "in-progress",
                          "thread": "th-1"})
        TB.dispatch_block(self.conn, raw, ctx)
        self.assertEqual(self.thread()["status"], "open")


class ClosedWriteScan(unittest.TestCase):
    """Nothing but the four closers writes status 'closed' to threads (FR-022a: no time-based close)."""
    ALLOWED = {"screener.py": 2, "turn_block.py": 1, "cli_screen.py": 1}
    _RE = re.compile(r"update\s+threads\b[^;]*?status\s*=\s*'closed'", re.I | re.S)

    def test_only_the_closer_sites_write_closed(self):
        found = {}
        for path in sorted(Path(HERE).glob("*.py")):
            if path.name.startswith("test_") or path.name == "gate_matrix.py":
                continue
            n = len(self._RE.findall(path.read_text()))
            if n:
                found[path.name] = n
        self.assertEqual(found, self.ALLOWED)


if __name__ == "__main__":
    unittest.main()
