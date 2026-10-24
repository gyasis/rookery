#!/usr/bin/env python3
"""rookery up, T005: the gate log has one fixed home, ~/.rookery/logs (spec D2, FR-002, SC-006).

    python3 -m unittest test_d2_gate_home -v

`rookery up` sets ROOKERY_ROOT for each project. The gate log must not split with it.
"""
import json
import os
import unittest

import mail_gate
import rookery_identity as ri
import turn_block as TB
from test_hooks import Base


class GateHome(Base):
    def gate_log(self):
        return os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl")

    def read(self, path):
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_two_project_roots_share_one_gate_log(self):
        p1 = os.path.join(self.tmp.name, "p1")
        p2 = os.path.join(self.tmp.name, "p2")
        for root in (p1, p2):
            os.environ["ROOKERY_ROOT"] = root
            conn = TB.open_db()
            try:
                s = ri.new_session(conn, name="s-" + os.path.basename(root))
                a = ri.new_identity(conn, s["session_id"], "a")
                ri.new_identity(conn, s["session_id"], "b")
                env = {"ROOKERY_SESSION": s["session_id"], "ROOKERY_NODE": a["agent_id"]}
                principal = mail_gate.principal_from_env(conn, env, root)
                res = mail_gate.submit(conn, path="cli", raw_sender="a", to="b", body="hi",
                                       principal=principal, root=root)
                self.assertEqual(res["outcome"], "sent", res)
            finally:
                conn.close()
        recs = self.read(self.gate_log())
        self.assertEqual(len(recs), 2)
        for root in (p1, p2):
            self.assertFalse(os.path.exists(os.path.join(root, "logs", "gate.jsonl")), root)

    def test_explicit_root_is_ignored_for_the_gate_stream(self):
        TB.log_event(self.root, "gate", path="send", outcome="sent")
        self.assertEqual(len(self.read(self.gate_log())), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "gate.jsonl")))

    def test_turn_block_stream_stays_in_the_project_root(self):
        TB.log_event(self.root, "turn-block", outcome="x")
        self.assertTrue(os.path.exists(os.path.join(self.root, "logs", "turn-block.jsonl")))


if __name__ == "__main__":
    unittest.main()
