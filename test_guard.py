"""T019: `rookery hook guard` and `rookery hook watch` (US7, FR-029, D16, R9, R12)."""
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
import unittest

import cli_init
import rookery_views
import test_up_build as tb
import test_worktree as tw

HERE = tb.HERE
SCOUT = dict(name="scout", harness="claude", role="reader", claims=["shared/*.txt", "docs/*"])


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class Guard(unittest.TestCase):
    up = tb.UpBuild.up
    write_team = tb.UpBuild.write_team
    count = tb.UpBuild.count
    setUp = tb.UpBuild.setUp
    tearDown = tb.UpBuild.tearDown
    wt = tw.Worktree.wt

    def start(self, *agents):
        self.write_team(tw.team(tw.WRITER, *(agents or (SCOUT,))))
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        self.ids = dict(c.execute("SELECT name, agent_id FROM identities").fetchall())
        self.sid = c.execute("SELECT session_id FROM identities LIMIT 1").fetchone()[0]
        c.close()
        self.scratch = os.path.join(self.tmp.name, "scratch")
        os.makedirs(self.scratch, exist_ok=True)

    def env_for(self, name="coder", **extra):
        self.scratch = os.path.join(self.tmp.name, "scratch")
        os.makedirs(self.scratch, exist_ok=True)
        e = dict(self.env, ROOKERY_SESSION="s1", ROOKERY_NODE=self.ids[name], TMPDIR=self.scratch)
        e.update(extra)
        return e

    def hook(self, which, payload, env=None, raw=None):
        r = subprocess.run([sys.executable, tb.CLI, "hook", which, "--project", "p"],
                           input=raw if raw is not None else json.dumps(payload), cwd=HERE,
                           env=env or self.env_for(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def log_lines(self):
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def write(self, path, tool="Write", **extra):
        return self.hook("guard", dict(extra, tool_name=tool, tool_input={"file_path": path}))

    def ws(self, name="coder"):
        return os.path.join(self.root, "agents", "s1", name)

    def test_write_in_workspace_is_silent(self):
        self.start()
        self.assertEqual(self.write(os.path.join(self.ws(), "notes.md")), "")
        self.assertEqual(self.log_lines(), [])

    def deny_text(self, path):
        return (f"Rookery guard: {path} is outside your folders ({os.path.realpath(self.ws())}, "
                f"{os.path.realpath(self.wt())}). Write only in your own folders.")

    def test_write_outside_denied_r9(self):
        self.start()
        for path in (os.path.join(self.repo, "test_one.py"), os.path.join(self.ws("scout"), "n.md")):
            out = json.loads(self.write(path))
            self.assertEqual(out, {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": self.deny_text(os.path.realpath(path))}})
        self.assertEqual(len(self.log_lines()), 2)

    def test_guard_allow_default_tmp_and_claude(self):
        self.start()
        self.assertEqual(self.write(os.path.join(self.scratch, "x")), "")
        self.assertEqual(self.write(os.path.join(self.home, ".claude", "plans", "x.md")), "")
        self.assertEqual(self.log_lines(), [])

    def test_guard_allow_empty_denies_tmp(self):
        self.start()
        os.makedirs(os.path.join(self.home, ".rookery"))
        cfg = json.load(open(os.path.join(HERE, "rookery_config.default.json")))
        cfg["guard_allow"] = []
        with open(os.path.join(self.home, ".rookery", "config.json"), "w") as fh:
            json.dump(cfg, fh)
        self.assertIn("deny", self.write(os.path.join(self.scratch, "x")))

    def test_multiedit_checked(self):
        self.start()
        self.assertIn("deny", self.write(os.path.join(self.repo, "a"), tool="MultiEdit"))
        self.assertEqual(self.write(os.path.join(self.wt(), "a"), tool="MultiEdit"), "")

    def test_absent_agent_claims_deactivated(self):
        self.start()
        self.write_team(tw.team(tw.WRITER))
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT active FROM claims").fetchall()
        c.close()
        self.assertEqual(sorted(rows), [(0,), (0,)])

    def test_relative_path_uses_hook_cwd(self):
        self.start()
        self.assertEqual(self.write("a.txt", cwd=self.wt()), "")
        self.assertIn("deny", self.write("../../../../../../../../a.txt", cwd=self.wt()))

    def test_notebook_path_checked(self):
        self.start()
        out = self.hook("guard", {"tool_input": {"notebook_path": os.path.join(self.repo, "n.ipynb")}})
        self.assertIn("deny", out)

    def test_edit_in_worktree_is_silent(self):
        self.start()
        self.assertEqual(self.write(os.path.join(self.wt(), "test_one.py"), tool="Edit"), "")
        self.assertEqual(self.log_lines(), [])

    def test_symlink_escape_denied(self):
        self.start()
        os.symlink(self.repo, os.path.join(self.wt(), "esc"))
        self.assertIn("deny", self.write(os.path.join(self.wt(), "esc", "x")))

    def test_claimed_file_allowed_with_reason_r12(self):
        self.start()
        out = json.loads(self.write(os.path.join(self.wt(), "shared", "x.txt")))
        self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                         "Rookery claims: scout owns shared/*.txt. Tell that agent before you change it."}})
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual(self.log_lines()[0]["outcome"], "claimed")

    def test_own_claim_is_not_flagged(self):
        self.write_team(tw.team(dict(tw.WRITER, claims=["mine/*"]), SCOUT))
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        self.ids = dict(c.execute("SELECT name, agent_id FROM identities").fetchall())
        c.close()
        self.assertEqual(self.write(os.path.join(self.wt(), "mine", "x.txt")), "")
        self.assertEqual(self.log_lines(), [])

    def test_reader_without_worktree_denied_cleanly(self):
        self.start()
        out = self.hook("guard", {"tool_input": {"file_path": os.path.join(self.repo, "x")}}, env=self.env_for("scout"))
        self.assertIn(f"({os.path.realpath(self.ws('scout'))}). Write only", json.loads(out)
                      ["hookSpecificOutput"]["permissionDecisionReason"])

    def test_finish_lists_claimed_file_r12(self):
        self.start()
        os.makedirs(os.path.join(self.wt(), "shared"))
        with open(os.path.join(self.wt(), "shared", "x.txt"), "w") as fh:
            fh.write("x")
        r = subprocess.run([sys.executable, tb.CLI, "finish", "coder", "--session", "s1", "--message", "m"],
                           cwd=self.repo, input="no\n", env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn("shared/x.txt claimed by scout (shared/*.txt)", r.stdout)

    def test_no_identity_is_silent(self):
        self.start()
        clean = {k: v for k, v in self.env.items() if k not in ("ROOKERY_SESSION", "ROOKERY_NODE")}
        self.assertEqual(self.hook("guard", {"tool_input": {"file_path": "/tmp/x"}}, env=clean), "")
        self.assertEqual(self.hook("watch", {"tool_input": {"command": "echo x > /tmp/f"}}, env=clean), "")

    def test_bad_json_one_log_line(self):
        self.start()
        self.assertEqual(self.hook("guard", None, raw="{not json"), "")
        self.assertEqual(self.hook("watch", None, raw="[1]"), "")
        lines = self.log_lines()
        self.assertEqual([x["adapter"] for x in lines], ["guard", "watch"])
        self.assertTrue(all(x["outcome"] == "bad-input" for x in lines))

    def test_watch_foreign_write_r9(self):
        self.start()
        out = json.loads(self.hook("watch", {"cwd": self.wt(), "tool_input": {"command": "echo x > /tmp/foreign"}}))
        text = "Rookery watch: this command wrote /tmp/foreign, outside your folders."
        self.assertEqual(out, {"systemMessage": text, "hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": text}})
        self.assertEqual([x["outcome"] for x in self.log_lines()], ["watch-flag"])

    def test_watch_inside_worktree_is_silent(self):
        self.start()
        for cmd in ("echo x > out.txt", "echo x >out.txt", "echo x | tee -a out.txt", "touch a b",
                    "cp test_one.py copy.py", "echo x > /dev/null", "echo x 2>&1", "echo 'a > b'"):
            self.assertEqual(self.hook("watch", {"cwd": self.wt(), "tool_input": {"command": cmd}}), "", cmd)
        self.assertEqual(self.log_lines(), [])

    def test_watch_forms(self):
        self.start()
        for cmd, path in (("echo x >>/tmp/a1", "/tmp/a1"), ("echo x 2> /tmp/a2", "/tmp/a2"),
                          ("ls | tee /tmp/a3", "/tmp/a3"), ("cp -r src /tmp/a4", "/tmp/a4"),
                          ("mv a /tmp/a5", "/tmp/a5"), ("install -m 644 a /tmp/a6", "/tmp/a6"),
                          ("mkdir -p /tmp/a7", "/tmp/a7"), ("cd x && touch /tmp/a8", "/tmp/a8")):
            out = json.loads(self.hook("watch", {"cwd": self.wt(), "tool_input": {"command": cmd}}))
            self.assertIn(path, out["systemMessage"], cmd)
        out = json.loads(self.hook("watch", {"cwd": self.wt(), "tool_input": {"command": "touch /tmp/b1 /tmp/b2"}}))
        self.assertIn("/tmp/b1, /tmp/b2", out["systemMessage"])
        self.assertEqual(self.hook("watch", {"cwd": self.wt(), "tool_input": {"command": "echo 'unclosed > /tmp/q"}}), "")

    def test_guard_under_one_second_with_10000_rows(self):
        self.start()
        c = sqlite3.connect(self.db)
        c.executemany("INSERT INTO inbox(sender, recipient, body, topic, status, delivered, created_at, session_id) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      [(self.ids["scout"], self.ids["coder"], "{}", f"T{i % 40}", "done", 1, time.time() - i, self.sid)
                       for i in range(10000)])
        c.commit()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 10000)
        c.close()
        times = []
        for _ in range(3):
            t0 = time.monotonic()
            self.write(os.path.join(self.repo, "x"))
            times.append(time.monotonic() - t0)
        self.assertLess(statistics.median(times), 1.0, times)

    def test_settings_json_has_two_new_handlers(self):
        fresh = os.path.join(self.tmp.name, "fresh", ".claude", "settings.json")
        cli_init.merge_hooks(fresh, "app")
        out = os.path.join(self.tmp.name, "ws", "_config", "settings.json")
        rookery_views.settings_json(out, "app")
        before = open(out).read()
        rookery_views.settings_json(out, "app")
        self.assertEqual(open(out).read(), before)
        got, want = json.load(open(out)), json.load(open(fresh))
        for event, _sub in cli_init.HOOK_EVENTS:
            self.assertEqual(got["hooks"][event], want["hooks"][event])
        self.assertEqual(got["hooks"]["PreToolUse"], [{"matcher": "Write|Edit|MultiEdit|NotebookEdit", "hooks": [
            {"type": "command", "command": "rookery hook guard --project app"}]}])
        self.assertEqual(got["hooks"]["PostToolUse"], [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "rookery hook watch --project app"}]}])

    def test_dropped_claim_goes_inactive_and_is_ignored(self):
        self.start()
        target = os.path.join(self.wt(), "docs", "a.md")
        self.assertIn("additionalContext", self.write(target))
        self.write_team(tw.team(tw.WRITER, dict(SCOUT, claims=["shared/*.txt"])))
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        rows = dict(c.execute("SELECT pattern, active FROM claims").fetchall())
        c.close()
        self.assertEqual(rows, {"shared/*.txt": 1, "docs/*": 0})
        self.assertEqual(self.write(target), "")
        self.write_team(tw.team(tw.WRITER, dict(SCOUT, claims=["shared/*.txt", "docs/*"])))
        self.up(self.team, "--no-launch")
        self.assertEqual(self.count("claims"), 2)
        self.assertIn("additionalContext", self.write(target))


if __name__ == "__main__":
    unittest.main()
