#!/usr/bin/env python3
"""Rookery remote follower, T020 (research R8): `rookery check --follow` and the bounded follower.

    python3 -m unittest test_wake_remote_follow -v
"""
import json
import os
import unittest
from unittest import mock

import rookery_remote as RR
import rookery_wake
from test_wake_remote import Mailroom, RemoteBase, row, TOKEN


class FollowBase(RemoteBase):
    def setUp(self):
        super().setUp()
        self.t = [1000.0]
        self.sleeps = []
        self.on_sleep = None
        self.rdir = os.path.join(self.tmp.name, ".rookery", "remote")

    def now(self):
        return self.t[0]

    def sleep(self, s):
        self.sleeps.append(s)
        self.t[0] += s
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))

    def follow(self, **kw):
        return RR.run_follower(self.url, TOKEN, "remote1", self.config, sleep=self.sleep, now=self.now,
                               env=self.env, **kw)

    def write_follow(self, start):
        os.makedirs(self.rdir, exist_ok=True)
        with open(os.path.join(self.rdir, "remote1.follow"), "w") as fh:
            json.dump({"start": start}, fh)

    def lock_free(self):
        fh = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.lock"))
        if fh is None:
            return False
        fh.close()
        return True


class TestFollower(FollowBase):
    def test_no_mail_five_checks_four_sleeps(self):
        r = self.follow()
        self.assertEqual(r["checks"], 5)
        self.assertEqual(self.sleeps, [300] * 4)
        self.assertEqual(len(Mailroom.seen), 5)
        self.assertTrue(self.lock_free())

    def test_ask_on_third_check_pushes_once_and_stops(self):
        def appear(n):
            if n == 2:
                Mailroom.rows = [row(1)]
        self.on_sleep = appear
        r = self.follow()
        self.assertEqual((r["checks"], r["outcome"]), (3, "pushed"))
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(self.sleeps, [300, 300])

    def test_new_trigger_restarts_the_count(self):
        def retrigger(n):
            if n == 4:
                self.write_follow(self.t[0] + 1)
        self.write_follow(1000.0)
        self.on_sleep = retrigger
        r = self.follow()
        self.assertEqual(r["checks"], 9)

    def test_trigger_during_the_last_check_is_not_lost(self):
        # a new turn writes its trigger while the follower runs its 5th (last) check: --follow saw the
        # lock held, so this follower must pick the trigger up after it releases the lock
        self.write_follow(1000.0)
        real = RR.check_once
        calls = []

        def spy(*a, **k):
            calls.append(1)
            if len(calls) == 5:
                self.write_follow(5000.0)
            return real(*a, **k)
        RR.check_once = spy
        try:
            r = self.follow()
        finally:
            RR.check_once = real
        self.assertEqual(r["checks"], 10)
        self.assertTrue(self.lock_free())

    def test_lock_held_exits_quietly(self):
        held = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.lock"))
        try:
            r = self.follow()
        finally:
            held.close()
        self.assertEqual((r["outcome"], r["checks"]), ("follower-running", 0))
        self.assertEqual(Mailroom.seen, [])

    def test_keyboard_interrupt_releases_lock(self):
        def boom(n):
            raise KeyboardInterrupt
        self.on_sleep = boom
        r = self.follow()
        self.assertEqual(r["checks"], 1)
        self.assertTrue(self.lock_free())


class TestFollowCommand(FollowBase):
    def test_follow_with_lock_free_spawns_once(self):
        with mock.patch.object(rookery_wake, "spawn", return_value=True) as sp:
            self.assertEqual(RR.start_follow("remote1", now=self.now), "follower-started")
        sp.assert_called_once_with(["check", "--follower"])
        with open(os.path.join(self.rdir, "remote1.follow")) as fh:
            self.assertEqual(json.load(fh), {"start": 1000.0})
        self.assertTrue(self.lock_free())

    def test_follow_with_lock_held_spawns_one_check_and_updates_start(self):
        self.write_follow(1.0)
        held = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.lock"))
        try:
            with mock.patch.object(rookery_wake, "spawn", return_value=True) as sp:
                self.assertEqual(RR.start_follow("remote1", now=self.now), "follower-running")
        finally:
            held.close()
        sp.assert_called_once_with(["check"])
        with open(os.path.join(self.rdir, "remote1.follow")) as fh:
            self.assertEqual(json.load(fh), {"start": 1000.0})

    def test_cli_exits_2_without_url(self):
        r = self.cli("--follow")
        self.assertEqual(r.returncode, 2)

    def test_cli_follow_json_exit_0(self):
        # a held lock keeps the CLI from spawning a real follower
        held = rookery_wake.try_lock(os.path.join(self.rdir, "remote1.lock"))
        try:
            r = self.cli("--follow", "--json", env={"ROOKERY_URL": self.url, "ROOKERY_NODE": "remote1",
                                                    "ROOKERY_WAKE": "off"})   # no real one-shot check child
        finally:
            held.close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"node": "remote1", "outcome": "follower-running"})


if __name__ == "__main__":
    unittest.main()
