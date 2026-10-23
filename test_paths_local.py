#!/usr/bin/env python3
"""Mail gate, T012: the 3 tries on the local paths, block (`rookery turn`) and `rookery send`.

    python3 -m unittest test_paths_local -v

2 paths x 3 tries = 6 cases, all with a session. The tries and the asserts are in gate_matrix.py.
"""
import json
import subprocess
import sys
import unittest

from gate_matrix import SendMatrix
from test_hooks import Base, CLI, HERE


class BlockPath(SendMatrix, Base):
    def send(self, to):
        block = json.dumps({"to": to, "task": "TASK-1", "kind": "ask", "status": "in-progress", "say": "please review"})
        self.hook("stop", {"hook_event_name": "Stop", "session_id": "c1", "cwd": self.tmp.name,
                           "last_assistant_message": f"Done.\n\n```rookery\n{block}\n```\n"},
                  env=self.env(ROOKERY_NODE="me"))


class SendPath(SendMatrix, Base):
    def send(self, to):
        out = subprocess.run([sys.executable, CLI, "send", to, "please review"],
                             capture_output=True, text=True, env=self.env(), cwd=HERE, timeout=30)
        self.assertIn(out.returncode, (0, 1), out.stderr)


if __name__ == "__main__":
    unittest.main()
