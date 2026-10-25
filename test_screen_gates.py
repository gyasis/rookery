#!/usr/bin/env python3
"""004 T022: the worker gates G1, G2, G3 and G5 (D19, FR-030, RQ-R.1).

    python3 -m unittest test_screen_gates -v

Only an ask gives anomalies. Each anomaly is its own `gates` record. Shadow mode records and delivers. Live mode
parks the mail as `escalated` (decided_by t0:gates) and sends the escalation notice. G4 and P1 are in no code.
"""
import json
import os
import re
import shutil
import sqlite3
import unittest
from unittest import mock

import mail_gate
import rookery
import screener_t0
import plugins
import screener_backend as SB
import test_screen_b2
from test_hooks import Base
from test_screen_run import FIXTURE


class Gates(Base):
    config, recs, row, passes, set_budget = test_screen_b2.B2.config, test_screen_b2.B2.recs, test_screen_b2.B2.row, test_screen_b2.B2.passes, test_screen_b2.B2.set_budget

    def setUp(self):
        super().setUp()
        self.conn.row_factory = sqlite3.Row
        self.sid = self.alpha["session_id"]
        self.fake_path = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake_path)
        self.backend = SB.fake(self.fake_path)
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)
        self.config()

    def gconfig(self, **checks):
        self.config()
        self.cfg["checks"].update(checks)
        path = os.path.join(self.tmp.name, ".rookery", "config.json")
        with open(path) as fh:
            data = json.load(fh)
        data["screener"]["checks"].update(checks)
        with open(path, "w") as fh:
            json.dump(data, fh)

    def gask(self, sc=None, **kw):
        say = kw.pop("say", "CASE-DELIVER read the file")
        task = kw.pop("task", "T-7")
        blk = {"kind": kw.pop("kind", "ask"), "say": say, "task": task}
        if sc is not None:
            blk["self_check"] = sc
        return mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x",
                                to=kw.pop("to", "researcher"), body=f"body {say}", topic=task, block=blk)

    def labels(self, r):
        return [x["label"] for x in self.recs(r["msg_id"], "gates")]

    # --- the function
    def test_each_anomaly_alone(self):
        for sc, want in (({"task_complete": True}, "g1-task-complete"), ({"gap": ""}, "g2-no-gap"),
                         ({"gap": "  "}, "g2-no-gap"), ({"gap_in_my_scope": True}, "g3-gap-in-scope")):
            got = screener_t0.gate_anomalies({"kind": "ask", "self_check": sc}, False, False)
            self.assertEqual([a[0] for a in got], [want], sc)
        self.assertEqual([a[0] for a in screener_t0.gate_anomalies({"kind": "ask"}, True, False)], ["g5-ask-not-allowed"])
        self.assertEqual([a[0] for a in screener_t0.gate_anomalies({"kind": "ask"}, False, True)], ["g5-ask-not-allowed"])

    def test_clean_ask_and_non_ask_give_none(self):
        ok = {"task_complete": False, "gap": "the schema is missing", "gap_in_my_scope": False}
        self.assertEqual(screener_t0.gate_anomalies({"kind": "ask", "self_check": ok}, False, False), [])
        self.assertEqual(screener_t0.gate_anomalies({"kind": "ask"}, False, False), [])
        bad = {"task_complete": True, "gap": "", "gap_in_my_scope": True}
        for kind in ("answer", "status", "handoff", "recruit", None):
            self.assertEqual(screener_t0.gate_anomalies({"kind": kind, "self_check": bad}, True, True), [])
        self.assertEqual(screener_t0.gate_anomalies(None, True, True), [])

    def test_detail_never_holds_the_gap_text(self):
        got = screener_t0.gate_anomalies({"kind": "ask", "self_check": {"gap": "", "task_complete": True}}, False, False)
        self.assertTrue(all(len(d) < 40 for _, d in got))

    # --- through the gate, shadow
    def test_all_four_on_one_ask_give_four_records(self):
        self.gconfig(budget="shadow", gates="shadow")
        self.set_budget(1)
        self.gask()
        r = self.gask({"task_complete": True, "gap": "", "gap_in_my_scope": True}, say="CASE-DELIVER second")
        self.assertEqual(self.labels(r), ["g1-task-complete", "g2-no-gap", "g3-gap-in-scope", "g5-ask-not-allowed"])
        for rec in self.recs(r["msg_id"], "gates"):
            self.assertEqual((rec["stage"], rec["mode"], rec["action"], rec["decided_by"]),
                             ("t0", "shadow", "none", "t0:gates"))
        self.assertEqual(self.row(r)["status"], "pending")   # shadow: delivered

    def test_non_ask_kind_gives_no_record(self):
        r = self.gask({"task_complete": True}, kind="answer")
        self.assertEqual(self.labels(r), [])

    def test_no_record_when_gates_is_off(self):
        self.gconfig(gates="off")
        r = self.gask({"task_complete": True})
        self.assertEqual(self.labels(r), [])

    def test_b1_failure_writes_g5_on_the_quarantined_mail(self):
        r = self.gask({"gap": "x"}, to="nobody-here")
        self.assertEqual(r["outcome"], "quarantined")
        self.assertEqual(self.labels(r), ["g5-ask-not-allowed"])
        self.assertEqual(self.recs(r["msg_id"], "gates")[0]["detail"], "roster check failed")

    # --- live
    def live(self):
        self.gconfig(gates="live")

    def test_live_escalates_and_notifies(self):
        self.live()
        with mock.patch.object(screener_t0, "notify_escalated") as note:
            r = self.gask({"task_complete": True, "gap": ""})
        row = self.row(r)
        self.assertEqual((row["status"], row["decided_by"]), ("escalated", "t0:gates"))
        self.assertIn("g1-task-complete", row["reason"])
        self.assertIn("g2-no-gap", row["reason"])
        note.assert_called_once()
        self.assertEqual(note.call_args[0][1], r["msg_id"])
        self.assertEqual(r["reason"], "gates")
        for rec in self.recs(r["msg_id"], "gates"):
            self.assertEqual((rec["mode"], rec["action"]), ("live", "escalate"))

    def test_live_notice_is_a_real_mail_to_the_boss(self):
        self.live()
        self.conn.execute("INSERT INTO up_agents VALUES(?,?,?,?,?,?,?,?)",
                          (self.me["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()
        with mock.patch.object(plugins, "notify", return_value=0):
            r = self.gask({"gap_in_my_scope": True})
        n = self.conn.execute("SELECT body FROM inbox WHERE sender='rookery' AND recipient=?",
                              (self.me["agent_id"],)).fetchone()
        self.assertIn(f"Mail {r['msg_id']}", n[0])
        self.assertNotIn("CASE-DELIVER", n[0])    # no mail body in the notice

    def test_live_clean_ask_is_delivered(self):
        self.live()
        r = self.gask({"task_complete": False, "gap": "real gap", "gap_in_my_scope": False})
        self.assertEqual(self.row(r)["status"], "pending")

    def test_budget_live_wins_over_gates(self):
        self.gconfig(budget="live", gates="live")
        self.set_budget(1)
        self.gask()
        r = self.gask({"task_complete": True}, say="CASE-DELIVER again")
        row = self.row(r)
        self.assertEqual((row["status"], row["reason"]), ("held", "budget"))
        for rec in self.recs(r["msg_id"], "gates"):
            self.assertEqual(rec["action"], "none")

    def test_gates_wins_over_screening(self):
        self.gconfig(gates="live", t1="live")
        r = self.gask({"task_complete": True})
        self.assertEqual(self.row(r)["status"], "escalated")

    def test_loop_hold_drops_the_gate_records(self):
        self.live()
        with mock.patch.object(rookery, "count_loop", return_value=True):
            r = self.gask({"task_complete": True})
        self.assertEqual((r["outcome"], self.labels(r)), ("loop-held", []))

    def test_escalated_by_gates_is_not_t2_mail_and_the_screener_leaves_it(self):
        self.live()
        with mock.patch.object(screener_t0, "notify_escalated"):
            r = self.gask({"task_complete": True})
        import screener
        row = self.row(r)
        self.assertFalse(screener._t2_mine(row))
        self.assertEqual(screener._t2_batch(self.conn, self.cfg), [])
        self.passes()
        row = self.row(r)
        self.assertEqual((row["status"], row["decided_by"]), ("escalated", "t0:gates"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_decisions WHERE mail_id=? AND action<>'none' "
                                           "AND stage<>'t0'", (r["msg_id"],)).fetchone()[0], 0)

    def test_failed_notice_keeps_the_escalation(self):
        self.live()
        with mock.patch.object(screener_t0, "notify_escalated", side_effect=RuntimeError("boom")):
            r = self.gask({"task_complete": True})
        self.assertEqual((r["outcome"], self.row(r)["status"]), ("sent", "escalated"))


class NoPersonaChecks(unittest.TestCase):
    def test_g4_and_p1_are_in_no_code(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("screener.py", "screener_t0.py", "screener_config.py", "screener_backend.py", "cli_screen.py",
                     "rookery_names.py"):
            path = os.path.join(here, name)
            if not os.path.exists(path):
                continue
            with open(path) as fh:
                text = fh.read()
            self.assertIsNone(re.search(r"peer_match|(?<!gap_)in_my_scope", text), name)


if __name__ == "__main__":
    unittest.main()
