#!/usr/bin/env python3
"""rookery wake under a per-agent lock (feature 005, T005): run_wake rounds and the CLI.

    python3 -m unittest test_wake_lock -v
"""
import json
import os
import subprocess
import sys
import unittest

import rookery as R
import rookery_wake as W
import turn_block as TB
from test_wake import HERE, WakeBase


def pushes(path):
    try:
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []


def log_lines(root, outcome):
    try:
        with open(os.path.join(root, "postmaster", "postmaster.jsonl")) as fh:
            return [ln for ln in fh if json.loads(ln).get("outcome") == outcome]
    except OSError:
        return []


class RunWakeTest(WakeBase):
    def wake(self, **kw):
        return W.run_wake(self.conn, self.root, self.config, self.sid, self.lc["agent_id"], **kw)

    def stand_in(self, every_call):
        calls = []

        def run(*a, **k):
            if every_call or not calls:
                self.mail()
            calls.append(1)
            return subprocess.run(*a, **k)
        return run, calls

    def test_one_wake_pushes_once(self):
        self.mail()
        r = self.wake()
        self.assertEqual((r["outcome"], r["rounds"], r["agent_id"]), ("pushed", 1, self.lc["agent_id"]))
        self.assertEqual(len(pushes(self.record)), 1)

    def test_held_lock_gives_wake_busy(self):
        self.mail()
        fh = W.try_lock(os.path.join(self.root, "postmaster", "lock", self.lc["agent_id"] + ".lock"))
        try:
            r = self.wake()
        finally:
            fh.close()
        self.assertEqual((r["outcome"], r["rounds"]), ("wake-busy", 0))
        self.assertEqual(pushes(self.record), [])
        self.assertEqual(len(log_lines(self.root, "wake-busy")), 1)

    def test_mail_stored_during_push_gets_round_two(self):
        self.mail()
        run, _ = self.stand_in(False)
        r = self.wake(run=run)
        self.assertEqual(len(pushes(self.record)), 2)
        self.assertGreaterEqual(r["rounds"], 2)

    def test_loser_gets_busy_and_holder_pushes_its_mail(self):
        # guarantee (b): a second wake that finds the lock held exits wake-busy, and the holder's recheck
        # after it releases the lock pushes the mail that the second wake left behind
        self.mail()
        inner = []

        def run(*a, **k):
            if not inner:
                inner.append(self.wake())          # the loser, while the holder runs
                self.mail()                        # the loser's mail
            return subprocess.run(*a, **k)
        r = self.wake(run=run)
        self.assertEqual(inner[0]["outcome"], "wake-busy")
        self.assertEqual((r["outcome"], r["rounds"]), ("pushed", 2))
        self.assertEqual(len(pushes(self.record)), 2)
        self.assertEqual(len(log_lines(self.root, "wake-busy")), 1)

    def test_newer_mail_after_a_stop_outcome_gets_one_more_round(self):
        # the holder stops on wake-failed; a mail that arrived during that round still gets a round
        self.mail()
        state = []

        def run(*a, **k):
            if not state:
                state.append(1)
                self.mail()
                return subprocess.CompletedProcess(a, 1, b"", b"")
            return subprocess.run(*a, **k)
        r = self.wake(run=run)
        self.assertEqual((r["outcome"], r["rounds"]), ("pushed", 2))

    def test_broken_lock_folder_is_lock_failed_not_busy(self):
        self.mail()
        os.makedirs(os.path.join(self.root, "postmaster"), exist_ok=True)
        with open(os.path.join(self.root, "postmaster", "lock"), "w") as fh:
            fh.write("a file where the folder should be")
        r = self.wake()
        self.assertEqual(r["outcome"], "lock-failed")
        self.assertEqual(log_lines(self.root, "wake-busy"), [])
        self.assertEqual(len(log_lines(self.root, "lock-failed")), 1)

    def test_loop_stops_at_three_rounds(self):
        self.mail()
        run, _ = self.stand_in(True)
        r = self.wake(run=run)
        self.assertTrue(r["capped"])
        self.assertEqual(r["rounds"], 3)
        self.assertEqual(len(pushes(self.record)), 3)

    def test_wake_failed_stops_with_one_log_line(self):
        self.mail()

        def run(*a, **k):
            return subprocess.CompletedProcess(a, 1, b"", b"")
        r = self.wake(run=run)
        self.assertEqual((r["outcome"], r["rounds"]), ("wake-failed", 1))
        self.assertEqual(len(log_lines(self.root, "wake-failed")), 1)

    def test_no_root(self):
        r = W.run_wake(self.conn, None, self.config, self.sid, self.lc["agent_id"])
        self.assertEqual((r["outcome"], r["rounds"]), ("no-root", 0))

    def test_closed_session_is_nothing(self):
        import rookery_identity as RI
        self.mail()
        RI.end_session(self.conn, self.sid)
        self.assertEqual(self.wake()["outcome"], "nothing")
        self.assertEqual(pushes(self.record), [])


class WakeCliTest(WakeBase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "rookery_cli.py", "wake", *args], cwd=HERE,
                              capture_output=True, text=True, env=dict(os.environ), timeout=30)

    def test_json_and_exit_zero(self):
        self.mail()
        p = self.cli("--session", "alpha", "--agent", "law-counsel", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(set(d), {"session", "agent_id", "outcome", "mail_id", "rounds", "capped"})
        self.assertEqual(d["agent_id"], self.lc["agent_id"])
        self.assertEqual(len(pushes(self.record)), 1)

    def test_text_line(self):
        p = self.cli("--session", "alpha", "--agent", "law-counsel")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "wake law-counsel: nothing"))

    def test_unknown_session(self):
        self.assertEqual(self.cli("--session", "nope", "--agent", "law-counsel").returncode, 3)

    def test_unknown_agent(self):
        self.assertEqual(self.cli("--session", "alpha", "--agent", "nobody").returncode, 3)

    def test_bad_config_is_one_line_exit_1(self):
        os.makedirs(os.path.join(os.environ["HOME"], ".rookery"), exist_ok=True)
        with open(os.path.join(os.environ["HOME"], ".rookery", "config.json"), "w") as fh:
            fh.write("{not json")
        p = self.cli("--session", "alpha", "--agent", "law-counsel")
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Traceback", p.stderr)

    def test_bad_name(self):
        self.assertEqual(self.cli("--session", "alpha", "--agent", "../x").returncode, 2)


if __name__ == "__main__":
    unittest.main()
