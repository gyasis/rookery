#!/usr/bin/env python3
"""T008: release, unquarantine and requeue_dlq wake the recipient after the commit (feature 005, R1).

    python3 -m unittest test_wake_event_release -v

`rookery_wake.spawn` is replaced by a recorder: no process starts.
"""
import json
import unittest

import rookery as R
import rookery_wake as RW
from test_wake import WakeBase


class ReleaseWake(WakeBase):
    def setUp(self):
        super().setUp()
        self.spawned = []
        self._spawn = RW.spawn
        RW.spawn = lambda args, log_root=None: self.spawned.append(list(args)) or True

    def tearDown(self):
        RW.spawn = self._spawn
        super().tearDown()

    def held(self, body="please review"):
        return R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], body,
                      topic="TASK-7", session_id=self.sid, hold=True)

    def expect(self):
        return [["wake", "--session", self.sid, "--agent", self.lc["agent_id"]]]

    def test_release_spawns_one_wake(self):
        mid = self.held()
        self.assertEqual(self.spawned, [])
        R.release(self.conn, mid, "tev1")
        self.assertEqual(self.spawned, self.expect())

    def test_quarantine_bounce_wakes_the_sender(self):
        # a person (or cli) quarantines a held mail: the bounce is pending mail for the sender
        mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "please review", topic="T",
                     session_id=self.sid, hold=True)
        self.spawned.clear()
        bid = R.quarantine(self.conn, mid, "wrong-recipient", "human:test")
        self.assertIsNotNone(bid)
        self.assertEqual(self.spawned, [["wake", "--session", self.sid, "--agent", self.me["agent_id"]]])

    def test_unquarantine_spawns_one_wake(self):
        mid = self.held()
        R.quarantine(self.conn, mid, "unneeded", "tev1", bounce=False)
        self.assertEqual(self.spawned, [])
        R.unquarantine(self.conn, mid, "tev2")
        self.assertEqual(self.spawned, self.expect())

    def test_requeue_dlq_spawns_one_wake(self):
        mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "please review",
                     topic="TASK-7", session_id=self.sid)
        self.conn.execute("UPDATE inbox SET status='dlq' WHERE id=?", (mid,))
        self.conn.commit()
        R.requeue_dlq(self.conn, mid)
        self.assertEqual(self.spawned, self.expect())

    def test_requeue_dlq_of_a_non_dlq_row_spawns_none(self):
        mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "please review",
                     topic="TASK-7", session_id=self.sid)
        R.requeue_dlq(self.conn, mid)
        self.assertEqual(self.spawned, [])

    def test_release_of_a_status_mail_spawns_none(self):
        mid = self.held(json.dumps({"kind": "status", "text": "fyi"}))
        R.release(self.conn, mid, "tev1")
        self.assertEqual(R.get_message(self.conn, mid)["status"], "pending")
        self.assertEqual(self.spawned, [])

    def test_unquarantine_of_a_non_quarantined_row_raises_and_spawns_none(self):
        mid = self.held()
        with self.assertRaises(ValueError):
            R.unquarantine(self.conn, mid, "tev2")
        self.assertEqual(self.spawned, [])

    def test_release_of_a_non_held_row_raises_and_spawns_none(self):
        mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "x",
                     topic="TASK-7", session_id=self.sid)
        with self.assertRaises(ValueError):
            R.release(self.conn, mid, "tev1")
        self.assertEqual(self.spawned, [])

    def test_a_spawn_that_raises_changes_nothing(self):
        def boom(args, log_root=None):
            raise OSError("no fork")
        RW.spawn = boom
        mid = self.held()
        R.release(self.conn, mid, "tev1")
        r = R.get_message(self.conn, mid)
        self.assertEqual((r["status"], r["verdict"], r["decided_by"]), ("pending", "release", "tev1"))


if __name__ == "__main__":
    unittest.main()
