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
TEXT_FILES = ("cli_join.py", "plugin_herdr/cli.py")
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
                self.assertNotIn(bad, text, f"{name} tells agents to use {bad}")

    def test_skill_names_no_removed_script(self):
        for name in ("skills/rookery/SKILL.md", "rookery_section.md"):
            text = (ROOT / name).read_text()
            for gone in ("mailctl.py", "send_mail.py", "read_mail.py", "rookery_mcp", "Legacy mesh"):
                self.assertNotIn(gone, text, f"{name} names {gone}")

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
