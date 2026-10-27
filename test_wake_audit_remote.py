#!/usr/bin/env python3
"""Audit fixes for the remote check (F4 trigger during a follower sleep, F7 unsafe ROOKERY_NODE).

    python3 -m unittest test_wake_audit_remote -v
"""
import os
import unittest
from unittest import mock

import rookery_remote as RR
import rookery_wake
from test_wake_remote import Mailroom, row
from test_wake_remote_follow import FollowBase


class TestTriggerDuringSleep(FollowBase):
    def test_lock_held_spawns_one_plain_check_and_writes_follow(self):
        held = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.lock"))
        try:
            with mock.patch.object(rookery_wake, "spawn", return_value=True) as sp:
                self.assertEqual(RR.start_follow("remote1", now=self.now), "follower-running")
        finally:
            held.close()
        self.assertEqual([c.args[0] for c in sp.call_args_list], [["check"]])
        self.assertEqual(RR._read_follow("remote1"), 1000.0)

    def test_lock_free_spawns_follower_only(self):
        with mock.patch.object(rookery_wake, "spawn", return_value=True) as sp:
            self.assertEqual(RR.start_follow("remote1", now=self.now), "follower-started")
        self.assertEqual([c.args[0] for c in sp.call_args_list], [["check", "--follower"]])

    def test_second_check_while_first_holds_the_check_lock_is_busy(self):
        Mailroom.rows = [row(1)]
        held = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.check.lock"))
        try:
            r = self.check()
        finally:
            held.close()
        self.assertEqual(r["outcome"], "check-busy")
        self.assertEqual(Mailroom.seen, [])
        self.assertEqual(self.pushes(), [])
        self.assertEqual(self.check()["outcome"], "pushed")   # lock released: the next check runs


class TestUnsafeNode(FollowBase):
    BAD = "../../escaped/evil"

    def escaped(self):
        found = []
        for d, _, files in os.walk(self.tmp.name):
            for f in files:
                p = os.path.join(d, f)
                if not p.startswith(self.rdir + os.sep) and f != "herdr.record":
                    found.append(p)
        return found

    def test_start_follow_bad_node_writes_nothing(self):
        with mock.patch.object(rookery_wake, "spawn", return_value=True) as sp:
            self.assertEqual(RR.start_follow(self.BAD, now=self.now), "bad-node")
        sp.assert_not_called()
        self.assertEqual(self.escaped(), [])
        self.assertFalse(os.path.exists(self.rdir))

    def test_run_follower_bad_node(self):
        r = RR.run_follower(self.url, "t", self.BAD, self.config, sleep=self.sleep, now=self.now, env=self.env)
        self.assertEqual(r["outcome"], "bad-node")
        self.assertEqual(self.escaped(), [])
        self.assertEqual(Mailroom.seen, [])

    def test_cli_exit_2_for_unsafe_node(self):
        r = self.cli("--follow", env={"ROOKERY_URL": self.url, "ROOKERY_NODE": self.BAD})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.escaped(), [])

    def test_mcp_remote_follow_skips(self):
        import rookery_mcp
        with mock.patch.dict(os.environ, {"ROOKERY_NODE": self.BAD}), \
                mock.patch.object(RR, "start_follow") as sf:
            rookery_mcp._remote_follow()
        sf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
