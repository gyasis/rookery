#!/usr/bin/env python3
"""rookery_wake.after_pending (feature 005, T006): which stored mail spawns a wake.

    python3 -m unittest test_wake_event -v
"""
import json
import os
import sqlite3
import unittest

import rookery as R
import rookery_identity as RI
import rookery_wake as W
from test_wake import WakeBase
from test_wake_lock import log_lines


class AfterPendingTest(WakeBase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self._spawn = W.spawn
        W.spawn = lambda args, log_root=None: self.calls.append(list(args)) or True

    def tearDown(self):
        W.spawn = self._spawn
        super().tearDown()

    def mail(self, kind=None, to=None, sender=None, session=None, topic="TASK-7"):
        to, sender = to or self.lc, sender or self.me
        body = json.dumps({"to": to["name"], "task": topic, "kind": kind, "say": "x", "status": "done"}) \
            if kind else "plain hello"
        return R.send(self.conn, sender["agent_id"], to["agent_id"], body, topic=topic,
                      session_id=session or self.sid)

    def test_ask_spawns_one_wake(self):
        mid = self.mail("ask")
        self.assertEqual(W.after_pending(self.conn, [mid], log_root=self.root), 1)
        self.assertEqual(self.calls, [["wake", "--session", self.sid, "--agent", self.lc["agent_id"]]])

    def test_plain_mail_spawns_one_wake(self):
        W.after_pending(self.conn, [self.mail()], log_root=self.root)
        self.assertEqual(len(self.calls), 1)

    def test_status_spawns_none(self):
        self.assertEqual(W.after_pending(self.conn, [self.mail("status")]), 0)
        self.assertEqual(self.calls, [])

    def test_self_send_spawns_none(self):
        W.after_pending(self.conn, [self.mail("ask", to=self.lc, sender=self.lc)])
        self.assertEqual(self.calls, [])

    def test_legacy_spawns_none(self):
        mid = self.mail("ask", session="legacy")
        row = self.conn.execute("SELECT session_id, status FROM inbox WHERE id=?", (mid,)).fetchone()
        self.assertEqual((row["session_id"], row["status"]), ("legacy", "pending"))
        W.after_pending(self.conn, [mid])
        self.assertEqual(self.calls, [])

    def test_closed_session_spawns_none(self):
        mid = self.mail("ask")
        RI.end_session(self.conn, "alpha")
        W.after_pending(self.conn, [mid])
        self.assertEqual(self.calls, [])

    def test_non_pending_spawns_none(self):
        mid = self.mail("ask")
        self.conn.execute("UPDATE inbox SET status='held' WHERE id=?", (mid,))
        self.conn.commit()
        W.after_pending(self.conn, [mid])
        self.assertEqual(self.calls, [])

    def test_missing_id_spawns_none(self):
        self.assertEqual(W.after_pending(self.conn, [99999]), 0)
        self.assertEqual(self.calls, [])

    def test_two_mails_for_one_agent_spawn_one_wake(self):
        ids = [self.mail("ask"), self.mail("ask", topic="TASK-8")]
        self.assertEqual(W.after_pending(self.conn, ids), 1)
        self.assertEqual(len(self.calls), 1)

    def test_database_error_logs_once_and_does_not_raise(self):
        mid = self.mail("ask")
        self.conn.close()
        self.assertEqual(W.after_pending(self.conn, [mid], log_root=self.root), 0)
        self.assertEqual(len(log_lines(self.root, "wake-spawn-failed")), 1)
        self.assertEqual(self.calls, [])
        self.conn = sqlite3.connect(":memory:")   # so tearDown can close


if __name__ == "__main__":
    unittest.main()
