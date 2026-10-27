#!/usr/bin/env python3
"""005 T009: the screener wakes the recipient after the commit of a step that released a mail to pending (R1, Q1).

    python3 -m unittest test_wake_event_screener -v

The fake backend answers (no model); rookery_wake.spawn is replaced by a recorder that reads the DB at spawn time.
"""
import os
import unittest.mock as mock

import rookery_wake
import screener
import test_screen_live as L


class Wake(L.Live):
    def setUp(self):
        super().setUp()
        os.environ.pop("ROOKERY_WAKE", None)   # Live turns the wake off; this class tests it
        self.spawned = []

        def rec(args, log_root=None):
            ids = [r["id"] for r in self.conn.execute("SELECT id FROM inbox WHERE status='pending'")]
            self.spawned.append((list(args), ids))
            return True
        p = mock.patch.object(rookery_wake, "spawn", rec)
        p.start()
        self.addCleanup(p.stop)

    def test_live_deliver_wakes_once_after_the_commit(self):
        mid = self.submit("CASE-DELIVER")
        self.assertEqual(self.row(mid)["status"], "held")
        self.spawned.clear()
        self.assertEqual(self.passes(), 1)
        self.assertEqual(len(self.spawned), 1, self.spawned)
        args, pending_at_spawn = self.spawned[0]
        self.assertEqual(args[0], "wake")
        self.assertIn(mid, pending_at_spawn)   # committed state: the row was already pending
        self.assertFalse(self.conn.in_transaction)

    def test_quarantine_wakes_only_the_sender_and_escalation_wakes_nobody(self):
        quar, esc = self.submit("CASE-DONE"), self.submit("CASE-TIMEOUT")
        self.spawned.clear()
        self.passes()
        self.assertEqual(self.row(quar)["status"], "quarantined")
        self.assertEqual(self.row(esc)["status"], "escalated")
        # the quarantine bounce is pending mail for the sender: exactly one wake, aimed at the sender
        self.assertEqual(len(self.spawned), 1, self.spawned)
        args, pending_at_spawn = self.spawned[0]
        self.assertEqual(args[args.index("--agent") + 1], self.row(quar)["sender"])
        self.assertNotIn(quar, pending_at_spawn)

    def test_a_rollback_before_the_commit_spawns_none(self):
        mid = self.submit("CASE-DELIVER")
        self.spawned.clear()
        with mock.patch.object(screener, "_finish", side_effect=RuntimeError("boom")):
            self.passes()
        self.assertEqual(self.row(mid)["status"], "held")
        self.assertEqual(self.spawned, [])

    def test_shadow_mode_spawns_none_from_the_screener(self):
        self.cfg["checks"]["t1"] = "shadow"
        self.write_config({"checks": {"t1": "shadow"}})
        mid = self.submit("CASE-DELIVER")
        self.assertEqual(self.row(mid)["status"], "pending")
        self.spawned.clear()
        self.passes()
        self.assertEqual(self.spawned, [])


for _n in [n for n in dir(L.Live) if n.startswith("test_")]:
    setattr(Wake, _n, None)   # do not rerun the inherited tests

if __name__ == "__main__":
    unittest.main()
