"""T015: `rookery up` makes one git worktree per writer (US4, FR-020, FR-021, FR-021a, FR-024)."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import unittest

import test_up_build as tb

HERE = tb.HERE
WRITER = dict(name="coder", harness="claude", role="writer", task_id="T1")


def team(*agents, session="s1"):
    return {"session": session, "task": "t", "agents": list(agents) or [WRITER]}


def out(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class Worktree(unittest.TestCase):
    up = tb.UpBuild.up
    write_team = tb.UpBuild.write_team
    count = tb.UpBuild.count
    porcelain = tb.UpBuild.porcelain
    setUp = tb.UpBuild.setUp
    tearDown = tb.UpBuild.tearDown

    def go(self, spec, code=0):
        self.write_team(spec)
        r = self.up(self.team, "--no-launch", "--json", code=code)
        return r, (json.loads(r.stdout) if code == 0 else None)

    def rows(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT path, branch, state FROM worktrees").fetchall()
        finally:
            c.close()

    def wt(self, session="s1", name="coder"):
        return os.path.join(self.root, "wt", session, name, "hostrepo")

    def test_path_and_branch(self):
        _, o = self.go(team())
        p = self.wt()
        self.assertEqual(o["agents"][0]["worktree"], p)
        self.assertTrue(os.path.isfile(os.path.join(p, "test_one.py")))
        self.assertEqual(out("git", "-C", p, "rev-parse", "--abbrev-ref", "HEAD").strip(), "agent/s1/coder/T1")
        self.assertEqual(self.rows(), [(p, "agent/s1/coder/T1", "active")])

    def test_existing_branch_keeps_commit(self):
        tb.git(self.repo, "branch", "agent/s1/coder/T1")
        tb.git(self.repo, "commit", "-q", "--allow-empty", "-m", "later")
        old = out("git", "-C", self.repo, "rev-parse", "agent/s1/coder/T1").strip()
        self.go(team())
        self.assertEqual(out("git", "-C", self.wt(), "rev-parse", "HEAD").strip(), old)
        self.assertEqual(out("git", "-C", self.repo, "rev-parse", "agent/s1/coder/T1").strip(), old)

    def test_human_folder_refuses_session(self):
        human = os.path.join(self.root, "wt", "s1")
        os.makedirs(os.path.join(human, "mine"))
        self.write_team(team())
        r = self.up(self.team, "--no-launch", code=2)
        self.assertIn(f"The folder {human} exists and Rookery did not make it. Choose a new session name.",
                      r.stderr)
        self.assertEqual(os.listdir(human), ["mine"])
        for t in ("worktrees", "identities", "up_agents"):
            self.assertEqual(self.count(t), 0, t)

    def test_host_repo_unchanged(self):
        def collect():
            lines = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=self.repo,
                                   capture_output=True, text=True).stdout.strip().splitlines()
            return lines[:-1]
        tests = collect()      # pytest itself writes __pycache__, so measure after one collect
        before = self.porcelain()
        self.go(team())
        self.assertEqual(self.porcelain(), before)
        self.assertEqual(collect(), tests)

    def test_git_failure_then_fix(self):
        p = self.wt()
        self.go(team())
        tb.git(self.repo, "worktree", "remove", "--force", p)
        with open(p, "w") as fh:
            fh.write("in the way")
        _, o = self.go(team())
        self.assertFalse(o["ok"])
        self.assertTrue(o["agents"][0]["launch"].startswith("not started: "), o["agents"][0]["launch"])
        self.assertNotIn("Preparing worktree", o["agents"][0]["launch"])
        self.assertIsNone(o["agents"][0]["worktree"])
        self.assertEqual([r[2] for r in self.rows()], ["failed"])
        self.assertTrue(os.path.isfile(p))
        n = self.count("worktrees")
        os.remove(p)
        _, o = self.go(team())
        self.assertTrue(o["ok"])
        self.assertEqual([r[2] for r in self.rows()], ["active"])
        self.assertTrue(os.path.isdir(os.path.join(p, ".git")) or os.path.isfile(os.path.join(p, ".git")))
        self.assertGreaterEqual(self.count("worktrees"), n)

    def test_repo_without_commits_fails_then_fixed(self):
        empty = os.path.join(os.path.dirname(self.repo), "empty")
        os.makedirs(empty)
        tb.git(empty, "init", "-q")
        w = dict(WRITER, repo=empty)
        self.write_team(team(w))
        # Team validation accepts a repo with no commits, so the build reaches git and git fails.
        r = subprocess.run([sys.executable, tb.CLI, "up", self.team, "--no-launch", "--json"], cwd=self.repo,
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        o = json.loads(r.stdout)
        self.assertTrue(o["agents"][0]["launch"].startswith("not started: "))
        self.assertEqual([x[2] for x in self.rows()], ["failed"])
        with open(os.path.join(empty, "f"), "w") as fh:
            fh.write("x")
        tb.git(empty, "add", ".")
        tb.git(empty, "commit", "-q", "-m", "init")
        self.up(self.team, "--no-launch")
        self.assertEqual([x[2] for x in self.rows()], ["active"])

    def test_second_run_reuses(self):
        self.go(team())
        first = self.rows()
        _, o = self.go(team())
        self.assertEqual(self.rows(), first)
        self.assertEqual(o["agents"][0]["worktree"], self.wt())
        self.assertTrue(o["ok"])

    def test_no_forbidden_names(self):
        bad = re.compile(r"GIT_(DIR|WORK_TREE)\s*[\]\"']?\s*=|--git-" + "dir|--work-" + "tree|LD_" + "PRELOAD|over"
                         + "lay|node_" + "modules|symlink", re.I)
        for f in ("rookery_worktree.py", "cli_workspace.py"):
            with open(os.path.join(HERE, f)) as fh:
                m = bad.search(fh.read())
                self.assertIsNone(m, f"{f}: {m and m.group(0)}")

    def second(self, spec, **kw):
        self.write_team(spec)
        r = self.up(self.team, "--no-launch", "--json", **kw)
        return json.loads(r.stdout)

    def test_switched_branch_and_detached_stay_active(self):
        self.go(team())
        p = self.wt()
        for cmd in (("checkout", "-q", "-b", "other"), ("checkout", "-q", "--detach")):
            tb.git(p, *cmd)
            o = self.second(team())
            self.assertTrue(o["ok"], o)
            self.assertFalse(o["agents"][0]["launch"].startswith("not started"))
            self.assertEqual([r[2] for r in self.rows()], ["active"])
            self.assertTrue(any("coder: the worktree is on" in n and "agent/s1/coder/T1" in n
                                for n in o["notes"]), o["notes"])

    def test_inherited_git_dir_ignored(self):
        other = os.path.join(os.path.dirname(self.repo), "other")
        os.makedirs(other)
        tb.git(other, "init", "-q")
        tb.git(other, "commit", "-q", "--allow-empty", "-m", "x")
        env = dict(self.env, GIT_DIR=os.path.join(other, ".git"))
        self.write_team(team())
        self.up(self.team, "--no-launch", "--json", env=env)
        self.assertIn("agent/s1/coder/T1", out("git", "-C", self.repo, "branch", "--list", "agent/*"))
        self.assertEqual(out("git", "-C", other, "branch", "--list", "agent/*").strip(), "")

    def test_unowned_agent_folder_refused(self):
        self.go(team())
        two = team(WRITER, dict(WRITER, name="other", task_id="T2"))
        for fill in (True, False):
            d = os.path.join(self.root, "wt", "s1", "other", "hostrepo")
            os.makedirs(d)
            if fill:
                open(os.path.join(d, "mine"), "w").close()
            self.write_team(two)
            r = self.up(self.team, "--no-launch", code=2)
            self.assertIn(f"The folder {d} exists and Rookery did not make it. "
                          "Move it, or rename the agent.", r.stderr)
            self.assertEqual(self.count("identities"), 1)
            self.assertEqual(self.count("worktrees"), 1)
            shutil_rm = __import__("shutil").rmtree
            shutil_rm(os.path.join(self.root, "wt", "s1", "other"))

    def test_symlink_session_folder_refused(self):
        os.makedirs(os.path.join(self.root, "wt"))
        link = os.path.join(self.root, "wt", "s1")
        os.symlink("/nonexistent-rookery-target", link)
        self.write_team(team())
        r = self.up(self.team, "--no-launch", code=2)
        self.assertIn("did not make it", r.stderr)
        self.assertEqual(self.count("worktrees"), 0)

    def sql(self, q, *a):
        c = sqlite3.connect(self.db)
        try:
            c.execute(q, a)
            c.commit()
        finally:
            c.close()

    def test_task_id_changed_keeps_branch(self):
        self.go(team())
        o = self.second(team(dict(WRITER, task_id="T2")))
        self.assertTrue(any("coder: task_id changed; the worktree stays on agent/s1/coder/T1. "
                            "Run rookery finish first." in n for n in o["notes"]), o["notes"])
        self.assertEqual(self.rows(), [(self.wt(), "agent/s1/coder/T1", "active")])
        self.assertEqual(out("git", "-C", self.repo, "branch", "--list", "agent/s1/coder/T2").strip(), "")

    def test_repo_mismatch_refused(self):
        self.go(team())
        self.sql("UPDATE worktrees SET repo=?", "/somewhere/else")
        o = self.second(team())
        self.assertEqual(o["agents"][0]["launch"], "not started: the worktree belongs to /somewhere/else")
        self.assertFalse(o["ok"])
        self.assertEqual([r[2] for r in self.rows()], ["active"])

    def test_finished_row_starts_new_worktree(self):
        self.go(team())
        tb.git(self.repo, "worktree", "remove", "--force", self.wt())
        self.sql("UPDATE worktrees SET state='finished', finished_at='now'")
        o = self.second(team())
        self.assertTrue(o["ok"], o)
        self.assertTrue(any("coder: starts a new worktree; the old branch stays" in n for n in o["notes"]))
        self.assertEqual([r[2] for r in self.rows()], ["active"])
        self.assertTrue(os.path.isdir(self.wt()))


if __name__ == "__main__":
    unittest.main()
