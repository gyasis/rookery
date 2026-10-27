#!/usr/bin/env python3
"""Feature 005, T012: the per-session sweep (postmaster.run_session_sweep, `rookery sweep`).

    python3 -m unittest test_wake_sweep -v
"""
import json
import os
import subprocess
import sys

import rookery_identity as RI
import rookery_wake
import turn_block as TB
import postmaster as PM
from test_wake import WakeBase, HERE


class SweepBase(WakeBase):
    def sweep(self, rounds, session="alpha"):
        self.sleeps = []
        return PM.run_session_sweep(self.root, self.config, session, sleep=self.sleeps.append,
                                    rounds=rounds)

    def lost_mail(self, **kw):
        os.environ["ROOKERY_WAKE"] = "off"       # no event wake: the sweep is the safety net
        try:
            return self.mail(**kw)
        finally:
            os.environ.pop("ROOKERY_WAKE")

    def write_config(self, **kw):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        cfg = dict(self.config, **kw)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump(cfg, fh)


class TestSweep(SweepBase):
    def test_round_wakes_lost_event_once(self):
        self.lost_mail()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.sweep(1), 0)
        self.assertEqual(len(self.calls()), 1)

    def test_second_round_does_not_wake_again(self):
        self.lost_mail()
        self.assertEqual(self.sweep(2), 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.sleeps, [300])      # slept between the rounds, not after the last

    def test_closed_session_ends_loop(self):
        RI.end_session(self.conn, "alpha")
        self.assertEqual(self.sweep(5), 0)
        self.assertEqual(self.sleeps, [])
        self.assertTrue(self.has_log("sweep-end"), self.log_lines())

    def test_session_closed_between_rounds_ends(self):
        self.sleeps = []

        def sleep(s):
            self.sleeps.append(s)
            RI.end_session(self.conn, "alpha")
        self.assertEqual(PM.run_session_sweep(self.root, self.config, "alpha", sleep=sleep, rounds=9), 0)
        self.assertEqual(self.sleeps, [300])
        self.assertTrue(self.has_log("sweep-end"))

    def test_held_lock_is_sweep_busy(self):
        self.lost_mail()
        lock = rookery_wake.try_lock(os.path.join(self.root, "postmaster", "lock",
                                                  f"sweep-{self.sid}.lock"))
        self.assertIsNotNone(lock)
        try:
            self.assertEqual(self.sweep(1), 0)
        finally:
            lock.close()
        self.assertTrue(self.has_log("sweep-busy"), self.log_lines())
        self.assertEqual(self.calls(), [])

    def test_missing_session_returns_3(self):
        self.assertEqual(self.sweep(1, session="nope"), 3)

    def test_default_sleep_300(self):
        self.sweep(3)
        self.assertEqual(self.sleeps, [300, 300])
        self.assertFalse(self.has_log("poll-raised"))

    def test_low_sweep_s_raised_with_one_log(self):
        self.write_config(sweep_s=0.5)
        self.sweep(3)
        self.assertEqual(self.sleeps, [300, 300])
        n = sum(1 for x in self.log_lines() if x.get("outcome") == "poll-raised")
        self.assertEqual(n, 1)

    def test_never_wakes_another_session(self):
        beta = RI.new_session(self.conn, name="beta")
        bi = RI.new_identity(self.conn, beta["session_id"], "bob")
        RI.bind(self.conn, beta["session_id"], bi["agent_id"], "herdr-pane", "w9:p9")
        self.set_state("bob", "idle", session="beta")
        self.lost_mail(to=bi)
        self.sweep(1)
        self.assertEqual(self.calls(), [])
        self.assertEqual(PM.wake_identities(self.conn, self.root, self.config,
                                            session_id=beta["session_id"]), 1)

    def test_cli_subprocess_one_round(self):
        self.lost_mail()
        env = dict(os.environ, ROOKERY_SWEEP_ROUNDS="1", PYTHONPATH=HERE)
        r = subprocess.run([sys.executable, os.path.join(HERE, "rookery_cli.py"), "sweep",
                            "--session", "alpha"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_cli_missing_session_exit_3(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "rookery_cli.py"), "sweep",
                            "--session", "nope"], env=dict(os.environ), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 3, r.stderr)


class TestIdentityPoll(SweepBase):
    def test_poll_decision(self):
        self.assertEqual(PM._identity_poll(0.5), (300, True))
        self.assertEqual(PM._identity_poll(None), (300, False))
        self.assertEqual(PM._identity_poll(300), (300, False))
        self.assertEqual(PM._identity_poll(900), (900, False))


if __name__ == "__main__":
    import unittest
    unittest.main()


class SweepOutbox(WakeBase):
    """The outbox drop-box rides the session sweep (005 wave 8 audit, A1)."""
    def test_one_round_delivers_a_dropped_file(self):
        import time as _t
        box = os.path.join(TB.workspace(self.root, "alpha", "me"), "outbox")
        os.makedirs(box, exist_ok=True)
        path = os.path.join(box, "drop.json")
        from test_outbox import fixture_block          # a valid ask from me to law-counsel, task TASK-12
        with open(path, "w") as fh:
            fh.write(fixture_block())
        old = _t.time() - 10
        os.utime(path, (old, old))           # older than the 2 s freshness rule
        PM.run_session_sweep(self.root, self.config, "alpha", sleep=lambda s: None, rounds=1)
        self.assertFalse(os.path.exists(path))
        n = self.conn.execute("SELECT COUNT(*) FROM inbox WHERE recipient=? AND topic=?",
                              (self.lc["agent_id"], "TASK-12")).fetchone()[0]
        self.assertEqual(n, 1)

    def test_sigterm_style_interrupt_is_a_clean_stop(self):
        def boom(_s):
            raise KeyboardInterrupt
        self.assertEqual(PM.run_session_sweep(self.root, self.config, "alpha", sleep=boom, rounds=2), 0)
        self.assertIsNotNone(rookery_wake.try_lock(os.path.join(self.root, "postmaster", "lock",
                                                     "sweep-%s.lock" % self.sid)))
