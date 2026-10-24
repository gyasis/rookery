"""T011: `rookery up TEAM_FILE --no-launch` builds root, workspaces and rows (US1)."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")
HAVE_GIT = shutil.which("git") is not None

TEAM = {
    "session": "auth-fix",
    "task": "Fix the token refresh race.",
    "agents": [
        {"name": "scout", "harness": "claude", "role": "reader"},
        {"name": "mapper", "harness": "claude", "role": "reader"},
    ],
}


def git(cwd, *a):
    subprocess.run(["git", "-C", cwd, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


@unittest.skipUnless(HAVE_GIT, "git is missing")
class UpBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.home = os.path.join(t, "home")
        os.makedirs(self.home)
        self.db = os.path.join(t, "r.db")
        self.root = os.path.join(t, "root")
        self.repo = os.path.join(t, "hostrepo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "test_one.py"), "w") as fh:
            fh.write("def test_a():\n    assert True\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.team = os.path.join(t, "team.json")
        self.write_team(TEAM)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        self.env.update(HOME=self.home, ROOKERY_DB=self.db, ROOKERY_ROOT=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write_team(self, spec):
        with open(self.team, "w") as fh:
            json.dump(spec, fh)

    def up(self, *args, cwd=None, env=None, code=0):
        r = subprocess.run([sys.executable, CLI, "up", *args], cwd=cwd or self.repo,
                           env=env or self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return r

    def count(self, table):
        if not os.path.exists(self.db):
            return 0
        c = sqlite3.connect(self.db)
        try:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            c.close()

    def porcelain(self):
        return subprocess.run(["git", "-C", self.repo, "status", "--porcelain"],
                              capture_output=True, text=True).stdout

    def test_two_readers_no_launch(self):
        r = self.up(self.team, "--no-launch", "--json")
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["root"], self.root)
        self.assertEqual([a["name"] for a in out["agents"]], ["scout", "mapper"])
        for a in out["agents"]:
            self.assertTrue(os.path.isdir(a["workspace"]))
            self.assertEqual(a["worktree"], None)
            self.assertEqual(a["launch"], "skipped (--no-launch)")
            self.assertTrue(os.path.isfile(os.path.join(a["workspace"], "_config", "settings.json")))
            self.assertTrue(os.path.isfile(os.path.join(a["workspace"], "CLAUDE.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "ROOKERY.md")))
        self.assertEqual(self.count("rookery_sessions"), 1 + 1)  # legacy + auth-fix
        self.assertEqual(self.count("identities"), 2)
        self.assertEqual(self.count("up_agents"), 2)
        self.assertFalse(os.path.commonpath([self.root, self.repo]) == self.repo)

    def test_text_output_and_without_flag(self):
        r = self.up(self.team, "--no-launch")
        self.assertIn("scout: made", r.stdout)
        self.assertIn("mapper: made", r.stdout)
        self.assertIn(os.path.join(self.root, "ROOKERY.md"), r.stdout)
        r = self.up(self.team, "--json")
        self.assertEqual(json.loads(r.stdout)["agents"][0]["launch"],
                         "not started: no launch yet")

    def test_host_repo_untouched(self):
        collect = lambda: subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=self.repo, capture_output=True, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        c0 = collect()
        before = self.porcelain()
        self.up(self.team, "--no-launch")
        self.assertEqual(self.porcelain(), before)
        self.assertEqual(self.porcelain(), "")
        if "No module named pytest" not in c0.stderr + c0.stdout:
            self.assertEqual(collect().stdout.splitlines()[:-1], c0.stdout.splitlines()[:-1])

    def test_home_files_not_written(self):
        self.up(self.team, "--no-launch")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude.json")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "settings.json")))

    def test_relative_root_refused(self):
        env = dict(self.env, ROOKERY_ROOT="rel/root")
        r = self.up(self.team, "--no-launch", env=env, code=2)
        self.assertIn("absolute", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "rel")))

    def test_root_inside_repo_refused(self):
        env = dict(self.env, ROOKERY_ROOT=os.path.join(self.repo, "rk"))
        self.up(self.team, "--no-launch", env=env, code=2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "rk")))
        self.assertEqual(self.porcelain(), "")

    def test_bad_team_changes_nothing(self):
        bad = json.loads(json.dumps(TEAM))
        bad["agents"][1]["role"] = "boss"
        self.write_team(bad)
        r = self.up(self.team, "--no-launch", code=2)
        self.assertIn("entry 1 (mapper)", r.stderr)
        self.assertFalse(os.path.exists(self.root))
        self.assertEqual(self.count("rookery_sessions"), 0)
        r = self.up(self.team, "--json", code=2)
        out = json.loads(r.stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out["errors"][0]["name"], "mapper")

    def test_url_names_join(self):
        r = self.up("http://x", code=2)
        self.assertIn("rookery join", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_not_in_git_repo(self):
        plain = os.path.join(os.path.dirname(self.repo), "plain")
        os.makedirs(plain)
        r = self.up(self.team, "--no-launch", cwd=plain, code=2)
        self.assertIn("git", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_linked_worktree_uses_host_project(self):
        wt = os.path.join(os.path.dirname(self.repo), "linked")
        git(self.repo, "worktree", "add", "-q", "-b", "side", wt)
        env = {k: v for k, v in self.env.items() if k != "ROOKERY_ROOT"}
        r = self.up(self.team, "--no-launch", "--json", cwd=wt, env=env)
        root = json.loads(r.stdout)["root"]
        self.assertEqual(root, os.path.join(self.home, ".rookery", "projects", "hostrepo"))

    def test_writer_repo_is_host(self):
        spec = json.loads(json.dumps(TEAM))
        spec["agents"].append({"name": "fixer", "harness": "claude", "role": "writer",
                               "task_id": "T1"})
        self.write_team(spec)
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        repo, task, binding = c.execute(
            "SELECT repo, task_id, binding_kind FROM up_agents WHERE role='writer'").fetchone()
        rd = c.execute("SELECT repo FROM up_agents WHERE role='reader'").fetchone()[0]
        c.close()
        self.assertEqual((repo, task, rd), (self.repo, "T1", None))
        self.assertEqual(binding, "herdr-pane")

    def test_ended_session_refused(self):
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        c.execute("UPDATE rookery_sessions SET state='ended' WHERE name='auth-fix'")
        c.commit()
        c.close()
        r = self.up(self.team, "--no-launch", code=2)
        self.assertIn("ended", r.stderr)

    # ---- audit fixes ----
    def other_repo(self, name="repob"):
        t = os.path.dirname(self.repo)
        r = os.path.join(t, name)
        os.makedirs(r)
        git(r, "init", "-q")
        git(r, "commit", "-q", "--allow-empty", "-m", "i")
        return r

    def test_session_hijack_refused(self):
        self.up(self.team, "--no-launch")
        rb = self.other_repo()
        env = dict(self.env, ROOKERY_ROOT=os.path.join(os.path.dirname(self.repo), "rootb"))
        r = self.up(self.team, "--no-launch", cwd=rb, env=env, code=2)
        self.assertIn("belongs to the project root", r.stderr)
        self.assertEqual(self.count("identities"), 2)
        self.assertFalse(os.path.exists(os.path.join(env["ROOKERY_ROOT"], "agents")))

    def test_reserved_session_names_refused(self):
        for bad in ("legacy", "rs-0123abcd"):
            self.write_team(dict(TEAM, session=bad))
            self.up(self.team, "--no-launch", code=2)

    def test_hand_made_session_adopted_once(self):
        subprocess.run([sys.executable, CLI, "session", "new", "--name", "auth-fix"],
                       env=self.env, check=True, capture_output=True)
        r = self.up(self.team, "--no-launch")
        self.assertIn("adopted session auth-fix", r.stdout)
        r = self.up(self.team, "--no-launch")
        self.assertNotIn("adopted", r.stdout)

    def test_second_run_reused_no_duplicates(self):
        self.up(self.team, "--no-launch")
        r = self.up(self.team, "--no-launch")
        self.assertIn("scout: reused", r.stdout)
        self.assertEqual((self.count("identities"), self.count("up_agents"),
                          self.count("rookery_sessions")), (2, 2, 2))

    def test_subfolder_cwd(self):
        sub = os.path.join(self.repo, "a", "b")
        os.makedirs(sub)
        r = self.up(self.team, "--no-launch", "--json", cwd=sub)
        self.assertEqual(json.loads(r.stdout)["root"], self.root)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_workspace_write_failure(self):
        d = os.path.join(self.root, "agents", "auth-fix")
        os.makedirs(d)
        os.chmod(d, 0o500)
        try:
            r = self.up(self.team, "--no-launch", code=2)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("Rookery saved the agents", r.stderr)
            r = self.up(self.team, "--no-launch", "--json", code=2)
            self.assertFalse(json.loads(r.stdout)["ok"])
        finally:
            os.chmod(d, 0o700)
        r = self.up(self.team, "--no-launch")
        self.assertIn("scout: reused", r.stdout)

    def test_busy_db_json(self):
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db, isolation_level=None)
        c.execute("BEGIN IMMEDIATE")
        try:
            r = self.up(self.team, "--no-launch", "--json", code=4)
        finally:
            c.execute("ROLLBACK")
            c.close()
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_unsafe_project_name(self):
        r = self.other_repo("my repo")
        env = {k: v for k, v in self.env.items() if k != "ROOKERY_ROOT"}
        r2 = self.up(self.team, "--no-launch", cwd=r, env=env, code=2)
        self.assertIn("not a safe project name", r2.stderr)
        self.assertNotIn("Traceback", r2.stderr)

    def test_bare_repo_linked_worktree(self):
        t = os.path.dirname(self.repo)
        bare = os.path.join(t, "proj.git")
        subprocess.run(["git", "clone", "-q", "--bare", self.repo, bare], check=True,
                       capture_output=True)
        wt = os.path.join(t, "bwt")
        git(bare, "worktree", "add", "-q", wt, "HEAD")
        env = {k: v for k, v in self.env.items() if k != "ROOKERY_ROOT"}
        r = self.up(self.team, "--no-launch", "--json", cwd=wt, env=env)
        self.assertEqual(json.loads(r.stdout)["root"],
                         os.path.join(self.home, ".rookery", "projects", "proj"))

    def test_root_holding_host_refused(self):
        env = dict(self.env, ROOKERY_ROOT=os.path.dirname(self.repo))
        r = self.up(self.team, "--no-launch", env=env, code=2)
        self.assertIn("must not hold the host repo", r.stderr)

    def test_changed_fields_noted(self):
        self.up(self.team, "--no-launch")
        spec = json.loads(json.dumps(TEAM))
        spec["agents"][0]["harness"] = "pi"
        self.write_team(spec)
        r = self.up(self.team, "--no-launch")
        self.assertIn("scout: changed harness", r.stdout)
        spec["agents"][0]["harness"] = "claude"
        self.write_team(spec)
        out = json.loads(self.up(self.team, "--no-launch", "--json").stdout)
        self.assertEqual(out["agents"][0]["changed"], ["harness"])
        self.assertEqual(out["agents"][1]["changed"], [])


if __name__ == "__main__":
    unittest.main()
