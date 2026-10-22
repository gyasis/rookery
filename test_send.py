#!/usr/bin/env python3
"""`rookery send` (retires ~/.local/bin/rk): the same gate as the block path.

    python3 -m unittest test_send -v

Each case runs `python3 rookery_cli.py send ...` as a subprocess on a temp ROOKERY_DB.
"""
import json
import sqlite3
import subprocess
import sys
import unittest

import rookery_identity as RI
from test_hooks import Base, CLI, HERE


class SendTests(Base):
    def setUp(self):
        super().setUp()
        self.beta = RI.new_session(self.conn, name="beta")
        self.bme = RI.new_identity(self.conn, self.beta["session_id"], "bme")

    def cli(self, *argv, env=None):
        return subprocess.run([sys.executable, CLI, "send", *argv], capture_output=True,
                              text=True, env=env if env is not None else self.env(),
                              cwd=HERE, timeout=30)

    def rows(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute("SELECT * FROM inbox ORDER BY id")]
        finally:
            c.close()

    def test_valid_peer_is_delivered_from_my_identity_in_my_session(self):
        out = self.cli("researcher", "please review", "--task", "TASK-1", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout)
        self.assertEqual(res["outcome"], "sent")
        [row] = self.rows()
        self.assertEqual(row["id"], res["msg_id"])
        self.assertEqual(row["sender"], self.me["agent_id"])
        self.assertEqual(row["recipient"], self.researcher["agent_id"])
        self.assertEqual(row["session_id"], self.alpha["session_id"])
        self.assertEqual(row["status"], "pending")
        body = json.loads(row["body"])
        self.assertEqual((body["to"], body["say"], body["kind"]), ("researcher", "please review", "ask"))

    def test_unknown_name_is_quarantined_with_a_bounce(self):
        out = self.cli("nobody", "hi", "--json")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertEqual(json.loads(out.stdout)["reason"], "wrong-recipient")
        rows = self.rows()
        self.assertEqual(rows[0]["status"], "quarantined")
        self.assertTrue(any(r["recipient"] == self.me["agent_id"] and r["status"] == "pending"
                            for r in rows[1:]), "no bounce to the sender")

    def test_agent_in_another_session_is_off_team(self):
        out = self.cli("bme", "hi", "--json")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertEqual(json.loads(out.stdout)["reason"], "off-team")
        self.assertFalse(any(r["recipient"] == self.bme["agent_id"] for r in self.rows()))

    def test_as_and_from_are_not_accepted(self):
        for flag in ("--as", "--from"):
            out = self.cli("researcher", "hi", flag, "swe")
            self.assertEqual(out.returncode, 2, (flag, out.stderr))
            self.assertIn("unrecognized arguments", out.stderr)
        self.assertEqual(self.rows(), [])

    def test_no_session_exits_3_and_stores_nothing(self):
        out = self.cli("researcher", "hi", env=self.env(session=False, node=False))
        self.assertEqual(out.returncode, 3, out.stderr)
        self.assertIn("No current Rookery session", out.stderr)
        self.assertEqual(self.rows(), [])

    def test_unknown_sender_name_is_not_registered(self):
        before = self.snapshot()
        out = self.cli("researcher", "hi", env=self.env(ROOKERY_NODE="ghost"))
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("No agent identity found", out.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_bad_block_is_refused_and_stores_nothing(self):
        for argv in (("researcher", "hi", "--kind", "chat"), ("researcher", ""),
                     ("bad name!", "hi")):
            out = self.cli(*argv)
            self.assertEqual(out.returncode, 2, (argv, out.stderr))
        self.assertEqual(self.rows(), [])

    def test_loop_breaker_holds_after_the_limit(self):
        for _ in range(10):
            self.assertEqual(self.cli("researcher", "ping", "--task", "T").returncode, 0)
        out = self.cli("researcher", "ping", "--task", "T", "--json")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertEqual(json.loads(out.stdout)["outcome"], "loop-held")
        self.assertEqual(self.rows()[-1]["status"], "held")


if __name__ == "__main__":
    unittest.main()
