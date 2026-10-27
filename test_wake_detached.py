#!/usr/bin/env python3
"""Rookery event wake, T010: SC-001, SC-005, SC-006 end to end. No stand-in: a real `rookery send`
subprocess, a real detached `rookery wake` child, the fake herdr from fixtures/bin.

    python3 -m unittest test_wake_detached -v

SC-006 is timed on real `rookery send` subprocesses (wake on vs ROOKERY_WAKE=off), medians of 20.
"""
import json
import os
import statistics
import subprocess
import sys
import time
import unittest

import turn_block as TB
from test_wake import WakeBase, HERE

CLI = os.path.join(HERE, "rookery_cli.py")


class DetachedWake(WakeBase):
    def send_env(self, **extra):
        env = dict(os.environ, ROOKERY_SESSION="alpha", ROOKERY_NODE=self.me["agent_id"])
        env.update(extra)
        return env

    _n = 0

    def send(self, text="please review", **extra):
        DetachedWake._n += 1    # a task of its own per mail: the loop breaker counts mails per task
        TB.reset_loop_counts(self.conn, self.me["agent_id"])   # a human prompt, so 40+ sends stay under the pair limit
        t0 = time.monotonic()
        out = subprocess.run([sys.executable, CLI, "send", "law-counsel", text, "--task", "T-%d" % DetachedWake._n, "--json"],
                             capture_output=True, text=True, cwd=HERE, timeout=30,
                             env=self.send_env(**extra))
        dt = time.monotonic() - t0
        self.assertEqual(out.returncode, 0, out.stderr)
        return dt

    def wait_for(self, pred, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.05)
        return pred()

    def settle(self, quiet=2.0, timeout=30):
        """Wait until the push record and the postmaster log stop changing for `quiet` seconds."""
        def sig():
            return (len(self.calls()), len(self.probes()), len(self.log_lines()))
        last, since, end = sig(), time.monotonic(), time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.1)
            cur = sig()
            if cur != last:
                last, since = cur, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return True
        return False

    def tearDown(self):
        self.settle()   # children must be gone before the temp dir is
        super().tearDown()

    def pushed_ids(self):
        try:
            with open(self.pushed(self.lc)) as fh:
                return json.load(fh).get("pushed_ids") or []
        except (OSError, ValueError):
            return []

    def test_sc001_one_push_within_5s(self):
        t0 = time.monotonic()
        self.send()
        ok = self.wait_for(lambda: len(self.calls()) >= 1 and
                           any(x.get("outcome") == "pushed" for x in self.log_lines()), 5.0)
        took = time.monotonic() - t0
        print("\nSC-001 time to push: %.2fs" % took)
        self.assertTrue(ok, "no push + pushed log within 5s: %r %r" % (self.calls(), self.log_lines()))
        self.settle()
        self.assertEqual(len(self.calls()), 1, self.calls())

    def test_sc005_ten_asks_no_double_announce(self):
        t0 = time.monotonic()
        for i in range(10):
            self.send("ask %d" % i)
        print("\nSC-005 10 sends took %.2fs" % (time.monotonic() - t0))
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM inbox").fetchall()]
        self.assertEqual(len(ids), 10)
        self.assertTrue(self.wait_for(lambda: len(self.calls()) >= 1, 5.0))
        self.assertTrue(self.settle(quiet=2.0, timeout=30))
        n = len(self.calls())
        print("SC-005 pushes: %d" % n)
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, 10)
        got = self.pushed_ids()
        self.assertEqual(len(got), len(set(got)), "an id was announced twice: %r" % got)
        self.assertEqual(sorted(got), sorted(ids), "not every mail was announced")

    def test_sc006_send_cost_of_wake_at_most_50ms(self):
        self.send("warm up", ROOKERY_WAKE="off")
        on, off = [], []
        for i in range(20):     # interleaved so load drift hits both alike
            off.append(self.send("off %d" % i, ROOKERY_WAKE="off"))
            on.append(self.send("on %d" % i))
        m_on, m_off = statistics.median(on), statistics.median(off)
        print("\nSC-006 median on %.3fs off %.3fs diff %.1f ms" % (m_on, m_off, (m_on - m_off) * 1000))
        self.assertLessEqual(m_on - m_off, 0.05)

    def test_switch_off_spawns_no_wake(self):
        self.send(ROOKERY_WAKE="off")
        time.sleep(2.0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.probes(), [])


if __name__ == "__main__":
    unittest.main()
