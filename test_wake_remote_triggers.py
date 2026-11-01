#!/usr/bin/env python3
"""Rookery remote wake, T021: the Stop hook starts the bounded follower.

    python3 -m unittest test_wake_remote_triggers -v

Seam: rookery_remote.start_follow is replaced by a recorder; rookery_wake.spawn by a recorder too.
"""
import os
import unittest
import unittest.mock as mock

import rookery_remote
import test_wake_stop


class StopTrigger(test_wake_stop.StopWake):
    """Inherited StopWake cases are skipped here; test_wake_stop runs them."""

    def run_with(self, boom=False, **env):
        os.environ.update(env)
        follows, spawns = [], []

        def fake(node, now=None):
            follows.append(node)
            if boom:
                raise RuntimeError("boom")
            return "follower-started"
        with mock.patch.object(rookery_remote, "start_follow", fake):
            self.run_stop(lambda args, log_root=None: spawns.append(args) or True)
        return follows, spawns

    def test_remote_calls_start_follow_once_and_no_local_wake(self):
        self.mail()
        follows, spawns = self.run_with(ROOKERY_URL="http://127.0.0.1:1")
        self.assertEqual(follows, [self.lc["agent_id"]])
        self.assertFalse([s for s in spawns if s and s[0] == "wake"])

    def test_without_url_no_follow(self):
        self.mail()
        follows, _ = self.run_with()
        self.assertEqual(follows, [])

    def test_wake_off_no_follow(self):
        follows, _ = self.run_with(ROOKERY_URL="http://127.0.0.1:1", ROOKERY_WAKE="off")
        self.assertEqual(follows, [])

    def test_start_follow_raising_is_silent(self):
        follows, _ = self.run_with(boom=True, ROOKERY_URL="http://127.0.0.1:1")
        self.assertEqual(len(follows), 1)       # run_stop asserts stdout is empty


if __name__ == "__main__":
    unittest.main()
