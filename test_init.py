#!/usr/bin/env python3
"""`rookery init` tests (T028, RQ-1.10, RQ-1.11, FR-012 to FR-014, FR-023, SC-004).

Every case drives `python3 rookery_cli.py init ...` as a subprocess with a temp --dir and a
temp ROOKERY_ROOT / HOME / ROOKERY_DB. Nothing touches the real ~/.rookery.

    python3 -m unittest test_init -v
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")
DEVKID = os.path.join(HERE, "fixtures", "settings", "devkid.json")
START = "<!-- ROOKERY START -->"
END = "<!-- ROOKERY END -->"
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")


class InitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        self.home = os.path.join(self.tmp.name, "home")
        self.dir = os.path.join(self.tmp.name, "proj")
        for d in (self.root, self.home, self.dir):
            os.makedirs(d)
        # Scrub every ROOKERY_* and CLAUDE_PROJECT_DIR variable, then set only ours.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("ROOKERY_") and k != "CLAUDE_PROJECT_DIR"}
        self.env.update(ROOKERY_ROOT=self.root, HOME=self.home,
                        ROOKERY_DB=os.path.join(self.tmp.name, "r.db"))

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def args(self, *extra, dir=None, project="p"):
        a = [sys.executable, CLI, "init", "--dir", dir or self.dir]
        if project:
            a += ["--project", project]
        return a + list(extra)

    def run_init(self, *extra, **kw):
        return subprocess.run(self.args(*extra, **kw), env=self.env, cwd=self.tmp.name,
                              capture_output=True, text=True, timeout=60)

    def run_json(self, *extra, **kw):
        r = self.run_init("--json", *extra, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    def read(self, *parts):
        with open(self.p(*parts), "rb") as f:
            return f.read()

    def settings(self):
        return json.loads(self.read(".claude", "settings.json"))

    def tree(self, base=None):
        base = base or self.dir
        out = {}
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d != ".git"]
            for f in fn:
                full = os.path.join(dp, f)
                with open(full, "rb") as fh:
                    out[os.path.relpath(full, base)] = fh.read()
        return out

    def stat_ids(self, *parts):
        st = os.stat(self.p(*parts))
        return (st.st_mtime_ns, st.st_ino)

    def seed_devkid(self):
        os.makedirs(self.p(".claude"))
        shutil.copy(DEVKID, self.p(".claude", "settings.json"))

    @staticmethod
    def rookery_handlers(settings, event):
        return [h for g in settings["hooks"].get(event, []) for h in g["hooks"]
                if h.get("command", "").startswith("rookery hook ")]


class TestContextFile(InitBase):
    def test_creates_claude_md_with_only_section(self):
        out = self.run_json()
        self.assertIs(out["ok"], True)
        self.assertEqual(out["context_file"]["action"], "created")
        self.assertEqual(out["context_file"]["path"], self.p("CLAUDE.md"))
        self.assertEqual(out["settings"]["path"], self.p(".claude", "settings.json"))
        text = self.read("CLAUDE.md").decode()
        self.assertTrue(text.lstrip().startswith(START))
        self.assertTrue(text.rstrip().endswith(END))
        self.assertEqual(text.count(START), 1)
        self.assertEqual(text.count(END), 1)

    def test_appends_section_other_bytes_identical(self):
        orig = b"# My project\n\nSome notes, \xc3\xa9 unicode.\nNo trailing newline"
        with open(self.p("CLAUDE.md"), "wb") as f:
            f.write(orig)
        out = self.run_json()
        self.assertEqual(out["context_file"]["action"], "inserted")
        new = self.read("CLAUDE.md")
        self.assertTrue(new.startswith(orig))
        self.assertIn(START.encode(), new[len(orig):])
        self.assertEqual(new.count(START.encode()), 1)

    def test_replaces_only_between_markers(self):
        before = b"# Head\n\nkeep me\n\n"
        after = b"\n\n## Tail\nkeep me too\n"
        old = before + START.encode() + b"\nOLD STALE TEXT\n" + END.encode() + after
        with open(self.p("CLAUDE.md"), "wb") as f:
            f.write(old)
        out = self.run_json()
        self.assertEqual(out["context_file"]["action"], "replaced")
        new = self.read("CLAUDE.md")
        self.assertTrue(new.startswith(before + START.encode()))
        self.assertTrue(new.endswith(END.encode() + after))
        self.assertNotIn(b"OLD STALE TEXT", new)
        self.assertEqual(new.count(START.encode()), 1)

    def test_section_content(self):
        self.run_json()
        text = self.read("CLAUDE.md").decode()
        self.assertIn(self.root, text)
        self.assertNotIn("{root}", text)
        self.assertNotIn("{project}", text)
        self.assertIn("Rookery (project: p)", text)
        self.assertIn("rookery inbox", text)
        self.assertIn("rookery identity show", text)
        low = text.lower()
        self.assertIn("identity", low)
        self.assertTrue("only when" in low or "only if" in low or "only applies" in low
                        or "applies only" in low, "section must say rules apply only when "
                        "Rookery gives an identity")

    def test_codex_writes_agents_md_only(self):
        out = self.run_json("--harness", "codex")
        self.assertIs(out["ok"], True)
        self.assertEqual(out["context_file"]["action"], "created")
        self.assertEqual(out["context_file"]["path"], self.p("AGENTS.md"))
        self.assertIsNone(out["settings"])
        self.assertTrue(os.path.isfile(self.p("AGENTS.md")))
        self.assertFalse(os.path.exists(self.p(".claude", "settings.json")))
        self.assertFalse(os.path.exists(self.p("CLAUDE.md")))

    def test_project_defaults_to_git_toplevel_name(self):
        top = os.path.join(self.tmp.name, "myrepo")
        sub = os.path.join(top, "sub", "deep")
        os.makedirs(sub)
        subprocess.run(["git", "init", "-q", top], check=True, capture_output=True)
        r = subprocess.run([sys.executable, CLI, "init", "--dir", sub, "--json"],
                           env=self.env, cwd=self.tmp.name, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["project"], "myrepo")
        with open(os.path.join(sub, ".claude", "settings.json")) as f:
            s = json.load(f)
        self.assertEqual(self.rookery_handlers(s, "Stop")[0]["command"],
                         "rookery hook stop --project myrepo")


class TestSettingsMerge(InitBase):
    def test_devkid_gets_one_group_per_event_prefix_preserved(self):
        self.seed_devkid()
        with open(DEVKID) as f:
            old = json.load(f)
        self.run_json()
        new = self.settings()
        for ev in EVENTS:
            old_list = old["hooks"][ev]
            self.assertEqual(new["hooks"][ev][:len(old_list)], old_list, ev)
            hs = self.rookery_handlers(new, ev)
            self.assertEqual(len(hs), 1, ev)
            self.assertEqual(hs[0]["command"], f"rookery hook {ev_cmd(ev)} --project p")
            self.assertEqual(len(new["hooks"][ev]), len(old_list) + 1, ev)
        for ev in old["hooks"]:
            if ev not in EVENTS:
                self.assertEqual(new["hooks"][ev], old["hooks"][ev])
        for k in old:
            if k != "hooks":
                self.assertEqual(new[k], old[k])

    def test_changed_command_replaced_in_place(self):
        os.makedirs(self.p(".claude"))
        seed = {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "other.sh"}]},
            {"hooks": [{"type": "command", "command": "rookery hook stop --project old"}]},
            {"hooks": [{"type": "command", "command": "last.sh"}]},
        ]}}
        with open(self.p(".claude", "settings.json"), "w") as f:
            json.dump(seed, f, indent=2)
        out = self.run_json()
        self.assertIn("Stop", out["settings"]["updated"])
        stop = self.settings()["hooks"]["Stop"]
        self.assertEqual(len(stop), 3)
        self.assertEqual(stop[0], seed["hooks"]["Stop"][0])
        self.assertEqual(stop[1]["hooks"][0]["command"], "rookery hook stop --project p")
        self.assertEqual(stop[2], seed["hooks"]["Stop"][2])

    def test_three_runs_idempotent(self):
        self.seed_devkid()
        first = self.run_json()
        self.assertEqual(sorted(first["settings"]["added"]), sorted(EVENTS))
        ids = (self.stat_ids("CLAUDE.md"), self.stat_ids(".claude", "settings.json"))
        before = self.tree()
        time.sleep(0.05)   # a rewrite would change st_mtime_ns
        for _ in range(2):
            out = self.run_json()
            self.assertEqual(out["context_file"]["action"], "unchanged")
            self.assertEqual(out["settings"]["added"], [])
            self.assertEqual(out["settings"]["updated"], [])
            self.assertEqual(sorted(out["settings"]["unchanged"]), sorted(EVENTS))
            self.assertEqual((self.stat_ids("CLAUDE.md"),
                              self.stat_ids(".claude", "settings.json")), ids)
            self.assertEqual(self.tree(), before)

    def test_invalid_json_exit_2_no_change(self):
        os.makedirs(self.p(".claude"))
        bad = b'{\n  "hooks": {\n    "Stop": [,\n  }\n}\n'
        sp = self.p(".claude", "settings.json")
        with open(sp, "wb") as f:
            f.write(bad)
        before = self.tree()
        r = self.run_init()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(self.p("CLAUDE.md")))
        self.assertIn(sp, r.stderr)
        self.assertRegex(r.stderr, r"line \d+")
        self.assertRegex(r.stderr, r"column \d+")
        self.assertIn("not valid JSON", r.stderr)
        self.assertIn("Fix the file, then run init again.", r.stderr)

    def test_invalid_json_with_json_flag_exit_2_no_change(self):
        os.makedirs(self.p(".claude"))
        sp = self.p(".claude", "settings.json")
        with open(sp, "wb") as f:
            f.write(b'{\n  "hooks": {\n    "Stop": [,\n  }\n}\n')
        before = self.tree()
        r = self.run_init("--json")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(self.p("CLAUDE.md")))
        self.assertIn(sp, r.stderr)
        self.assertIn("not valid JSON", r.stderr)
        self.assertIn("Fix the file, then run init again.", r.stderr)


class TestSideEffects(InitBase):
    def test_no_new_files_beyond_context_and_settings(self):
        before = set(self.tree())
        self.run_json()
        new = set(self.tree()) - before
        self.assertEqual(new, {"CLAUDE.md", os.path.join(".claude", "settings.json")})
        self.assertTrue(os.path.isfile(os.path.join(self.root, "locks", "init.lock")))

    def test_codex_no_new_files_beyond_agents_md(self):
        self.run_json("--harness", "codex")
        self.assertEqual(set(self.tree()), {"AGENTS.md"})

    def test_human_output_last_line(self):
        r = self.run_init()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        self.assertTrue(lines, "no output")
        self.assertIn("Open agent sessions do not join", r.stdout)
        self.assertTrue(lines[-1].startswith("Open agent sessions do not join"), lines[-1])
        self.assertIn("rookery session new", r.stdout)

    def test_init_waits_for_the_lock(self):
        locks = os.path.join(self.root, "locks")
        os.makedirs(locks, exist_ok=True)
        fd = os.open(os.path.join(locks, "init.lock"), os.O_CREAT | os.O_RDWR)
        proc = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            proc = subprocess.Popen(self.args("--json"), env=self.env, cwd=self.tmp.name,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)
            time.sleep(0.7)
            self.assertIsNone(proc.poll(), "init ran while another process held the lock")
            self.assertFalse(os.path.exists(self.p("CLAUDE.md")))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        res = json.loads(out)
        self.assertIs(res["ok"], True)
        self.assertEqual(res["context_file"]["action"], "created")
        self.assertEqual(sorted(res["settings"]["added"]), sorted(EVENTS))

    def test_back_to_back_inits_one_group_one_section(self):
        procs = [subprocess.Popen(self.args("--json"), env=self.env, cwd=self.tmp.name,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
        res = [(p.communicate(timeout=60), p.returncode) for p in procs]
        for (o, e), rc in res:
            self.assertEqual(rc, 0, e)
        s = self.settings()
        for ev in EVENTS:
            self.assertEqual(len(s["hooks"][ev]), 1, ev)
            self.assertEqual(len(self.rookery_handlers(s, ev)), 1, ev)
        text = self.read("CLAUDE.md").decode()
        self.assertEqual(text.count(START), 1)
        self.assertEqual(text.count(END), 1)


class TestContextFileEdgeCases(InitBase):
    def test_symlinked_claude_md_keeps_link_and_updates_target(self):
        with open(self.p("AGENTS.md"), "wb") as f:
            f.write(b"# shared rules\n")
        os.symlink("AGENTS.md", self.p("CLAUDE.md"))
        out = self.run_json()
        self.assertEqual(out["context_file"]["action"], "inserted")
        self.assertTrue(os.path.islink(self.p("CLAUDE.md")))
        target = self.read("AGENTS.md")
        self.assertTrue(target.startswith(b"# shared rules\n"))
        self.assertEqual(target.count(START.encode()), 1)
        self.assertEqual(self.read("CLAUDE.md"), target)

    def test_non_utf8_claude_md_inserted_prefix_preserved(self):
        orig = b"# caf\xe9 notes\n"
        with open(self.p("CLAUDE.md"), "wb") as f:
            f.write(orig)
        out = self.run_json()
        self.assertEqual(out["context_file"]["action"], "inserted")
        new = self.read("CLAUDE.md")
        self.assertTrue(new.startswith(orig))
        self.assertEqual(new.count(START.encode()), 1)

    def test_malformed_markers_exit_2_no_change_names_path(self):
        bad = ("x\n" + START + "\n" + START + "\n" + END + "\n").encode()
        with open(self.p("CLAUDE.md"), "wb") as f:
            f.write(bad)
        before = self.tree()
        r = self.run_init()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.tree(), before)
        self.assertIn(self.p("CLAUDE.md"), r.stderr)

    def test_pi_and_omp_write_agents_md_only(self):
        for harness in ("pi", "omp"):
            with self.subTest(harness=harness):
                shutil.rmtree(self.dir)
                os.makedirs(self.dir)
                out = self.run_json("--harness", harness)
                self.assertIs(out["ok"], True)
                self.assertEqual(out["context_file"]["path"], self.p("AGENTS.md"))
                self.assertIsNone(out["settings"])
                self.assertEqual(set(self.tree()), {"AGENTS.md"})


def ev_cmd(event):
    return {"SessionStart": "session-start", "UserPromptSubmit": "prompt",
            "Stop": "stop"}[event]


if __name__ == "__main__":
    unittest.main()


class TestInstallSafety(InitBase):
    """Wave-13 audit: no partial install, and the root never lands in --dir."""

    @unittest.skipIf(os.geteuid() == 0, "root ignores file modes")
    def test_unwritable_settings_dir_changes_no_context_file(self):
        os.makedirs(self.p(".claude"))
        os.chmod(self.p(".claude"), 0o555)
        try:
            r = self.run_init("--json")
        finally:
            os.chmod(self.p(".claude"), 0o755)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse(os.path.exists(self.p("CLAUDE.md")))

    def test_relative_rookery_root_rejected(self):
        self.env["ROOKERY_ROOT"] = "rel"
        r = subprocess.run(self.args(), env=self.env, cwd=self.dir,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ROOKERY_ROOT", r.stderr)
        self.assertEqual(os.listdir(self.dir), [])

    def test_root_inside_dir_rejected(self):
        self.env["ROOKERY_ROOT"] = self.p("rk")
        r = self.run_init()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(os.listdir(self.dir), [])
