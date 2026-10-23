#!/usr/bin/env python3
"""Mail gate, T015: the 3 tries and the busy case on the `send_mail.py` path.

    python3 -m unittest test_paths_send_mail -v
"""
import os
import sqlite3
import subprocess
import sys
import unittest

from gate_matrix import SendMatrix
from test_hooks import Base, HERE


class SendMailPath(SendMatrix, Base):
    def run_script(self, to, env=None, *extra):
        return subprocess.run([sys.executable, os.path.join(HERE, "send_mail.py"), "--to", to,
                               "--body", "please review", *extra],
                              capture_output=True, text=True, env=env or self.env(), cwd=HERE,
                              timeout=30)

    def send(self, to):
        out = self.run_script(to)
        self.assertIn(out.returncode, (0, 1), out.stderr)

    def test_exit_codes_and_output(self):
        ok = self.run_script(self.PEER)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.run_script("nobody")
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertIn("wrong-recipient", bad.stdout)
        self.assertIn("There is no agent named", bad.stdout)

    def test_runs_from_any_folder_without_rookery_root(self):
        cwd = os.path.join(self.tmp.name, "my proj")          # a name project_root refuses
        os.makedirs(cwd)
        env = self.env()
        env.pop("ROOKERY_ROOT")
        out = subprocess.run([sys.executable, os.path.join(HERE, "send_mail.py"), "--to", self.PEER,
                              "--body", "hi"], capture_output=True, text=True, env=env, cwd=cwd,
                             timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl")))
        self.assertIn("-> researcher", out.stdout)

    def test_bad_input_exits_2(self):
        out = self.run_script(self.PEER, None, "--from", "bad name!")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_busy_database_exits_4_and_stores_nothing(self):
        before = len(self._query("SELECT id FROM inbox"))
        holder = sqlite3.connect(self.db, timeout=1)
        holder.execute("BEGIN IMMEDIATE")
        try:
            out = self.run_script(self.PEER)
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
        self.assertEqual(len(self._query("SELECT id FROM inbox")), before)

    def test_other_failure_exits_4_with_one_line_and_no_traceback(self):
        junk = os.path.join(self.tmp.name, "junk.db")      # not a database: DatabaseError
        with open(junk, "wb") as f:
            f.write(b"not a sqlite file " * 100)
        env = self.env(ROOKERY_DB=junk)
        out = subprocess.run([sys.executable, os.path.join(HERE, "send_mail.py"), "--to", self.PEER,
                              "--body", "hi"], capture_output=True, text=True, env=env,
                             cwd=self.root, timeout=30)
        self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(len(out.stderr.strip().splitlines()), 1)


class SendMailNoSession(SendMatrix, Base):
    with_session = False

    def setUp(self):
        super().setUp()

    def send(self, to):
        out = subprocess.run([sys.executable, os.path.join(HERE, "send_mail.py"), "--to", to,
                              "--body", "please review"], capture_output=True, text=True,
                             env=self.env(session=False, node=False), cwd=HERE, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
