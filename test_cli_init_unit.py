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


import json
import multiprocessing

DEVKID = os.path.join(HERE, "fixtures", "settings", "devkid.json")
EVS = ("SessionStart", "UserPromptSubmit", "Stop")
SUBS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "Stop": "stop"}


def _rk(data, ev):
    return [h for g in data["hooks"].get(ev, []) for h in g["hooks"]
            if h["command"].startswith("rookery hook ")]


def _merge_worker(args):
    cli_init.merge_hooks(*args)


class TestMergeHooks(Base):
    def sp(self):
        return os.path.join(self.d, ".claude", "settings.json")

    def load(self):
        return json.loads(self.get_abs(self.sp()))

    def get_abs(self, p):
        with open(p, "rb") as f:
            return f.read()

    def put_settings(self, data):
        os.makedirs(os.path.dirname(self.sp()), exist_ok=True)
        with open(self.sp(), "wb") as f:
            f.write(data)

    def test_missing_file_created(self):
        r = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(r, {"added": list(EVS), "updated": [], "unchanged": []})
        raw = self.get_abs(self.sp()).decode()
        self.assertTrue(raw.endswith("}\n"))
        self.assertIn('\n  "hooks"', raw)
        d = json.loads(raw)
        for ev in EVS:
            self.assertEqual(_rk(d, ev)[0]["command"], f"rookery hook {SUBS[ev]} --project p")

    def test_devkid_merge_prefix_order_indent_idempotent(self):
        raw = self.get_abs(DEVKID)
        self.put_settings(raw)
        old = json.loads(raw)
        r = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(sorted(r["added"]), sorted(EVS))
        new = self.load()
        for ev in EVS:
            ol = old["hooks"].get(ev, [])
            self.assertEqual(new["hooks"][ev][:len(ol)], ol)
            self.assertEqual(len(new["hooks"][ev]), len(ol) + 1)
            self.assertEqual(len(_rk(new, ev)), 1)
        for k in old["hooks"]:
            if k not in EVS:
                self.assertEqual(new["hooks"][k], old["hooks"][k])
        for k in old:
            if k != "hooks":
                self.assertEqual(new[k], old[k])
        self.assertEqual(list(new), list(old))
        self.assertEqual(list(new["hooks"])[:len(old["hooks"])], list(old["hooks"]))
        self.assertIn('\n  "hooks"', self.get_abs(self.sp()).decode())
        before = os.stat(self.sp()).st_mtime_ns
        bytes1 = self.get_abs(self.sp())
        r2 = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(r2, {"added": [], "updated": [], "unchanged": list(EVS)})
        self.assertEqual(os.stat(self.sp()).st_mtime_ns, before)
        self.assertEqual(self.get_abs(self.sp()), bytes1)

    def test_indent_and_no_trailing_newline_kept(self):
        self.put_settings(b'{\n    "a": 1\n}')
        cli_init.merge_hooks(self.sp(), "p")
        raw = self.get_abs(self.sp()).decode()
        self.assertTrue(raw.startswith('{\n    "a": 1,\n    "hooks"'))
        self.assertFalse(raw.endswith("\n"))
        self.put_settings(b'{\n\t"a": 1\n}\n')
        cli_init.merge_hooks(self.sp(), "p")
        self.assertIn('\n\t"hooks"', self.get_abs(self.sp()).decode())

    def test_stale_command_replaced_in_place(self):
        seed = {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "a.sh"}]},
            {"hooks": [{"type": "command", "command": "b.sh"},
                       {"type": "command", "command": "rookery hook stop --project old"},
                       {"type": "command", "command": "c.sh"}]},
            {"hooks": [{"type": "command", "command": "d.sh"}]}]}}
        self.put_settings(json.dumps(seed, indent=2).encode())
        r = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(r["updated"], ["Stop"])
        self.assertEqual(sorted(r["added"]), ["SessionStart", "UserPromptSubmit"])
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual(stop[0], seed["hooks"]["Stop"][0])
        self.assertEqual(stop[2], seed["hooks"]["Stop"][2])
        self.assertEqual(stop[1]["hooks"][0], seed["hooks"]["Stop"][1]["hooks"][0])
        self.assertEqual(stop[1]["hooks"][1]["command"], "rookery hook stop --project p")
        self.assertEqual(stop[1]["hooks"][2], seed["hooks"]["Stop"][1]["hooks"][2])
        self.assertEqual(len(stop), 3)

    def test_duplicates_collapse_to_one(self):
        seed = {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "rookery hook stop --project p"}]},
            {"hooks": [{"type": "command", "command": "x.sh"},
                       {"type": "command", "command": "rookery hook stop --project q"}]},
            {"hooks": [{"type": "command", "command": "rookery hook stop --project r"}]}]}}
        self.put_settings(json.dumps(seed).encode())
        r = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(r["updated"], ["Stop"])
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual(len(_rk(self.load(), "Stop")), 1)
        self.assertEqual(len(stop), 2)   # the group left empty was dropped
        self.assertEqual(stop[1]["hooks"], [{"type": "command", "command": "x.sh"}])

    def _stop_cmds(self):
        return [h["command"] for g in self.load()["hooks"]["Stop"] for h in g["hooks"]]

    def test_user_command_with_suffix_untouched(self):
        seed = {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "rookery hook stop --project x && other"}]}]}}
        self.put_settings(json.dumps(seed).encode())
        cli_init.merge_hooks(self.sp(), "p")
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual(stop[0], seed["hooks"]["Stop"][0])
        self.assertEqual(stop[1]["hooks"][0]["command"], "rookery hook stop --project p")

    def test_abs_path_and_leading_space_recognised(self):
        for c in ("/usr/bin/rookery hook stop --project p", " rookery hook stop --project p ",
                  "/usr/bin/rookery hook stop --project old"):
            with self.subTest(c=c):
                self.put_settings(json.dumps({"hooks": {"Stop": [
                    {"hooks": [{"type": "command", "command": c, "timeout": 5}]}]}}).encode())
                cli_init.merge_hooks(self.sp(), "p")
                stop = self.load()["hooks"]["Stop"]
                self.assertEqual(len(stop), 1)
                self.assertEqual(stop[0]["hooks"], [{"type": "command", "timeout": 5,
                                 "command": "rookery hook stop --project p"}])

    def test_rename_project_in_place(self):
        self.put_settings(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "rookery hook stop --project a"}]}]}}).encode())
        r = cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(r["updated"], ["Stop"])
        self.assertEqual(self._stop_cmds(), ["rookery hook stop --project p"])

    def test_dedupe_keeps_non_ours_in_same_group(self):
        self.put_settings(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "rookery hook stop --project p"}]},
            {"hooks": [{"type": "command", "command": "rookery hook stop --project q"},
                       {"type": "command", "command": "rookery hook stop --project q && y"}]}]}}).encode())
        cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(self._stop_cmds(), ["rookery hook stop --project p",
                                             "rookery hook stop --project q && y"])

    def _reject(self, raw, word):
        self.put_settings(raw)
        with self.assertRaises(cli_init.InitError) as cm:
            cli_init.merge_hooks(self.sp(), "p")
        m = str(cm.exception)
        self.assertIn(self.sp(), m)
        self.assertIn(word, m)
        self.assertTrue(m.endswith("Fix the file, then run init again."))
        self.assertEqual(self.get_abs(self.sp()), raw)

    def test_duplicate_key_rejected(self):
        self._reject(b'{"a": 1, "a": 2}', "duplicate")

    def test_nan_infinity_rejected(self):
        for raw in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}'):
            with self.subTest(raw=raw):
                self._reject(raw, "not valid JSON")

    def test_overflow_float_rejected(self):
        self._reject(b'{"a": 1e999}', "not valid JSON")

    def test_crlf_preserved(self):
        self.put_settings(b'{\r\n  "a": 1\r\n}\r\n')
        cli_init.merge_hooks(self.sp(), "p")
        raw = self.get_abs(self.sp())
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertTrue(raw.endswith(b"}\r\n"))
        before = os.stat(self.sp()).st_mtime_ns
        cli_init.merge_hooks(self.sp(), "p")
        self.assertEqual(os.stat(self.sp()).st_mtime_ns, before)
        self.assertEqual(self.get_abs(self.sp()), raw)

    def test_bom_preserved(self):
        self.put_settings(b'\xef\xbb\xbf{"a": 1}\n')
        cli_init.merge_hooks(self.sp(), "p")
        raw = self.get_abs(self.sp())
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf{"))
        self.assertEqual(json.loads(raw[3:])["a"], 1)
        self.assertEqual(len(_rk(json.loads(raw[3:]), "Stop")), 1)

    def test_empty_file_treated_as_object(self):
        for raw in (b"", b" \n\t\n"):
            with self.subTest(raw=raw):
                self.put_settings(raw)
                r = cli_init.merge_hooks(self.sp(), "p")
                self.assertEqual(r["added"], list(EVS))
                self.assertEqual(list(self.load()), ["hooks"])

    def test_invalid_json(self):
        bad = b'{\n  "hooks": {\n    "Stop": [,\n  }\n}\n'
        self.put_settings(bad)
        with self.assertRaises(cli_init.InitError) as cm:
            cli_init.merge_hooks(self.sp(), "p")
        m = str(cm.exception)
        self.assertIn(self.sp(), m)
        self.assertRegex(m, r"line 3 column \d+")
        self.assertIn("is not valid JSON", m)
        self.assertTrue(m.endswith("Fix the file, then run init again."))
        self.assertEqual(self.get_abs(self.sp()), bad)

    def test_bad_shapes_raise_and_leave_file(self):
        cases = [b"[]", b"3", b'{"hooks": []}', b'{"hooks": {"Stop": {}}}',
                 b'{"hooks": {"Stop": [3]}}', b'{"hooks": {"Stop": [{"x": 1}]}}',
                 b'{"hooks": {"Stop": [{"hooks": "no"}]}}']
        for raw in cases:
            with self.subTest(raw=raw):
                self.put_settings(raw)
                with self.assertRaises(cli_init.InitError):
                    cli_init.merge_hooks(self.sp(), "p")
                self.assertEqual(self.get_abs(self.sp()), raw)

    def test_unsafe_project(self):
        for bad in ("a b", "x;rm", "$(x)", "", "..", None, "a" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(cli_init.InitError):
                    cli_init.merge_hooks(self.sp(), bad)
        self.assertFalse(os.path.exists(self.sp()))

    def test_concurrent_merges_under_root(self):
        root = os.path.join(self.d, "root")
        self.put_settings(self.get_abs(DEVKID))
        with multiprocessing.Pool(6) as pool:
            pool.map(_merge_worker, [(self.sp(), "p", root)] * 6)
        d = self.load()
        for ev in EVS:
            self.assertEqual(len(_rk(d, ev)), 1)
        self.assertTrue(os.path.isfile(os.path.join(root, "locks", "init.lock")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "locks")))
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.sp()))
                          if n.startswith(".tmp-")], [])


if __name__ == "__main__":
    unittest.main()


class TestMergePermissions(Base):
    """permissions.allow merge (init also writes the pre-approved command list)."""
    sp = TestMergeHooks.sp
    load = TestMergeHooks.load
    get_abs = TestMergeHooks.get_abs
    put_settings = TestMergeHooks.put_settings

    def merge(self):
        return cli_init.merge_hooks(self.sp(), "p", with_permissions=True)

    def test_fresh_gets_all_twelve(self):
        r = self.merge()
        self.assertEqual(len(cli_init.PERMISSION_RULES), 12)
        self.assertEqual(r["permissions"], {"added": list(cli_init.PERMISSION_RULES),
                                            "unchanged": [], "skipped": []})
        self.assertEqual(self.load()["permissions"]["allow"], list(cli_init.PERMISSION_RULES))

    def test_existing_entries_and_keys_kept_in_order(self):
        seed = {"permissions": {"defaultMode": "plan", "allow": ["Bash(ls:*)", "Bash(rookery peek:*)", 7],
                                "extra": {"k": 1}}}
        self.put_settings(json.dumps(seed).encode())
        r = self.merge()
        self.assertEqual(r["permissions"]["unchanged"], ["Bash(rookery peek:*)"])
        self.assertEqual(len(r["permissions"]["added"]), 11)
        perms = self.load()["permissions"]
        self.assertEqual(perms["allow"][:3], seed["permissions"]["allow"])
        self.assertEqual(perms["allow"][3:], r["permissions"]["added"])
        self.assertEqual(perms["defaultMode"], "plan")
        self.assertEqual(perms["extra"], {"k": 1})
        self.assertEqual(list(perms), ["defaultMode", "allow", "extra"])

    def test_idempotent(self):
        self.merge()
        raw, before = self.get_abs(self.sp()), os.stat(self.sp()).st_mtime_ns
        r = self.merge()
        self.assertEqual(r["permissions"]["added"], [])
        self.assertEqual(r["permissions"]["unchanged"], list(cli_init.PERMISSION_RULES))
        self.assertEqual(self.get_abs(self.sp()), raw)
        self.assertEqual(os.stat(self.sp()).st_mtime_ns, before)

    def test_deny_and_ask_rules_skipped_and_untouched(self):
        seed = {"permissions": {"deny": ["Bash(herdr agent start:*)"],
                                "ask": ["Bash(rookery turn:*)"]}}
        self.put_settings(json.dumps(seed).encode())
        r = self.merge()
        self.assertEqual(r["permissions"]["skipped"],
                         ["Bash(rookery turn:*)", "Bash(herdr agent start:*)"])
        self.assertEqual(len(r["permissions"]["added"]), 10)
        perms = self.load()["permissions"]
        self.assertNotIn("Bash(herdr agent start:*)", perms["allow"])
        self.assertNotIn("Bash(rookery turn:*)", perms["allow"])
        self.assertEqual(perms["deny"], seed["permissions"]["deny"])
        self.assertEqual(perms["ask"], seed["permissions"]["ask"])
        r2 = self.merge()
        self.assertEqual(r2["permissions"]["added"], [])
        self.assertEqual(len(r2["permissions"]["skipped"]), 2)

    def test_bad_permission_shapes_raise_and_leave_file(self):
        cases = [b'{"permissions": []}', b'{"permissions": "x"}',
                 b'{"permissions": {"allow": "x"}}', b'{"permissions": {"allow": {}}}',
                 b'{"permissions": {"deny": "x"}}', b'{"permissions": {"ask": 3}}',
                 b'{"permissions": null}']
        for raw in cases:
            with self.subTest(raw=raw):
                self.put_settings(raw)
                with self.assertRaises(cli_init.InitError) as cm:
                    self.merge()
                self.assertIn("Rookery wrote nothing", str(cm.exception))
                self.assertEqual(self.get_abs(self.sp()), raw)

    def test_hooks_only_call_leaves_permissions_alone(self):
        cli_init.merge_hooks(self.sp(), "p")
        self.assertNotIn("permissions", self.load())


class TestZipapp(unittest.TestCase):
    """T048: the installed zipapp must find its data files (no plain paths inside the archive)."""

    def test_init_from_zipapp_writes_section(self):
        import bootstrap
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as t:
            pyz = bootstrap.build_pyz(os.path.join(t, "rookery.pyz"))
            proj, home, root = (os.path.join(t, n) for n in ("proj", "home", "root"))
            for d in (proj, home, root):
                os.makedirs(d)
            env = {"PATH": "/usr/bin:/bin", "HOME": home, "ROOKERY_ROOT": root,
                   "ROOKERY_DB": os.path.join(t, "r.db")}
            r = subprocess.run([sys.executable, pyz, "init", "--dir", proj, "--project", "demo",
                                "--json"], env=env, cwd=t, capture_output=True, text=True,
                               timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(proj, "CLAUDE.md"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("rookery inbox", text)
            self.assertIn(root, text)

    def test_zipapp_run_by_relative_path_reads_data_files(self):
        # `./rookery ...`: the loader's archive path is relative while __file__ is absolute.
        import bootstrap
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as t:
            bootstrap.build_pyz(os.path.join(t, "rookery"))
            proj, home, root = (os.path.join(t, n) for n in ("proj", "home", "root"))
            for d in (proj, home, root):
                os.makedirs(d)
            env = {"PATH": "/usr/bin:/bin", "HOME": home, "ROOKERY_ROOT": root,
                   "ROOKERY_DB": os.path.join(t, "r.db")}
            for args in (["session", "list"], ["init", "--dir", proj, "--project", "demo"]):
                r = subprocess.run([sys.executable, "./rookery", *args], env=env, cwd=t,
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
