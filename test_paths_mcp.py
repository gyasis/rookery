#!/usr/bin/env python3
"""Mail gate, T014: the 3 tries on the MCP `send` tool, stdio and shared HTTP.

    python3 -m unittest test_paths_mcp -v

2 paths x 3 tries = 6 cases. stdio runs with a session; shared HTTP has none (header only).
Each call runs in a subprocess, because `rookery_mcp` reads its mode from argv and env at import.
"""
import subprocess
import sys
import unittest

from gate_matrix import SendMatrix
from test_hooks import Base, HERE

DRIVER = """
import sys, types
import rookery_mcp
hdr = {"x-rookery-node": sys.argv[2]} if len(sys.argv) > 2 else {}
ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(request=types.SimpleNamespace(headers=hdr)))
print(rookery_mcp.send(sys.argv[1], "please review", ctx))
"""


class McpStdioPath(SendMatrix, Base):
    def send(self, to):
        out = subprocess.run([sys.executable, "-c", DRIVER, to], capture_output=True, text=True,
                             env=self.env(), cwd=HERE, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)


class McpHttpPath(SendMatrix, Base):
    with_session = False

    def send(self, to):
        out = subprocess.run([sys.executable, "-c", DRIVER, to, "me", "--http", "0"],
                             capture_output=True, text=True, env=self.env(session=False, node=False),
                             cwd=HERE, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
