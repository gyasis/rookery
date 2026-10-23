#!/usr/bin/env python3
"""Mail gate, T018: the node runner's sends go through the gate (path `node-runner`).

    python3 -m unittest test_paths_node_runner -v

3 tries through `handle_directives` (a `MAILTO:` line, session alpha), plus the auto-reply rule.
"""
import os
import unittest
from unittest import mock

import node_runner
import rookery as R
from gate_matrix import SendMatrix
from test_hooks import Base


class NodeRunnerPath(SendMatrix, Base):
    def send(self, to):
        conn = R.connect(self.db)
        try:
            with mock.patch.dict(os.environ, {"ROOKERY_SESSION": "alpha"}):
                node_runner.handle_directives(conn, self.me["agent_id"], f"MAILTO:{to}:please review",
                                              poll=0, max_wait=0)
        finally:
            conn.close()


class AutoReply(Base):
    def _reply(self, sender):
        conn = R.connect(self.db)
        try:
            return node_runner.auto_reply(conn, self.me["agent_id"], sender, "result text")
        finally:
            conn.close()

    def rows(self):
        return self.conn.execute("SELECT * FROM inbox").fetchall()

    def test_system_sender_gets_no_reply(self):
        for name in ("rookery", "Rookery", "postmaster@x"):
            self._reply(name)
        self.assertEqual(len(self.rows()), 0)

    def test_peer_gets_reply(self):
        self._reply("researcher")
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
