"""test_join_rename — `rookery up` became `rookery join` (spec 003 D1, FR-001, T007)."""
import os
import socket
import subprocess
import sys
import tempfile
import unittest

import bootstrap

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class JoinRename(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        self.env.update(ROOKERY_DB=os.path.join(self.tmp.name, "j.db"), HOME=self.tmp.name,
                        ROOKERY_ROOT=os.path.join(self.tmp.name, "root"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI, *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_join_help_lists_options(self):
        r = self.run_cli("join", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--node-id", r.stdout)
        self.assertIn("--insecure-tls", r.stdout)

    def test_top_help_lists_join(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("join", r.stdout)

    def test_join_closed_port_fails_like_old_up(self):
        url = f"http://127.0.0.1:{_closed_port()}"
        r = self.run_cli("join", url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn(f"Failed to reach mailroom at {url}", r.stderr + r.stdout)

    def test_bootstrap_modules(self):
        self.assertIn("cli_join.py", bootstrap._PY_MODULES)
        self.assertNotIn("cli_up.py", bootstrap._PY_MODULES)


if __name__ == "__main__":
    unittest.main()
