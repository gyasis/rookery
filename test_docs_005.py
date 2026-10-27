"""T022 (feature 005): the docs describe the event wake and pass ste-lint.

README.md, CHANGELOG.md and skills/rookery/SKILL.md each pass
`ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt` as whole files. Before this
feature CHANGELOG.md scored 2.05 on origin/master (long sentences in the 0.7.0 notes);
the `## Unreleased` section of feature 005 adds enough clean text that the whole file now
scores under 2.0, so the test lints the whole file. The `## Unreleased` section is also
linted on its own, so that the gate holds if the 0.7.0 notes ever change.

The lint tests skip, with a reason, when `ste-lint` is not on PATH. The name tests never
skip: each file names `rookery wake`, `rookery check` and `screener.enabled`.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GLOSSARY = os.path.join(HERE, "ste-glossary.txt")
DOCS = ("README.md", "CHANGELOG.md", os.path.join("skills", "rookery", "SKILL.md"))
NAMES = ("rookery wake", "rookery check", "screener.enabled")
MAX_SCORE = "2.0"


def _lint(path):
    """(exit code, output) of ste-lint on path."""
    res = subprocess.run(["ste-lint", path, "--max-score", MAX_SCORE, "--glossary", GLOSSARY],
                         capture_output=True, text=True, timeout=120)
    return res.returncode, res.stdout + res.stderr


def _section(text, heading, stop_prefix):
    """The lines from `heading` up to the next line that starts with stop_prefix."""
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith(heading):
            inside = True
        elif inside and line.startswith(stop_prefix):
            break
        if inside:
            out.append(line)
    return "".join(out)


class DocsNameTheCommands(unittest.TestCase):
    def test_each_doc_names_wake_check_and_screener_enabled(self):
        for rel in DOCS:
            with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
                text = fh.read()
            for name in NAMES:
                self.assertIn(name, text, f"{rel} does not name {name!r}")

    def test_changelog_has_unreleased_above_0_7_0(self):
        with open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Unreleased", text)
        self.assertLess(text.index("## Unreleased"), text.index("## 0.7.0"))
        section = _section(text, "## Unreleased", "## 0.7.0")
        for name in NAMES + ("rookery sweep", "rookery up --session", "ROOKERY_WAKE=off", "300"):
            self.assertIn(name, section, f"the Unreleased section does not name {name!r}")

    def test_readme_has_the_wake_section(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        section = _section(text, "## How mail wakes an agent", "## ")
        self.assertTrue(section, "README.md has no section 'How mail wakes an agent'")
        for name in NAMES + ("rookery sweep", "ROOKERY_WAKE=off", "rookery-mcp.service",
                             "rookery-mailroom.service", "rookery session end"):
            self.assertIn(name, section, f"the wake section does not name {name!r}")
        self.assertNotIn("python3 postmaster.py\n```", text,
                         "README.md still tells the user to start the postmaster by hand")


@unittest.skipUnless(shutil.which("ste-lint"), "ste-lint is not on PATH")
class DocsPassSteLint(unittest.TestCase):
    def test_whole_files_pass_at_2_0(self):
        for rel in DOCS:
            code, out = _lint(os.path.join(HERE, rel))
            self.assertEqual(code, 0, f"{rel} fails ste-lint at {MAX_SCORE}:\n{out}")

    def test_changelog_unreleased_section_passes_on_its_own(self):
        with open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8") as fh:
            section = _section(fh.read(), "## Unreleased", "## 0.7.0")
        self.assertTrue(section)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "unreleased.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(section)
            code, out = _lint(path)
        self.assertEqual(code, 0, f"the Unreleased section fails ste-lint at {MAX_SCORE}:\n{out}")

    def test_readme_wake_section_passes_on_its_own(self):
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            section = _section(fh.read(), "## How mail wakes an agent", "## ")
        self.assertTrue(section)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wake.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(section)
            code, out = _lint(path)
        self.assertEqual(code, 0, f"the wake section fails ste-lint at {MAX_SCORE}:\n{out}")


if __name__ == "__main__":
    unittest.main()
