#!/usr/bin/env python3
"""Feature 005, T016: the session sweep runs one screening pass per round when screening is on (FR-022).

    python3 -m unittest test_wake_sweep_screen -v
"""
import json
import os
import shutil
import sys
import unittest

import postmaster as PM
import rookery_wake
import screener
from test_wake_sweep import SweepBase
from test_wake import HERE

FIXTURE = os.path.join(HERE, "fixtures", "screener", "fake_t1.json")


class SweepScreen(SweepBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("ROOKERY_WAKE", None)
        self.fake = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake)
        os.environ["ROOKERY_SCREENER_FAKE"] = self.fake
        self.spawned = []
        self._spawn = rookery_wake.spawn
        rookery_wake.spawn = lambda args, log_root=None: self.spawned.append(list(args)) or True

    def tearDown(self):
        rookery_wake.spawn = self._spawn
        super().tearDown()

    def model_calls(self):
        if not os.path.exists(self.fake + ".calls"):
            return 0
        with open(self.fake + ".calls") as fh:
            return sum(1 for x in fh if x.strip())

    def undecided(self):
        body = json.dumps({"to": self.lc["name"], "task": "TASK-7", "kind": "ask",
                           "say": "CASE-DELIVER read it", "status": "in-progress"},
                          separators=(",", ":"), sort_keys=True)
        return self.lost_mail_body(body)

    def lost_mail_body(self, body):
        import rookery as R
        os.environ["ROOKERY_WAKE"] = "off"
        try:
            return R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], body, topic="TASK-7",
                          session_id=self.sid)
        finally:
            os.environ.pop("ROOKERY_WAKE")

    def decisions(self):
        return self.conn.execute("SELECT COUNT(*) FROM screen_decisions").fetchone()[0]


class TestSweepScreen(SweepScreen):
    def test_on_screens_one_mail(self):
        self.write_config(screener={"enabled": True})
        self.undecided()
        self.assertEqual(self.sweep(1), 0)
        self.assertEqual(self.model_calls(), 1)
        self.assertEqual(self.decisions(), 1)

    def test_session_binding_turns_screening_on_for_that_session(self):
        self.conn.execute("INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) "
                          "VALUES(?,?,?,?,?,?)", (self.sid, "session", self.sid, "screener-enabled", "1", "x"))
        self.conn.commit()
        self.undecided()
        self.assertEqual(self.sweep(1), 0)
        self.assertEqual(self.model_calls(), 1)

    def test_held_for_screening_is_screened_even_when_not_enabled(self):
        import rookery as R
        os.environ["ROOKERY_WAKE"] = "off"
        try:
            body = json.dumps({"to": self.lc["name"], "task": "TASK-7", "kind": "ask",
                               "say": "CASE-DELIVER read it", "status": "in-progress"},
                              separators=(",", ":"), sort_keys=True)
            mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], body, topic="TASK-7",
                         session_id=self.sid, hold=True)
            self.conn.execute("UPDATE inbox SET reason='screening' WHERE id=?", (mid,))
            self.conn.commit()
        finally:
            os.environ.pop("ROOKERY_WAKE")
        self.assertEqual(self.sweep(1), 0)
        self.assertGreaterEqual(self.model_calls(), 1)

    def test_off_makes_no_call(self):
        self.undecided()
        self.assertEqual(self.sweep(2), 0)
        self.assertEqual(self.model_calls(), 0)
        self.assertEqual(self.decisions(), 0)

    def test_held_screen_lock_skips_round(self):
        self.write_config(screener={"enabled": True})
        self.undecided()
        lock = rookery_wake.try_lock(os.path.join(self.root, "postmaster", "lock",
                                                  f"screen-{self.sid}.lock"))
        self.assertIsNotNone(lock)
        try:
            self.assertEqual(self.sweep(1), 0)
        finally:
            lock.close()
        self.assertEqual(self.model_calls(), 0)
        self.assertTrue(self.has_log("screen-busy"), self.log_lines())

    def test_lock_released_after_round(self):
        self.write_config(screener={"enabled": True})
        self.sweep(1)
        lock = rookery_wake.try_lock(os.path.join(self.root, "postmaster", "lock",
                                                  f"screen-{self.sid}.lock"))
        self.assertIsNotNone(lock)
        lock.close()

    def test_error_is_logged_and_loop_continues(self):
        self.write_config(screener={"enabled": True})
        calls = []

        def boom(**kw):
            calls.append(kw)
            raise RuntimeError("kaput")
        real = screener.run
        screener.run = boom
        try:
            self.assertEqual(self.sweep(2), 0)
        finally:
            screener.run = real
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], {"sessions": [self.sid], "once": True})
        self.assertTrue(self.has_log("screen-step-error"), self.log_lines())
        self.assertEqual(self.sleeps, [300])


if __name__ == "__main__":
    unittest.main()
