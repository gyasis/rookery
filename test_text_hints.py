"""test_text_hints — agent-facing texts name `rookery send`, not the old send paths (FR-029, FR-004, SC-002, D4)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT_FILES = ("cli_up.py", "terminal_node.py", "plugin_herdr/cli.py")
BANNED = ("send_mail.py", "rookery.send(", "mailctl.py")


class TextHints(unittest.TestCase):
    def test_sources_do_not_name_old_send_paths(self):
        for name in TEXT_FILES:
            text = (ROOT / name).read_text()
            for bad in BANNED:
                if name == "plugin_herdr/cli.py" and bad == "mailctl.py":
                    continue      # the remote-sidecar briefing keeps mailctl (no remote `rookery send`)
                self.assertNotIn(bad, text, f"{name} tells agents to use {bad}")

    def test_local_briefing_names_only_rookery_commands(self):
        import os
        import sqlite3
        import plugin_herdr.cli as C
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE nodes(node_id TEXT)")
        saved = os.environ.pop("ROOKERY_URL", None)
        try:
            local = C.compose_briefing(conn, "a")
            os.environ["ROOKERY_URL"] = "http://127.0.0.1:1"
            remote = C.compose_briefing(conn, "a")
        finally:
            os.environ.pop("ROOKERY_URL", None)
            if saved is not None:
                os.environ["ROOKERY_URL"] = saved
        for bad in BANNED:
            self.assertNotIn(bad, local)
        self.assertIn("rookery send", local)
        self.assertIn("rookery ack", local)
        self.assertIn("mailctl.py send --url", remote)          # remote agents keep a working client
        self.assertNotIn("send_mail.py", remote)

    def test_briefing_template_names_no_old_path(self):
        import plugin_herdr.cli as C
        for bad in BANNED:
            self.assertNotIn(bad, C.DEFAULT_BRIEFING)

    def test_skill_documents_ack_tool(self):
        text = (ROOT / "skills/rookery/SKILL.md").read_text()
        self.assertIn("`ack` tool", text)
        for legacy in ("mailctl.py", "send_mail.py", "read_mail.py"):
            self.assertIn(legacy, text)


if __name__ == "__main__":
    unittest.main()
