"""`rookery herdr status` and `focus` read the identity bound to each pane from the `bindings` table.

    python3 -m unittest test_herdr_status -v
"""
import contextlib
import io
import types
import unittest
from unittest import mock

import plugin_herdr.bridge as H
import plugin_herdr.cli as C
import rookery
import rookery_identity as RI
from test_hooks import Base

PANES = [{"pane_id": "w1:p2", "agent": "claude", "agent_status": "idle"},
         {"pane_id": "w1:p3", "agent": "codex", "agent_status": "working"}]


class HerdrStatus(Base):
    def setUp(self):
        super().setUp()
        s = RI.new_session(self.conn, name="gamma")
        i = RI.new_identity(self.conn, s["session_id"], "scout")
        self.conn.execute("INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) "
                          "VALUES(?, 'identity', ?, 'herdr-pane', 'w1:p2', ?)",
                          (s["session_id"], i["agent_id"], rookery.now()))
        self.conn.commit()

    def test_status_shows_identity_and_session_per_pane(self):
        out = io.StringIO()
        with mock.patch.object(H, "agents", lambda: PANES), mock.patch.object(H, "reason", lambda: None), \
                contextlib.redirect_stdout(out):
            C.cmd_herdr_status(None)
        lines = out.getvalue().splitlines()
        self.assertIn("IDENTITY", lines[1])
        self.assertIn("SESSION", lines[1])
        self.assertTrue(any("w1:p2" in x and "scout" in x and "gamma" in x for x in lines), lines)
        self.assertTrue(any("w1:p3" in x and "no binding" in x for x in lines), lines)

    def test_focus_resolves_identity_name_to_pane(self):
        seen = []
        with mock.patch.object(H, "require", lambda what: None), \
                mock.patch.object(H, "focus", lambda t: seen.append(t) or True), \
                contextlib.redirect_stdout(io.StringIO()):
            C.cmd_herdr_focus(types.SimpleNamespace(name="scout"))
            C.cmd_herdr_focus(types.SimpleNamespace(name="w9:p9"))
        self.assertEqual(seen, ["w1:p2", "w9:p9"])


if __name__ == "__main__":
    unittest.main()
