#!/usr/bin/env python3
"""Feature 005 T007: the gate wakes the recipient after the commit of a pending mail.

    python3 -m unittest test_wake_event_gate -v
"""
import json
import os
import unittest

import mail_gate as G
import rookery_identity as RI
import rookery_wake as W
import turn_block as TB
from test_gate import GateBase


class GateWake(GateBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("ROOKERY_WAKE", None)   # GateBase turns the wake off; this class tests it
        self.calls = []
        self.boom = False
        self._spawn = W.spawn

        def rec(args, log_root=None):
            # a second connection, as the detached child would open, must see committed pending mail
            import sqlite3
            other = sqlite3.connect(os.environ["ROOKERY_DB"])
            try:
                self.seen_pending = [r[0] for r in other.execute("SELECT id FROM inbox WHERE status='pending'")]
            finally:
                other.close()
            self.calls.append(list(args))
            if self.boom:
                raise OSError("no spawn")
            return True
        W.spawn = rec
        self.rid = self.researcher_id()

    def tearDown(self):
        W.spawn = self._spawn
        super().tearDown()

    def researcher_id(self):
        return self.conn.execute("SELECT agent_id FROM identities WHERE session_id=? AND name='researcher'",
                                 (self.alpha["session_id"],)).fetchone()[0]

    def ask(self, **kw):
        return self.submit(to="researcher", body="please review", topic="TASK-1", **kw)

    def test_spawn_sees_the_committed_row(self):
        res = self.ask()
        self.assertEqual(res["outcome"], "sent", res)
        self.assertIn(res["msg_id"], self.seen_pending)

    def test_ask_spawns_one_wake(self):
        r = self.ask()
        self.assertEqual(r["outcome"], "sent", r)
        self.assertEqual(self.calls, [["wake", "--session", self.alpha["session_id"], "--agent", self.rid]])

    def test_loop_held_spawns_none(self):
        limit, _ = TB.loop_limits()
        for i in range(limit):
            self.ask()
        self.calls.clear()
        self.assertEqual(self.ask()["outcome"], "loop-held")
        self.assertEqual(self.calls, [])

    def test_quarantined_mail_spawns_none_for_the_recipient(self):
        r = self.submit(to="nobody")
        self.assertEqual(r["outcome"], "quarantined", r)
        self.assertNotIn(self.rid, [c[-1] for c in self.calls])

    def test_spawn_that_raises_leaves_the_result(self):
        self.boom = True
        r = self.ask()
        self.assertEqual((r["outcome"], r["reason"]), ("sent", None))
        self.assertEqual(len(self.calls), 1)       # the spawn was tried and raised
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM inbox WHERE id=?", (r["msg_id"],)).fetchone())

    def test_status_mail_spawns_none(self):
        r = self.submit(to="researcher", body=json.dumps({"kind": "status", "text": "fyi"}), topic="TASK-1")
        self.assertEqual(r["outcome"], "sent", r)
        self.assertEqual(self.calls, [])

    def test_system_send_spawns_one_wake(self):
        mid = G.system_send(self.conn, "rookery", self.rid, "bounce text", topic="TASK-1",
                            session_id=self.alpha["session_id"], root=self.root)
        self.assertTrue(mid)
        self.assertEqual(self.calls, [["wake", "--session", self.alpha["session_id"], "--agent", self.rid]])


if __name__ == "__main__":
    unittest.main()
