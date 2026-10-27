#!/usr/bin/env python3
"""Audit fixes F2 (wake --root) and F3 (remote Stop trigger on a joined machine).

    python3 -m unittest test_wake_audit_root -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import rookery_identity as RI
import rookery_wake
from test_wake import WakeBase, HERE

CLI = os.path.join(HERE, "rookery_cli.py")


class WakeRoot(WakeBase):
    def child_env(self):
        env = dict(os.environ)
        env.pop("ROOKERY_ROOT", None)
        return env

    def test_wake_with_root_pushes_without_binding_or_env(self):
        self.mail()
        out = subprocess.run([sys.executable, CLI, "wake", "--session", self.sid, "--agent",
                              self.lc["agent_id"], "--root", self.root, "--json"],
                             capture_output=True, text=True, env=self.child_env(), cwd=HERE, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["outcome"], "pushed", out.stdout)
        self.assertTrue(self.calls())

    def test_wake_without_root_is_no_root(self):
        self.mail()
        out = subprocess.run([sys.executable, CLI, "wake", "--session", self.sid, "--agent",
                              self.lc["agent_id"], "--json"],
                             capture_output=True, text=True, env=self.child_env(), cwd=HERE, timeout=30)
        self.assertEqual(json.loads(out.stdout)["outcome"], "no-root", out.stdout)

    def spawned(self, log_root):
        rec = []
        self.mail()
        orig = rookery_wake.spawn
        rookery_wake.spawn = lambda args, log_root=None: rec.append(args) or True
        try:
            rookery_wake.after_pending(self.conn, [r["id"] for r in
                                       self.conn.execute("SELECT id FROM inbox").fetchall()], log_root=log_root)
        finally:
            rookery_wake.spawn = orig
        return rec

    def test_after_pending_adds_root_when_unbound(self):
        os.environ.pop("ROOKERY_ROOT")
        rec = self.spawned(self.root)
        self.assertEqual(rec, [["wake", "--session", self.sid, "--agent", self.lc["agent_id"],
                                "--root", self.root]])

    def test_after_pending_unchanged_when_env_root_set(self):
        rec = self.spawned(self.root)
        self.assertEqual(rec, [["wake", "--session", self.sid, "--agent", self.lc["agent_id"]]])

    def test_after_pending_unchanged_when_binding_present(self):
        os.environ.pop("ROOKERY_ROOT")
        self.conn.execute(
            "INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) "
            "VALUES(?, 'session', ?, 'rookery-root', ?, '2026-01-01T00:00:00Z')",
            (self.sid, self.sid, self.root))
        self.conn.commit()
        rec = self.spawned(self.root)
        self.assertEqual(rec, [["wake", "--session", self.sid, "--agent", self.lc["agent_id"]]])


class RemoteStopJoined(unittest.TestCase):
    def test_stop_arms_remote_trigger_without_local_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("ROOKERY_", "HERDR_"))}
            env.update(HOME=tmp, ROOKERY_DB=os.path.join(tmp, "missing.db"),
                       ROOKERY_URL="http://127.0.0.1:1", ROOKERY_NODE="joined-node")
            out = subprocess.run([sys.executable, CLI, "hook", "stop", "--project", "p"],
                                 input="{}", capture_output=True, text=True, env=env, cwd=HERE, timeout=30)
            self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
            self.assertTrue(os.path.exists(os.path.join(tmp, ".rookery", "remote", "joined-node.follow")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "missing.db")))


if __name__ == "__main__":
    unittest.main()
