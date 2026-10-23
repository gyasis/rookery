#!/usr/bin/env python3
"""Mail gate, T025a: exit codes and printed errors of mailctl.py against a live mailroom server.

    python3 -m unittest test_mailctl -v
"""
import os
import subprocess
import sys
import unittest

from test_hooks import Base
from test_paths_mailroom import ServerMixin, TOKEN

HERE = os.path.dirname(os.path.abspath(__file__))


class MailctlCli(ServerMixin, Base):
    def run_ctl(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "ROOKERY_TOKEN"}
        return subprocess.run([sys.executable, os.path.join(HERE, "mailctl.py"), *args],
                              capture_output=True, text=True, timeout=30, env=env)

    def send(self, sender, token=TOKEN):
        return self.run_ctl("send", "--url", self.url, "--to", "researcher", "--from", sender,
                            "--body", "please review", "--token", token)

    def test_normal_send_exits_0(self):
        r = self.send("human")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_system_name_exits_2_and_prints_reason(self):
        r = self.send("rookery")
        self.assertEqual(r.returncode, 2)
        self.assertIn("system-name", r.stderr)

    def test_bad_token_exits_1(self):
        r = self.send("human", token="wrong")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unauthorized", r.stderr)

    def test_help_says_legacy_for_inbox_and_loop(self):
        top = self.run_ctl("--help")
        self.assertIn("legacy", top.stdout.lower())
        for sub in ("inbox", "loop"):
            r = self.run_ctl(sub, "--help")
            self.assertIn("legacy", r.stdout.lower(), sub)


if __name__ == "__main__":
    unittest.main()
