#!/usr/bin/env python3
"""Feature 005 T015: the gate spawns one screening pass after the store when screening is on.

    python3 -m unittest test_screen_event_gate -v
"""
import json
import os
import unittest

import rookery_wake as W
from test_gate import GateBase


class ScreenEvent(GateBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("ROOKERY_WAKE", None)   # GateBase turns the wake off; this class tests the spawn
        self.calls = []
        self.boom = False
        self._spawn = W.spawn

        def rec(args, log_root=None):
            self.calls.append(list(args))
            if self.boom:
                raise OSError("no spawn")
            return True
        W.spawn = rec
        self.sid = self.alpha["session_id"]

    def tearDown(self):
        W.spawn = self._spawn
        super().tearDown()

    def screens(self):
        return [c for c in self.calls if c[0] == "screen"]

    def config(self, text):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            fh.write(text)

    def enable(self):
        self.config(json.dumps({"screener": {"enabled": True}}))

    def test_off_by_default_spawns_no_screening(self):
        self.assertEqual(self.submit()["outcome"], "sent")
        self.assertEqual(self.screens(), [])

    def test_enabled_spawns_one(self):
        self.enable()
        self.assertEqual(self.submit()["outcome"], "sent")
        self.assertEqual(self.screens(), [["screen", "run", "--session", self.sid, "--once"]])

    def test_session_binding_spawns_for_that_session_only(self):
        self.conn.execute("INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) VALUES(?,?,?,?,?,?)",
                          (self.sid, "session", self.sid, "screener-enabled", "1", "now"))
        self.conn.commit()
        self.submit()
        self.assertEqual(self.screens(), [["screen", "run", "--session", self.sid, "--once"]])
        self.calls.clear()
        self.submit(principal=(self.beta, self.beta_me), to="beta-only")
        self.assertEqual(self.screens(), [])

    def test_mail_held_for_screening_spawns_one(self):
        self.enable()
        r = self.submit(pre_store=lambda rec: ("held", "screening"))
        row = self.conn.execute("SELECT status, reason FROM inbox WHERE id=?", (r["msg_id"],)).fetchone()
        self.assertEqual(tuple(row), ("held", "screening"))
        self.assertEqual(len(self.screens()), 1)

    def test_held_for_screening_spawns_even_when_not_enabled(self):
        # t1 live holds the mail; with screener.enabled unset nothing else would ever screen it
        r = self.submit(pre_store=lambda rec: ("held", "screening"))
        self.assertEqual(self.conn.execute("SELECT status FROM inbox WHERE id=?", (r["msg_id"],)).fetchone()[0],
                         "held")
        self.assertEqual(len(self.screens()), 1)

    def test_refused_send_spawns_none(self):
        self.enable()
        self.assertEqual(self.submit(to="nobody")["reason"], "wrong-recipient")
        self.assertEqual(self.screens(), [])

    def test_kill_switch_spawns_none(self):
        self.enable()
        os.environ["ROOKERY_WAKE"] = "off"
        self.submit()
        self.assertEqual(self.calls, [])

    def test_a_raising_spawn_keeps_the_result(self):
        self.enable()
        self.boom = True
        r = self.submit()
        self.assertEqual((r["outcome"], r["reason"]), ("sent", None))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM inbox WHERE id=?", (r["msg_id"],)).fetchone())

    def test_bad_config_spawns_none_and_sends(self):
        self.config("{not json")
        r = self.submit()
        self.assertEqual(r["outcome"], "sent", r)
        self.assertEqual(self.screens(), [])


if __name__ == "__main__":
    unittest.main()
