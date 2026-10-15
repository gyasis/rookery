#!/usr/bin/env python3
"""Direct unit tests of cli_init (wave-11 audit fixes). They call the module, not the CLI,
so they pass before `init` is registered in rookery_cli.py (T032).

    python3 -W ignore -m unittest test_cli_init_unit -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

import cli_init

HERE = os.path.dirname(os.path.abspath(__file__))
START, END = cli_init.START_MARKER, cli_init.END_MARKER
TEXT = "line one\nline two\n"


def section(text=TEXT, nl="\n"):
    body = nl.join(text.rstrip("\n").split("\n"))
    return START + nl + body + nl + END + nl


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)

    def path(self, name="CLAUDE.md"):
        return os.path.join(self.d, name)

    def put(self, data, name="CLAUDE.md"):
        with open(self.path(name), "wb") as f:
            f.write(data)
        return self.path(name)

    def get(self, name="CLAUDE.md"):
        with open(self.path(name), "rb") as f:
            return f.read()


class TestSymlink(Base):
    def test_symlink_kept_and_target_updated(self):
        self.put(b"# agents\n", "AGENTS.md")
        os.symlink("AGENTS.md", self.path())
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        self.assertTrue(os.path.islink(self.path()))
        self.assertEqual(self.get("AGENTS.md"), b"# agents\n\n" + section().encode())
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "unchanged")

    def test_dangling_symlink_creates_target(self):
        os.symlink("AGENTS.md", self.path())
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "created")
        self.assertTrue(os.path.islink(self.path()))
        self.assertEqual(self.get("AGENTS.md"), section().encode())

    def test_write_text_atomic_follows_symlink(self):
        self.put(b"old", "real.txt")
        os.symlink("real.txt", self.path("link.txt"))
        cli_init.write_text_atomic(self.path("link.txt"), "new")
        self.assertTrue(os.path.islink(self.path("link.txt")))
        self.assertEqual(self.get("real.txt"), b"new")
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith(".tmp-")], [])


class TestBadFiles(Base):
    def test_latin1_file_inserted_bytes_preserved(self):
        orig = b"# caf\xe9 notes\n"
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        new = self.get()
        self.assertTrue(new.startswith(orig))
        self.assertTrue(new.endswith(section().encode()))
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "unchanged")

    def test_latin1_outside_markers_kept_on_replace(self):
        old = b"caf\xe9\n" + section("old\n").encode() + b"tail \xff\n"
        self.put(old)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "replaced")
        self.assertEqual(self.get(), b"caf\xe9\n" + section().encode() + b"tail \xff\n")

    def test_directory_at_path_raises_init_error(self):
        os.mkdir(self.path())
        with self.assertRaises(cli_init.InitError) as cm:
            cli_init.upsert_section(self.path(), TEXT)
        self.assertIsInstance(cm.exception, ValueError)
        self.assertIn(self.path(), str(cm.exception))

    def test_unreadable_file_raises_init_error(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        p = self.put(b"x\n")
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o644)
        with self.assertRaises(cli_init.InitError) as cm:
            cli_init.upsert_section(p, TEXT)
        self.assertIn(p, str(cm.exception))


class TestMarkerLines(Base):
    def test_markers_inside_backtick_fence_ignored(self):
        orig = ("# Docs\n\n```\n" + START + "\nexample\n" + END + "\n```\n").encode()
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        self.assertEqual(self.get(), orig + b"\n" + section().encode())
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "unchanged")

    def test_markers_inside_tilde_fence_ignored(self):
        orig = ("~~~~\n" + START + "\n" + END + "\n~~~~\n").encode()
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        self.assertEqual(self.get(), orig + b"\n" + section().encode())

    def test_shorter_inner_fence_does_not_close(self):
        orig = ("````\n```\n" + START + "\n````\n").encode()
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")

    def test_prose_mention_of_one_marker_is_not_malformed(self):
        orig = ("Insert " + START + " where you want it.\n").encode()
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        self.assertTrue(self.get().startswith(orig))

    def test_midline_pair_ignored(self):
        orig = ("a " + START + " b " + END + " c\n").encode()
        self.put(orig)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "inserted")
        self.assertEqual(self.get(), orig + b"\n" + section().encode())

    def test_real_section_beside_fenced_example_replaced(self):
        fenced = "```\n" + START + "\n" + END + "\n```\n"
        old = (fenced + section("old\n")).encode()
        self.put(old)
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "replaced")
        self.assertEqual(self.get(), (fenced + section()).encode())

    def test_malformed_real_markers_raise_and_write_nothing(self):
        cases = {
            "two_start": START + "\n" + START + "\n" + END + "\n",
            "two_end": START + "\n" + END + "\n" + END + "\n",
            "end_first": END + "\n" + START + "\n",
            "only_start": "x\n" + START + "\n",
            "only_end": "x\n" + END + "\n",
        }
        for name, content in cases.items():
            with self.subTest(name):
                p = self.put(content.encode())
                with self.assertRaises(cli_init.InitError) as cm:
                    cli_init.upsert_section(p, TEXT)
                self.assertIn(p, str(cm.exception))
                self.assertEqual(self.get(), content.encode())


class TestNewlines(Base):
    def test_crlf_file_gets_crlf_section(self):
        orig = b"a\r\nb\r\n"
        self.put(orig)
        cli_init.upsert_section(self.path(), TEXT)
        self.assertEqual(self.get(), orig + b"\r\n" + section(nl="\r\n").encode())
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "unchanged")

    def test_crlf_blank_line_not_doubled(self):
        orig = b"a\r\n\r\n"
        self.put(orig)
        cli_init.upsert_section(self.path(), TEXT)
        self.assertEqual(self.get(), orig + section(nl="\r\n").encode())

    def test_crlf_unterminated_last_line(self):
        orig = b"a\r\nb"
        self.put(orig)
        cli_init.upsert_section(self.path(), TEXT)
        self.assertEqual(self.get(), orig + b"\r\n\r\n" + section(nl="\r\n").encode())

    def test_crlf_replace_keeps_crlf(self):
        self.put(b"a\r\n\r\n" + section("old\n", "\r\n").encode() + b"z\r\n")
        self.assertEqual(cli_init.upsert_section(self.path(), TEXT), "replaced")
        self.assertEqual(self.get(),
                         b"a\r\n\r\n" + section(nl="\r\n").encode() + b"z\r\n")

    def test_mixed_newlines_use_lf(self):
        orig = b"a\r\nb\n"
        self.put(orig)
        cli_init.upsert_section(self.path(), TEXT)
        self.assertEqual(self.get(), orig + b"\n" + section().encode())

    def test_lf_blank_line_not_doubled(self):
        self.put(b"a\n\n")
        cli_init.upsert_section(self.path(), TEXT)
        self.assertEqual(self.get(), b"a\n\n" + section().encode())


class TestLock(Base):
    def test_upsert_has_no_lock_root_parameter(self):
        with self.assertRaises(TypeError):
            cli_init.upsert_section(self.path(), TEXT, lock_root=self.d)

    def test_nested_init_lock_returns(self):
        code = ("import sys, cli_init\n"
                "r = sys.argv[1]\n"
                "with cli_init.init_lock(r):\n"
                "    with cli_init.init_lock(r):\n"
                "        pass\n"
                "print('ok')\n")
        try:
            out = subprocess.run([sys.executable, "-c", code, self.d], cwd=HERE,
                                 capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("nested init_lock deadlocked")
        self.assertEqual(out.stdout.strip(), "ok", out.stderr)

    def test_lock_released_after_outermost_exit(self):
        import fcntl
        with cli_init.init_lock(self.d):
            with cli_init.init_lock(self.d):
                pass
        with open(os.path.join(self.d, "locks", "init.lock"), "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_empty_or_none_root_rejected(self):
        old = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, old)
        for bad in ("", None, "  "):
            with self.subTest(bad=bad):
                with self.assertRaises(cli_init.InitError):
                    with cli_init.init_lock(bad):
                        pass
        self.assertEqual(os.listdir(self.d), [])

    def test_relative_root_made_absolute(self):
        old = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, old)
        with cli_init.init_lock("rel"):
            pass
        self.assertTrue(os.path.isfile(os.path.join(self.d, "rel", "locks", "init.lock")))


class TestBasics(Base):
    def test_created_replaced_unchanged(self):
        p = self.path()
        self.assertEqual(cli_init.upsert_section(p, TEXT), "created")
        self.assertEqual(cli_init.upsert_section(p, TEXT), "unchanged")
        self.assertEqual(cli_init.upsert_section(p, "other\n"), "replaced")
        self.assertEqual(self.get(), section("other\n").encode())


if __name__ == "__main__":
    unittest.main()
