#!/usr/bin/env python3
"""Feature 005, T013: cost proofs (SC-003, SC-004, FR-016).

    python3 -m unittest test_wake_cost -v

Model calls are counted through the fake screener backend: ROOKERY_SCREENER_FAKE=<file> makes every
decision append one line to <file>.calls. No file means no call. `spawn` is a recorder.
"""
import json
import os
import shutil
import unittest

import mail_gate as G
import postmaster as PM
import rookery_identity as RI
import rookery_wake as W
from test_wake import WakeBase, HERE, BIND

FIXTURE = os.path.join(HERE, "fixtures", "screener", "fake_t1.json")


class CostBase(WakeBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("ROOKERY_WAKE", None)          # the event wake must be ON here
        self.spawned = []
        self._spawn = W.spawn
        W.spawn = lambda args, log_root=None: self.spawned.append(list(args)) or True

    def tearDown(self):
        W.spawn = self._spawn
        super().tearDown()

    def model_calls(self):
        path = os.environ.get("ROOKERY_SCREENER_FAKE")
        if not path or not os.path.exists(path + ".calls"):
            return 0
        with open(path + ".calls") as fh:
            return sum(1 for x in fh if x.strip())


class TestIdleHour(CostBase):
    def test_idle_hour_is_cheap(self):
        fake = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, fake)
        os.environ["ROOKERY_SCREENER_FAKE"] = fake
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump(dict(self.config, screener={"enabled": True}), fh)

        clock = [0.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            clock[0] += s
            if clock[0] >= 3600:
                raise KeyboardInterrupt            # the hour is over: a clean stop

        rc = PM.run_session_sweep(self.root, self.config, "alpha", sleep=sleep, now=lambda: clock[0])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(clock[0], 3600)
        self.assertLessEqual(len(sleeps), 12)       # at most 12 rounds in one hour (SC-003)
        self.assertTrue(all(s >= 300 for s in sleeps), sleeps)
        self.assertEqual(self.model_calls(), 0)      # no mail, no model call (FR-016)
        self.assertEqual(self.spawned, [])           # no mail, no wake
        self.assertEqual(self.calls(), [])           # no pane command either


class TestRing(CostBase):
    def ring(self, n):
        ids = []
        for i in range(n):
            ident = RI.new_identity(self.conn, self.sid, f"r{n}x{i}")
            RI.bind(self.conn, self.sid, ident["agent_id"], BIND[0], f"w{n}:p{i}")
            self.set_state(f"r{n}x{i}", "idle")
            ids.append(ident)
        for i, ident in enumerate(ids):
            res = G.submit(self.conn, path="send", principal=(self.sess, ident), raw_sender="human",
                           to=f"r{n}x{(i + 1) % n}", body="please review", topic=f"TASK-{n}-{i}",
                           root=self.root)
            self.assertEqual(res["outcome"], "sent", res)
        return ids

    def test_wakes_are_linear_in_mail(self):
        for n in (2, 5, 10):
            with self.subTest(agents=n):
                self.spawned.clear()
                ids = self.ring(n)
                self.assertEqual(len(self.spawned), n, self.spawned)     # n asks, n wakes (SC-004), not n*n
                self.assertEqual(sorted(c[-1] for c in self.spawned),
                                 sorted(i["agent_id"] for i in ids))     # one wake per recipient


if __name__ == "__main__":
    unittest.main()
