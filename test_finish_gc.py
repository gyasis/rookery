"""T016: `rookery finish NAME` and `rookery gc` (US4, FR-022, FR-023, D13, D14)."""
import json
import os
import sqlite3
import subprocess
import sys
import unittest

import test_up_build as tb
import test_worktree as tw

HERE = tb.HERE
BRANCH = "agent/s1/coder/T1"


def out(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class FinishGc(unittest.TestCase):
    up = tb.UpBuild.up
    write_team = tb.UpBuild.write_team
    count = tb.UpBuild.count
    setUp = tb.UpBuild.setUp
    tearDown = tb.UpBuild.tearDown
    rows = tw.Worktree.rows
    sql = tw.Worktree.sql
    wt = tw.Worktree.wt

    def make(self, *agents):
        tb.git(self.repo, "config", "user.email", "t@t")
        tb.git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "aaa.txt"), "w") as fh:
            fh.write("one")
        tb.git(self.repo, "add", ".")
        tb.git(self.repo, "commit", "-q", "-m", "aaa")
        self.write_team(tw.team(*(agents or (tw.WRITER,))))
        self.up(self.team, "--no-launch")
        with open(os.path.join(self.wt(), "new.txt"), "w") as fh:
            fh.write("change")

    def cli(self, *args, stdin="", env=None, code=None):
        r = subprocess.run([sys.executable, tb.CLI, *args], cwd=self.repo, input=stdin,
                           env=env or self.env, capture_output=True, text=True, timeout=60)
        if code is not None:
            self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return r

    def fin(self, stdin="yes\nyes\nyes\n", env=None, code=None, *extra):
        return self.cli("finish", "coder", "--session", "s1", "--message", "do it", *extra,
                        stdin=stdin, env=env, code=code)

    def state(self):
        return [r[2] for r in self.rows()]

    def branches(self):
        return sorted(out("git", "-C", self.repo, "branch", "--format=%(refname:short)").split())

    def test_three_yes(self):
        self.make()
        r = self.fin(code=0)
        for n in (1, 2, 3):
            self.assertIn(f"Type yes to continue ({n} of 3)", r.stdout)
        self.assertIn("new.txt", r.stdout)
        self.assertEqual(self.state(), ["finished"])
        self.assertFalse(os.path.exists(self.wt()))
        self.assertIn(BRANCH, self.branches())
        self.assertEqual(out("git", "-C", self.repo, "show", "--stat", "--format=%s", BRANCH).splitlines()[0],
                         "do it")
        self.assertIn("new.txt", out("git", "-C", self.repo, "ls-tree", "--name-only", BRANCH))
        c = sqlite3.connect(self.db)
        self.assertIsNotNone(c.execute("SELECT finished_at FROM worktrees").fetchone()[0])
        c.close()

    def test_no_at_each_prompt(self):
        self.make()
        for stdin in ("no\n", "yes\nno\n", "yes\nyes\nno\n", "yes\n"):
            r = self.fin(stdin=stdin, code=2)
            self.assertEqual(self.state(), ["active"])
            self.assertTrue(os.path.isfile(os.path.join(self.wt(), "new.txt")))
            self.assertEqual(out("git", "-C", self.wt(), "log", "--oneline").count("\n"), 2)

    def test_brotundwasser_skips_prompts(self):
        self.make()
        r = self.fin(stdin="", env=dict(self.env, BROTUNDWASSER="1"), code=0)
        self.assertNotIn("Type yes", r.stdout)
        self.assertEqual(self.state(), ["finished"])

    def test_no_remote_says_so(self):
        self.make()
        r = self.fin(code=0)
        self.assertIn("no remote; branch kept local", r.stdout)

    def test_bare_origin_gets_push(self):
        bare = os.path.join(os.path.dirname(self.repo), "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        tb.git(self.repo, "remote", "add", "origin", bare)
        self.make()
        self.fin(code=0)
        self.assertIn(BRANCH, out("git", "-C", bare, "branch", "--format=%(refname:short)"))
        self.assertEqual(self.state(), ["finished"])

    def test_nothing_to_commit_still_finishes(self):
        self.make()
        os.remove(os.path.join(self.wt(), "new.txt"))
        r = self.fin(code=0)
        self.assertIn("nothing to commit", r.stdout)
        self.assertEqual(self.state(), ["finished"])

    def test_commit_failure_keeps_active(self):
        self.make()
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho blocked >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        r = self.fin(code=2)
        self.assertIn("commit", r.stderr)
        self.assertIn("blocked", r.stderr)
        self.assertEqual(self.state(), ["active"])
        self.assertTrue(os.path.isdir(self.wt()))

    def test_unknown_name_exits_2(self):
        self.make()
        r = self.cli("finish", "nobody", "--session", "s1", "--message", "m", code=2)
        self.assertTrue(r.stderr.strip())
        self.cli("finish", "coder", "--session", "nosuch", "--message", "m", code=2)

    def test_session_from_env(self):
        self.make()
        self.cli("finish", "coder", "--message", "m", stdin="yes\nyes\nyes\n",
                 env=dict(self.env, ROOKERY_SESSION="s1"), code=0)
        self.assertEqual(self.state(), ["finished"])

    def test_claimed_file_listed(self):
        self.make(tw.WRITER, dict(name="scout", harness="claude", role="reader"))
        c = sqlite3.connect(self.db)
        sid, aid = c.execute("SELECT session_id, agent_id FROM identities WHERE name='scout'").fetchone()
        c.execute("INSERT INTO claims VALUES(?,?,?,1,'now')", (sid, aid, "shared/*.txt"))
        c.commit()
        c.close()
        os.makedirs(os.path.join(self.wt(), "shared"))
        with open(os.path.join(self.wt(), "shared", "x.txt"), "w") as fh:
            fh.write("x")
        r = self.fin(stdin="no\n", code=2)
        self.assertIn("shared/x.txt", r.stdout)
        self.assertIn("scout", r.stdout)
        self.assertNotIn("new.txt claimed", r.stdout)

    def claim(self, owner, pattern):
        c = sqlite3.connect(self.db)
        sid, aid = c.execute("SELECT session_id, agent_id FROM identities WHERE name=?", (owner,)).fetchone()
        c.execute("INSERT INTO claims VALUES(?,?,?,1,'now')", (sid, aid, pattern))
        c.commit()
        c.close()

    SCOUT = dict(name="scout", harness="claude", role="reader")

    def test_modified_tracked_first_file_claimed(self):
        self.make(tw.WRITER, self.SCOUT)
        self.claim("scout", "aaa.txt")
        with open(os.path.join(self.wt(), "aaa.txt"), "w") as fh:
            fh.write("two")
        r = self.fin(stdin="no\n", code=2)
        self.assertIn("aaa.txt claimed by scout", r.stdout)

    def test_non_ascii_name_claimed(self):
        self.make(tw.WRITER, self.SCOUT)
        self.claim("scout", "\u00e9.txt")
        with open(os.path.join(self.wt(), "\u00e9.txt"), "w") as fh:
            fh.write("x")
        r = self.fin(stdin="no\n", code=2)
        self.assertIn("\u00e9.txt claimed by scout", r.stdout)

    def test_timeout_keeps_row_active(self):
        self.make()
        hook = os.path.join(self.repo, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\nsleep 3\n")
        os.chmod(hook, 0o755)
        r = self.fin(code=2, env=dict(self.env, ROOKERY_FINISH_TIMEOUT="1"))
        self.assertIn("finish stopped at", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.state(), ["active"])

    def test_bad_remote_then_fixed(self):
        bare = os.path.join(os.path.dirname(self.repo), "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        tb.git(self.repo, "remote", "add", "origin", os.path.join(os.path.dirname(self.repo), "nowhere.git"))
        self.make()
        r = self.fin(code=2)
        self.assertIn("finish stopped at push", r.stderr)
        self.assertEqual(self.state(), ["active"])
        self.assertTrue(os.path.isdir(self.wt()))
        self.assertEqual(out("git", "-C", self.repo, "log", "-1", "--format=%s", BRANCH).strip(), "do it")
        tb.git(self.repo, "remote", "set-url", "origin", bare)
        self.fin(code=0)
        self.assertEqual(self.state(), ["finished"])
        self.assertIn(BRANCH, out("git", "-C", bare, "branch", "--format=%(refname:short)"))

    def test_other_root_refused_and_not_listed(self):
        self.make()
        other = dict(self.env, ROOKERY_ROOT=os.path.join(os.path.dirname(self.root), "otherroot"))
        r = self.fin(stdin="yes\nyes\nyes\n", env=other, code=2)
        self.assertIn(f"The worktree {self.wt()} is not under this Rookery root.", r.stderr)
        self.assertEqual(self.state(), ["active"])
        self.sql("UPDATE worktrees SET state='failed'")
        self.assertEqual(json.loads(self.cli("gc", "--json", env=other, code=0).stdout), [])
        self.assertEqual(len(self.gc()), 1)

    def test_secret_and_big_files_warned(self):
        self.make()
        open(os.path.join(self.wt(), ".env"), "w").close()
        with open(os.path.join(self.wt(), "big.bin"), "wb") as fh:
            fh.truncate(5 * 1024 * 1024 + 1)
        for env, stdin in ((None, "no\n"), (dict(self.env, BROTUNDWASSER="1"), "")):
            r = self.fin(stdin=stdin, env=env)
            line = [x for x in r.stdout.splitlines() if x.startswith("Check these files before you commit:")]
            self.assertEqual(len(line), 1, r.stdout)
            self.assertIn(".env", line[0])
            self.assertIn("big.bin", line[0])
            self.assertNotIn("new.txt", line[0])
            if env is None:
                self.assertLess(r.stdout.index("Check these files"), r.stdout.index("Type yes"))
            else:
                break

    def test_claim_matches_shared(self):
        import rookery_worktree as w
        self.assertTrue(w.claim_matches("src/*.py", "src/a/b.py"))
        self.assertFalse(w.claim_matches("src/*.py", "doc/b.py"))

    def test_yes_must_be_exact(self):
        self.make()
        self.fin(stdin="YES\nyes\nyes\n", code=2)
        self.assertEqual(self.state(), ["active"])

    def test_brotundwasser_only_for_one(self):
        self.make()
        self.fin(stdin="", env=dict(self.env, BROTUNDWASSER="true"), code=2)
        self.assertEqual(self.state(), ["active"])

    def test_same_name_two_sessions(self):
        self.make()
        self.write_team(tw.team(session="s2"))
        self.up(self.team, "--no-launch")
        with open(os.path.join(self.wt("s2"), "new.txt"), "w") as fh:
            fh.write("c")
        self.cli("finish", "coder", "--session", "s2", "--message", "m", stdin="yes\nyes\nyes\n", code=0)
        self.assertEqual({r[0]: r[2] for r in self.rows()}, {self.wt(): "active", self.wt("s2"): "finished"})

    def test_json_is_one_object(self):
        self.make()
        r = self.cli("finish", "coder", "--session", "s1", "--message", "m", "--json",
                     stdin="yes\nyes\nyes\n", code=0)
        o = json.loads(r.stdout)
        self.assertTrue(o["ok"])

    def gc(self):
        r = self.cli("gc", "--json", code=0)
        return json.loads(r.stdout)

    def reasons(self):
        return {x["reason"] for x in self.gc()}

    def test_gc_empty_when_healthy(self):
        self.make()
        self.assertEqual(self.gc(), [])

    def test_gc_session_ended(self):
        self.make()
        self.cli("session", "end", "s1", code=0)
        (x,) = self.gc()
        self.assertEqual((x["reason"], x["session"], x["agent"], x["branch"], x["path"]),
                         ("session-ended", "s1", "coder", BRANCH, self.wt()))

    def test_gc_other_reasons(self):
        self.make()
        self.sql("UPDATE worktrees SET state='failed'")
        self.assertEqual(self.reasons(), {"failed"})
        self.sql("UPDATE worktrees SET state='finished'")
        self.assertEqual(self.reasons(), {"finished-path-exists"})
        self.sql("UPDATE worktrees SET state='pending', created_at='2020-01-01T00:00:00Z'")
        self.assertEqual(self.reasons(), {"pending-stale"})
        self.sql("UPDATE worktrees SET state='pending', created_at='2999-01-01T00:00:00Z'")
        self.assertEqual(self.reasons(), set())
        self.sql("UPDATE worktrees SET state='active'")
        tb.git(self.repo, "worktree", "remove", "--force", self.wt())
        self.assertEqual(self.reasons(), {"path-missing"})

    def test_gc_writes_nothing_and_skips_human_folders(self):
        self.make()
        human = os.path.join(self.root, "wt", "s1", "human")
        os.makedirs(human)
        self.sql("UPDATE worktrees SET state='failed'")

        def snap():
            files = sorted(os.path.join(d, f) for d, _, fs in os.walk(os.path.join(self.root, "wt"))
                           for f in fs)
            c = sqlite3.connect(self.db)
            rows = c.execute("SELECT * FROM worktrees").fetchall()
            c.close()
            return self.branches(), files, rows
        before = snap()
        listed = self.gc()
        self.assertEqual(snap(), before)
        self.assertEqual([x["path"] for x in listed], [self.wt()])
        self.assertTrue(os.path.isdir(human))
        r = self.cli("gc", code=0)
        self.assertIn("failed", r.stdout)
        self.assertNotIn("human", r.stdout)


if __name__ == "__main__":
    unittest.main()
