"""T013: `rookery up TEAM_FILE` starts each agent in a pane (US2, FR-008, FR-014 to FR-018)."""
import json
import os
import re
import sqlite3
import shlex
import sys
import tempfile
import unittest

import test_up_build as tb

HERE = tb.HERE
FIXBIN = os.path.join(HERE, "fixtures", "bin")


def team(*agents, session="auth-fix"):
    return {"session": session, "task": "t", "agents": list(agents)}


def agent(name, harness="claude", role="reader", binding="tmux-pane", **kw):
    return dict(name=name, harness=harness, role=role, binding=binding, **kw)


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class UpLaunch(unittest.TestCase):
    up = tb.UpBuild.up
    write_team = tb.UpBuild.write_team

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.home = os.path.join(t, "home")
        os.makedirs(self.home)
        self.db = os.path.join(t, "r.db")
        self.root = os.path.join(t, "root")
        self.repo = os.path.join(t, "hostrepo")
        os.makedirs(self.repo)
        tb.git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("x")
        tb.git(self.repo, "add", ".")
        tb.git(self.repo, "commit", "-q", "-m", "init")
        self.team = os.path.join(t, "team.json")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        self.env.update(HOME=self.home, ROOKERY_DB=self.db, ROOKERY_ROOT=self.root)
        self.log = os.path.join(self.home, "tmux.log")
        self.rec = os.path.join(self.home, "herdr.log")
        self.env.update(PATH=FIXBIN + os.pathsep + self.env["PATH"],
                        ROOKERY_FIXTURE_LOG=self.log, HERDR_RECORD=self.rec)

    def tearDown(self):
        self.tmp.cleanup()

    def calls(self, path=None):
        path = path or self.log
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def config(self, cfg):
        d = os.path.join(self.home, ".rookery")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as fh:
            json.dump(cfg, fh)

    def bindings(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT kind, value FROM bindings WHERE owner_type='identity' "
                             "ORDER BY rowid").fetchall()
        finally:
            c.close()

    def go(self, spec, *args, env=None):
        self.write_team(spec)
        r = self.up(self.team, "--json", *args, env=env)
        return r, json.loads(r.stdout)

    def new_window(self):
        return [c for c in self.calls() if c[:1] == ["new-window"]]

    def test_launch_tmux_argv(self):
        r, out = self.go(team(agent("scout")))
        a = out["agents"][0]
        self.assertEqual(a["launch"], "started")
        self.assertEqual(a["binding"], "tmux-pane:%0")
        self.assertTrue(a["first_start_dialogs"])
        (argv,) = self.new_window()
        self.assertEqual(argv[:8], ["new-window", "-d", "-P", "-F", "#{pane_id}", "-n", "auth-fix/scout", "-c"])
        self.assertEqual(argv[8], a["workspace"])
        env = {}
        i = 9
        while argv[i] == "-e":
            k, _, v = argv[i + 1].partition("=")
            env[k] = v
            i += 2
        self.assertEqual(env["ROOKERY_NODE"], a["agent_id"])
        self.assertEqual(env["ROOKERY_SESSION"], out["session_id"])
        self.assertEqual(env["ROOKERY_ROOT"], self.root)
        self.assertEqual(env["ROOKERY_PROJECT_ROOT"], self.repo)
        self.assertEqual(sorted(json.loads(env["ROOKERY_PORTS"])), ["api", "web"])
        self.assertEqual(env["ROOKERY_DB"], self.db)
        for k in ("ROOKERY_ROOT", "ROOKERY_PROJECT_ROOT", "ROOKERY_DB"):
            self.assertTrue(os.path.isabs(env[k]))
        ws = a["workspace"]
        self.assertEqual(argv[i:], ["claude", "--settings", os.path.join(ws, "_config", "settings.json"),
                                    "--add-dir", self.repo])
        self.assertIn(("tmux-pane", "%0"), self.bindings())

    def test_launch_first_start_text(self):
        self.write_team(team(agent("scout")))
        r = self.up(self.team)
        self.assertIn("scout: made; started", r.stdout)
        self.assertIn("First start: accept the trust and import dialogs in pane %0.", r.stdout)

    def test_launch_herdr(self):
        r, out = self.go(team(agent("scout", binding="herdr-pane")))
        a = out["agents"][0]
        self.assertEqual(a["binding"], "herdr-pane:h0")
        rows = self.calls(self.rec)
        self.assertEqual(rows[0][:3], ["tab", "create", "--cwd"])
        self.assertEqual(rows[0][3], a["workspace"])
        self.assertIn("--env", rows[0])
        self.assertEqual(rows[1][:3], ["pane", "run", "h0"])
        self.assertEqual(len(rows[1]), 4)
        self.assertTrue(rows[1][3].startswith("claude --settings "))
        self.assertIn("--settings", rows[1][3])
        self.assertIn(self.repo, rows[1][3])

    def test_launch_writer_and_pi(self):
        spec = team(agent("w", role="writer", task_id="T1"),
                    agent("p", harness="pi", role="writer", task_id="T2"),
                    agent("pr", harness="pi"))
        r, out = self.go(spec)
        by = {a["name"]: a for a in out["agents"]}
        w, p, pr = self.new_window()
        self.assertIn("--settings", w)
        ws = by["w"]["workspace"]
        j = w.index("--append-system-prompt-file")
        self.assertEqual(w[j + 1], os.path.join(ws, "_config", "system-prompt.md"))
        self.assertEqual(w[w.index("--add-dir") + 1], ws)
        self.assertEqual(w[-1], os.path.join(self.root, "_directory"))
        self.assertEqual(p[p.index("pi"):p.index("pi") + 2], ["pi", "--append-system-prompt"])
        with open(os.path.join(by["p"]["workspace"], "_config", "system-prompt.md")) as fh:
            self.assertEqual(p[-1], fh.read())
        self.assertEqual(pr[-1], "pi")
        self.assertFalse(by["p"]["first_start_dialogs"])
        self.assertTrue(by["w"]["first_start_dialogs"])

    def test_launch_alive_pane_skipped(self):
        self.go(team(agent("scout")))
        env = dict(self.env, ROOKERY_FIXTURE_ALIVE="%0")
        r, out = self.go(team(agent("scout")), env=env)
        self.assertEqual(out["agents"][0]["launch"], "running")
        self.assertEqual(out["agents"][0]["binding"], "tmux-pane:%0")
        self.assertFalse(out["agents"][0]["first_start_dialogs"])
        self.assertEqual(len(self.new_window()), 1)
        self.assertTrue(out["ok"])

    def test_launch_dead_pane_restarts(self):
        self.go(team(agent("scout")))
        r, out = self.go(team(agent("scout")))
        self.assertEqual(out["agents"][0]["launch"], "started")
        self.assertEqual(len(self.new_window()), 2)

    def test_launch_failing_step_isolated(self):
        with open(os.path.join(HERE, "rookery_config.default.json")) as fh:
            tmux = json.load(fh)["launch"]["tmux-pane"]
        self.config({"launch": {"bad-pane": {"env_flag": "-e", "steps": [{"argv": ["false"]}]},
                                "tmux-pane": tmux}})
        r, out = self.go(team(agent("a", binding="bad-pane"), agent("b")))
        a, b = out["agents"]
        self.assertTrue(a["launch"].startswith("not started: step 1 exited 1"), a["launch"])
        self.assertEqual(b["launch"], "started")
        self.assertFalse(out["ok"])

    def test_launch_no_match(self):
        self.config({"launch": {"x-pane": {"env_flag": "-e", "steps": [
            {"argv": ["echo", "hello"], "capture": "^pane:(\\S+)$"}]}}})
        r, out = self.go(team(agent("a", binding="x-pane")))
        self.assertEqual(out["agents"][0]["launch"], "not started: no pane ID in step 1 output: hello")

    def default_cfg(self):
        with open(os.path.join(HERE, "rookery_config.default.json")) as fh:
            return json.load(fh)

    def tmux_cfg(self):
        return self.default_cfg()["launch"]["tmux-pane"]

    def one(self, launch, binding="x-pane", **cfg):
        self.config(dict(launch=launch, **cfg))
        r, out = self.go(team(agent("a", binding=binding), agent("b", binding="tmux-pane")))
        return out["agents"][0], out

    def test_launch_bad_capture_regex(self):
        for cap in ("([", "^pane$"):
            a, out = self.one({"x-pane": {"env_flag": "-e", "steps": [
                {"argv": ["echo", "p"], "capture": cap}]}, "tmux-pane": self.tmux_cfg()})
            self.assertTrue(a["launch"].startswith("not started: "), a["launch"])
            self.assertIn("x-pane", a["launch"])
            self.assertEqual(out["agents"][1]["launch"], "started")

    def test_launch_bad_capture_regex_text(self):
        a, _ = self.one({"x-pane": {"env_flag": "-e", "steps": [
            {"argv": ["echo", "p"], "capture": "(["}]}, "tmux-pane": self.tmux_cfg()})
        self.assertTrue(a["launch"].startswith("not started: invalid launch config for x-pane: "))

    def test_launch_malformed_step(self):
        for step in ({}, {"argv": "echo"}, "echo"):
            a, out = self.one({"x-pane": {"env_flag": "-e", "steps": [step]},
                               "tmux-pane": self.tmux_cfg()})
            self.assertTrue(a["launch"].startswith("not started: invalid launch config for x-pane: "),
                            a["launch"])
            self.assertEqual(out["agents"][1]["launch"], "started")

    def test_launch_step_fails_after_capture(self):
        a, out = self.one({"x-pane": {"env_flag": "-e", "steps": [
            {"argv": ["echo", "pane:p9"], "capture": "^pane:(\\S+)$"}, {"argv": ["false"]}]},
            "tmux-pane": self.tmux_cfg()})
        self.assertEqual(a["launch"], "not started: step 2 exited 1. Pane p9 is open. "
                                      "Close it, then run rookery up again.")
        self.assertNotIn(("x-pane", "p9"), self.bindings())

    def test_launch_no_env_flag(self):
        a, _ = self.one({"x-pane": {"steps": [{"argv": ["echo", "{env}"]}]},
                         "tmux-pane": self.tmux_cfg()})
        self.assertEqual(a["launch"], "not started: launch config for x-pane has no env_flag")

    def test_launch_alive_check_only_own_kind(self):
        self.go(team(agent("a")))                       # binds tmux-pane:%0
        cfg = self.default_cfg()
        cfg["launch"]["herdr-pane"]["steps"] = [{"argv": ["echo", "pane:h7"], "capture": "pane:(\\S+)"}]
        self.config(cfg)
        env = dict(self.env, ROOKERY_FIXTURE_ALIVE="%0")
        r, out = self.go(team(agent("a", binding="herdr-pane")), env=env)
        self.assertEqual(out["agents"][0]["launch"], "started")   # the tmux pane is not probed

    def test_launch_alive_probe_error(self):
        self.go(team(agent("a")))
        cfg = self.default_cfg()
        cfg["alive"]["tmux-pane"] = ["no-such-probe-tool", "{value}"]
        self.config(cfg)
        n = len(self.new_window())
        r, out = self.go(team(agent("a")))
        self.assertEqual(out["agents"][0]["launch"], "not started: Rookery cannot tell if pane %0 is alive")
        self.assertEqual(len(self.new_window()), n)

    def test_launch_db_path_absolute(self):
        env = dict(self.env, ROOKERY_DB="rel.db")
        os.makedirs(os.path.join(self.repo, "x"), exist_ok=True)
        self.write_team(team(agent("a")))
        r = self.up(self.team, "--json", env=env)
        (argv,) = self.new_window()
        vals = [v for v in argv if v.startswith("ROOKERY_DB=")]
        self.assertTrue(os.path.isabs(vals[0].split("=", 1)[1]), vals)

    def test_launch_herdr_joined_command(self):
        persona = '"a" $(x) \n b'
        r, out = self.go(team(agent("p", harness="pi", role="writer", binding="herdr-pane",
                                    task_id="T1", persona=persona)))
        rows = self.calls(self.rec)
        run = rows[1]
        self.assertEqual(run[:3], ["pane", "run", "h0"])
        self.assertEqual(len(run), 4)
        ws = out["agents"][0]["workspace"]
        with open(os.path.join(ws, "_config", "system-prompt.md")) as fh:
            self.assertEqual(run[3], shlex.join(["pi", "--append-system-prompt", fh.read()]))

    def test_launch_value_not_reexpanded(self):
        a, out = self.one({"x-pane": {"env_flag": "-e", "steps": [
            {"argv": ["echo", "pane:{cwd}"], "capture": "pane:(.*)"},
            {"argv": ["echo", "{pane}", "{label}"]}]}, "tmux-pane": self.tmux_cfg()})
        self.assertEqual(a["launch"], "started")
        self.assertEqual(a["binding"], "x-pane:" + a["workspace"])

    def test_launch_pane_before_capture(self):
        a, _ = self.one({"x-pane": {"env_flag": "-e", "steps": [{"argv": ["echo", "{pane}"]}]},
                         "tmux-pane": self.tmux_cfg()})
        self.assertEqual(a["launch"], "not started: step 1 uses {pane} before a capture")

    def test_launch_validation_uses_merged_config(self):
        self.config({"loop_limit": 10})
        self.write_team(team(agent("a", binding="zellij-pane")))
        self.up(self.team, "--json", code=2)

    def test_launch_default_binding_from_packaged(self):
        self.config({"launch": {"herdr-pane": {"env_flag": "--env", "steps": [
            {"argv": ["herdr", "tab", "create"], "capture": "\"pane_id\"\\s*:\\s*\"([^\"]+)\""}]}}})
        self.write_team(team({"name": "a", "harness": "pi", "role": "reader"}))
        r = self.up(self.team, "--json")
        self.assertEqual(json.loads(r.stdout)["agents"][0]["binding"], "herdr-pane:h0")

    def test_launch_no_launch_flag(self):
        r, out = self.go(team(agent("scout")), "--no-launch")
        self.assertEqual(self.calls(), [])
        self.assertEqual(out["agents"][0]["launch"], "skipped (--no-launch)")
        self.assertTrue(out["ok"])

    def wake(self, state):
        sys.path.insert(0, HERE)
        import postmaster as PM
        import rookery as R
        import rookery_identity as RI
        import turn_block as TB
        saved = dict(os.environ)
        r, out = self.go(team(agent("scout"), agent("sender")))
        os.environ.update(HOME=self.home, ROOKERY_DB=self.db, ROOKERY_ROOT=self.root,
                          PATH=self.env["PATH"], ROOKERY_FIXTURE_LOG=self.log)
        try:
            ws = out["agents"][0]["workspace"]
            if state:
                os.makedirs(os.path.join(ws, "state"), exist_ok=True)
                with open(os.path.join(ws, "state", "turn.json"), "w") as fh:
                    json.dump({"state": state}, fh)
            conn = TB.open_db()
            try:
                R.send(conn, out["agents"][1]["agent_id"], out["agents"][0]["agent_id"], "hi",
                       topic="T", session_id=out["session_id"])
                PM.wake_identities(conn, self.root, RI.load_config(persist=False))
            finally:
                conn.close()
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return [c for c in self.calls() if c[:1] == ["send-keys"]]

    def test_launch_wake_when_idle(self):
        sent = self.wake("idle")
        self.assertEqual(sent[0][:4], ["send-keys", "-t", "%0", "-l"])
        self.assertEqual(sent[1], ["send-keys", "-t", "%0", "Enter"])

    def test_launch_no_wake_without_turn_state(self):
        self.assertEqual(self.wake(None), [])

    def test_launch_no_tool_literals(self):
        for f in ("cli_workspace.py", "rookery_team.py", "rookery_views.py",
                  "rookery_worktree.py", "rookery_guard.py"):
            if not os.path.exists(os.path.join(HERE, f)):
                continue
            with open(os.path.join(HERE, f)) as fh:
                text = fh.read()
            self.assertIsNone(re.search(r"herdr|tmux", text), f)


if __name__ == "__main__":
    unittest.main()
