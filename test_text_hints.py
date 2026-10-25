"""test_text_hints — agent-facing texts name `rookery send`, not the old send paths (FR-029, FR-004, SC-002, D4)."""
import ast
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT_FILES = ("cli_join.py", "terminal_node.py", "plugin_herdr/cli.py")
SCREENER_FILES = ("screener.py", "screener_t0.py", "screener_config.py", "cli_screen.py", "rookery_names.py")
BANNED = ("send_mail.py", "rookery.send(", "mailctl.py")


def _text_of(node):
    """The words of a string literal or f-string; each {placeholder} becomes the word X. None if not text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
    return None


def screener_texts(source):
    """Every bounce, notice, refusal and error text in a module: the *_NOTICE and *_BOUNCE constants, the text of
    _fail(...), ValueError(...), print(...) and the help= of add_argument(...). Key=value lines are not sentences."""
    out = []
    for node in ast.walk(ast.parse(source)):
        found = []
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and re.search(r"(NOTICE|BOUNCE)$", t.id)
                                                for t in node.targets):
            found.append(node.value)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in ("_fail", "ValueError", "print") and node.args:
                found.append(node.args[0])
            elif name == "add_argument":
                found += [k.value for k in node.keywords if k.arg == "help"]
        for n in found:
            t = _text_of(n)
            if t and " " in t.strip() and "=" not in t:
                out.append(re.sub(r"\{\w*\}", "X", t.strip()))
    return out


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

    def test_docs_name_up_join_and_tree(self):
        skill = (ROOT / "skills/rookery/SKILL.md").read_text()
        self.assertIn("`rookery up`", skill)
        self.assertIn("`rookery join`", skill)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("postmaster/", readme)
        self.assertIn("state/", readme)

    def test_skill_names_screen_run_and_shadow(self):
        skill = (ROOT / "skills/rookery/SKILL.md").read_text()
        self.assertIn("`rookery screen run`", skill)
        self.assertIn("shadow", skill)

    def test_screener_texts_pass_ste_lint(self):
        exe = shutil.which("ste-lint")
        if not exe:
            self.skipTest("ste-lint is not installed")
        texts = []
        for name in SCREENER_FILES:
            texts += screener_texts((ROOT / name).read_text())
        self.assertGreater(len(texts), 30, "the collector found too few texts")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "texts.md")
            with open(path, "w") as fh:
                fh.write("\n\n".join(texts) + "\n")
            p = subprocess.run([exe, "--max-score", "2.0", "--glossary", str(ROOT / "ste-glossary.txt"), path],
                               capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
