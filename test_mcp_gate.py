#!/usr/bin/env python3
"""Mail gate, T039: MCP reads never mark mail read; the `ack` tool does (FR-032, US6 scenario 4).

    python3 -m unittest test_mcp_gate -v

Each MCP call runs in a subprocess, because `rookery_mcp` reads its mode from argv and env at import.
"""
import asyncio
import json
import subprocess
import sys
import unittest

import rookery as R
from test_hooks import Base, HERE

DRIVER = """
import asyncio, json, sys, types
import rookery_mcp as m
hdr = {"x-rookery-node": sys.argv[2]} if len(sys.argv) > 2 else {}
ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(request=types.SimpleNamespace(headers=hdr)))
print(json.dumps(eval(sys.argv[1])))
"""


class McpGate(Base):
    def call(self, expr, env=None):
        out = subprocess.run([sys.executable, "-c", DRIVER, expr],
                             capture_output=True, text=True, env=env or self.env(), cwd=HERE,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def put(self, recipient, topic, sid=None, sender=None):
        return R.send(self.conn, sender or self.swe["agent_id"], recipient, "body-" + topic,
                      topic=topic, hold=False, session_id=sid or self.alpha["session_id"])

    def statuses(self):
        return [tuple(r) for r in self.conn.execute("SELECT id, status FROM inbox ORDER BY id")]

    def two(self):
        return (self.put(self.me["agent_id"], "one"), self.put(self.me["agent_id"], "two"))


class WithSession(McpGate):
    def test_check_inbox_twice_returns_both_and_changes_nothing(self):
        a, b = self.two()
        before = self.statuses()
        for _ in range(2):
            text = self.call("m.check_inbox(ctx)")
            self.assertIn("body-one", text)
            self.assertIn("body-two", text)
            self.assertTrue(text.rstrip().endswith(f"Mark read: ack([{a}, {b}])"), text)
        self.assertEqual(self.statuses(), before)

    def test_ack_one_then_inbox_shows_other(self):
        a, b = self.two()
        res = self.call(f"m.ack([{a}], ctx)")
        self.assertEqual((res["ok"], res["already"], res["skipped"]), ([a], [], []))
        text = self.call("m.check_inbox(ctx)")
        self.assertNotIn("body-one", text)
        self.assertIn("body-two", text)
        self.assertEqual(dict(self.statuses())[a], "done")
        self.assertEqual(dict(self.statuses())[b], "pending")

    def test_await_message_returns_mail_and_changes_nothing(self):
        a, b = self.two()
        before = self.statuses()
        got = self.call("asyncio.run(m.await_message(ctx, timeout=2))")
        self.assertEqual([g["id"] for g in got], [a, b])
        self.assertEqual([g["body"] for g in got], ["body-one", "body-two"])
        self.assertEqual(self.statuses(), before)

    def test_ack_of_other_identitys_mail_is_skipped(self):
        theirs = self.put(self.researcher["agent_id"], "theirs")
        res = self.call(f"m.ack([{theirs}], ctx)")
        self.assertEqual((res["ok"], res["skipped"]), ([], [theirs]))
        self.assertEqual(dict(self.statuses())[theirs], "pending")


class NoSession(McpGate):
    def env(self, **kw):
        return super().env(session=False, node=False, ROOKERY_NODE="loner", **kw)

    def test_send_stores_legacy_and_inbox_and_ack_work(self):
        out = self.call("m.send('loner', 'hello self', ctx)")
        self.assertTrue(out.startswith("sent #"), out)
        row = self.conn.execute("SELECT id, session_id FROM inbox WHERE body='hello self'").fetchone()
        self.assertEqual(row["session_id"], "legacy")
        text = self.call("m.check_inbox(ctx)")
        self.assertIn("hello self", text)
        self.assertEqual(self.call("m.check_inbox(ctx)"), text)       # reading again: still there
        session_row = self.put(self.me["agent_id"], "sess")
        res = self.call(f"m.ack([{row['id']}, {session_row}], ctx)")
        self.assertEqual((res["ok"], res["skipped"]), ([row["id"]], [session_row]))
        self.assertNotIn("hello self", self.call("m.check_inbox(ctx)"))


if __name__ == "__main__":
    unittest.main()
