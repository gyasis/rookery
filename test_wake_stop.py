#!/usr/bin/env python3
"""Rookery event wake, T011: the Stop hook wakes the stopping agent for mail that arrived in its turn.

    python3 -m unittest test_wake_stop -v

Unit cases run cli_hook.stop in-process with rookery_wake.spawn replaced by a recorder.
One case (SC-002) runs the hook as a subprocess with real detached children and the fake herdr.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import types
import unittest
import unittest.mock as mock

import cli_hook
import rookery_wake
import turn_block as TB
from test_wake import WakeBase, HERE

CLI = os.path.join(HERE, "rookery_cli.py")
PAYLOAD = {"last_assistant_message": "Done, no block here."}


class StopWake(WakeBase):
    def setUp(self):
        super().setUp()
        os.environ["ROOKERY_SESSION"] = "alpha"
        os.environ["ROOKERY_NODE"] = self.lc["agent_id"]

    def run_stop(self, spawn):
        out = io.StringIO()
        with mock.patch.object(rookery_wake, "spawn", spawn), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(PAYLOAD))), \
                contextlib.redirect_stdout(out):
            cli_hook.stop(types.SimpleNamespace(project="p"))
        self.assertEqual(out.getvalue(), "")
        return out.getvalue()

    def spawns(self, **env):
        rec = []
        os.environ.update(env)
        self.run_stop(lambda args, log_root=None: rec.append(args) or True)
        return rec

    def test_unread_ask_spawns_one_wake(self):
        self.mail()
        self.assertEqual(self.spawns(), [["wake", "--session", self.sid, "--agent", self.lc["agent_id"]]])

    def test_announced_ask_spawns_none(self):
        mid = self.mail()
        p = self.pushed(self.lc)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump({"pushed_ids": [mid]}, fh)
        self.assertEqual(self.spawns(), [])

    def test_no_pending_mail_spawns_none(self):
        self.assertEqual(self.spawns(), [])

    def test_status_mail_spawns_none(self):
        R = __import__("rookery")
        R.send(self.conn, self.me["agent_id"], self.lc["agent_id"],
               json.dumps({"to": "law-counsel", "kind": "status", "task": "T-1", "say": "fyi"}),
               topic="T-1", session_id=self.sid)
        self.assertEqual(self.spawns(), [])

    def test_raising_spawn_is_silent(self):
        self.mail()

        def boom(args, log_root=None):
            raise RuntimeError("no")
        self.run_stop(boom)     # asserts empty stdout, and no exception escapes

    def test_remote_url_spawns_no_local_wake(self):
        self.mail()
        self.assertEqual(self.spawns(ROOKERY_URL="http://127.0.0.1:1"), [])

    def test_switch_off_spawns_none(self):
        self.mail()
        self.assertEqual(self.spawns(ROOKERY_WAKE="off"), [])


class StopWakeEndToEnd(WakeBase):
    def settle(self, quiet=2.0, timeout=30):
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

    def test_sc002_stop_hook_pushes_unread_ask_within_5s(self):
        self.mail()
        env = dict(os.environ, ROOKERY_SESSION="alpha", ROOKERY_NODE=self.lc["agent_id"])
        t0 = time.monotonic()
        out = subprocess.run([sys.executable, CLI, "hook", "stop", "--project", "p"],
                             input=json.dumps(PAYLOAD), capture_output=True, text=True,
                             env=env, cwd=HERE, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        end = time.monotonic() + 5.0
        while time.monotonic() < end and not self.calls():
            time.sleep(0.05)
        print("\nSC-002 stop hook to push: %.2fs" % (time.monotonic() - t0))
        self.assertTrue(self.calls(), "no push within 5s: %r" % self.log_lines())
        self.settle()
        self.assertEqual(len(self.calls()), 1, self.calls())


if __name__ == "__main__":
    unittest.main()
