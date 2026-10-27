"""T014: agents started by `rookery up` send mail with their own identity (US2, SC-004, SC-005, D2, FR-030)."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import test_up_build as tb

HERE = tb.HERE
FIXBIN = os.path.join(HERE, "fixtures", "bin")
TEAM = {"session": "e2e", "task": "t", "agents": [
    {"name": "a", "harness": "claude", "role": "reader"},
    {"name": "b", "harness": "claude", "role": "reader"}]}


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class UpE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.home = os.path.join(t, "home")
        os.makedirs(os.path.join(self.home, ".rookery"))
        self.db = os.path.join(t, "r.db")
        self.root = os.path.join(t, "root")
        self.repo = os.path.join(t, "hostrepo")
        os.makedirs(self.repo)
        tb.git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "f.txt"), "w") as fh:
            fh.write("x")
        tb.git(self.repo, "add", ".")
        tb.git(self.repo, "commit", "-q", "-m", "init")
        self.team = os.path.join(t, "team.json")
        self.log = os.path.join(t, "fixture.log")
        with open(os.path.join(HERE, "rookery_config.default.json")) as fh:
            cfg = json.load(fh)
        cfg["launch"]["fake-pane"] = {"env_flag": "--env", "steps": [
            {"argv": [os.path.join(FIXBIN, "fakepane"), "--cwd", "{cwd}", "{env}", "--", "{cmd}"],
             "capture": "^pane:(\\S+)$"}]}
        cfg["default_binding"] = "fake-pane"
        cfg["harness"]["claude"]["cmd"] = [os.path.join(FIXBIN, "fakeagent")]
        with open(os.path.join(self.home, ".rookery", "config.json"), "w") as fh:
            json.dump(cfg, fh)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("ROOKERY_", "FAKEAGENT_"))}
        self.env["ROOKERY_WAKE"] = "off"   # no real detached sweep from non-sweep tests
        self.env.update(HOME=self.home, ROOKERY_DB=self.db, ROOKERY_ROOT=self.root,
                        ROOKERY_FIXTURE_LOG=self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def up(self, session="e2e", **extra):
        spec = dict(TEAM, session=session)
        with open(self.team, "w") as fh:
            json.dump(spec, fh)
        r = subprocess.run([sys.executable, os.path.join(HERE, "rookery_cli.py"), "up", self.team, "--json"],
                           cwd=self.repo, env=dict(self.env, **extra), capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"], r.stdout)
        return {a["name"]: a["agent_id"] for a in out["agents"]}, out

    def rows(self, sql, args=()):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def test_e2e_send_uses_agent_identity(self):
        ids, out = self.up(FAKEAGENT_SEND_TO="b")
        rows = self.rows("SELECT sender, recipient, session_id FROM inbox WHERE sender=? AND recipient=?",
                         (ids["a"], ids["b"]))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][2], out["session_id"])
        self.assertEqual(len(self.rows("SELECT 1 FROM inbox WHERE sender=?", (ids["a"],))), 1)

    def test_e2e_stop_hook_block(self):
        ids, out = self.up(session="e2e2", FAKEAGENT_BLOCK_TO="b")
        rows = self.rows("SELECT session_id FROM inbox WHERE sender=? AND recipient=?",
                         (ids["a"], ids["b"]))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][0], out["session_id"])

    def test_e2e_identity_show_with_recorded_env(self):
        ids, out = self.up()
        with open(self.log) as fh:
            recs = [json.loads(x) for x in fh if x.strip()]
        rec = next(r for r in recs if r["env"]["ROOKERY_NODE"] == ids["a"])
        env = dict(self.env, **rec["env"])
        r = subprocess.run([sys.executable, os.path.join(HERE, "rookery_cli.py"), "identity", "show", "--json"],
                           cwd=rec["cwd"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        text = json.dumps(doc)
        self.assertEqual(doc.get("name"), "a", text)
        self.assertIn(out["session_id"], text)
        # identity show has no root field: the root reaches the agent as ROOKERY_ROOT
        self.assertEqual(rec["env"]["ROOKERY_ROOT"], out["root"])

    def test_e2e_gate_log_has_decisions(self):
        self.up(FAKEAGENT_SEND_TO="b")
        p = os.path.join(self.home, ".rookery", "logs", "gate.jsonl")
        self.assertTrue(os.path.exists(p), p)
        with open(p) as fh:
            self.assertTrue([x for x in fh if x.strip()])

    def test_e2e_no_claude_json(self):
        self.up(FAKEAGENT_SEND_TO="b")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude.json")))


if __name__ == "__main__":
    unittest.main()
