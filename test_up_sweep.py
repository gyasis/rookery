"""T018: `rookery up` starts the session sweep, and writes the screener-enabled binding (US5, FR-013, SC-009).
In-process: rookery_wake.spawn is recorded, so no real sweep process starts."""
import contextlib
import io
import json
import os
import sqlite3
import unittest
from unittest import mock

import test_up_build as tb


class UpSweep(tb.UpBuild):
    def run(self, result=None):
        if self._testMethodName.startswith("test_sw_"):
            return super().run(result)
        return None

    def setUp(self):
        super().setUp()
        import rookery_wake
        self.wake = rookery_wake
        self.spawns = []
        patches = [
            # fake herdr/tmux first on PATH: `up` launches in-process, so the real herdr would open real tabs
            mock.patch.dict(os.environ, {"HOME": self.home, "ROOKERY_DB": self.db,
                                         "ROOKERY_ROOT": self.root,
                                         "PATH": os.path.join(tb.HERE, "fixtures", "bin") + os.pathsep + os.environ["PATH"],
                                         "HERDR_RECORD": os.path.join(self.home, "herdr.log"),
                                         "ROOKERY_FIXTURE_LOG": os.path.join(self.home, "tmux.log")}),
            mock.patch.object(rookery_wake, "spawn", side_effect=self.fake_spawn),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("ROOKERY_WAKE", None)
        os.environ.pop("HERDR_WORKSPACE_ID", None)
        self.spawn_ok = True
        self.cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.cwd)

    def fake_spawn(self, args, log_root=None):
        self.spawns.append(list(args))
        return self.spawn_ok

    def run_up(self, *flags, code=0):
        import cli_workspace
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="cmd")
        cli_workspace.add_parser(sub)
        args = p.parse_args(["up", self.team, *flags])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                args.func(args)
                rc = 0
            except SystemExit as e:
                rc = e.code or 0
        self.assertEqual(rc, code, err.getvalue() + out.getvalue())
        return out.getvalue(), err.getvalue()

    def bindings(self, kind):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT owner_type, owner_id, session_id, value FROM bindings WHERE kind=?",
                             (kind,)).fetchall()
        finally:
            c.close()

    def test_sw_first_run_spawns_one(self):
        out, _ = self.run_up("--json")
        d = json.loads(out)
        self.assertEqual(d["sweep"], "started")
        self.assertEqual(self.spawns, [["sweep", "--session", d["session_id"], "--root", self.root]])

    def test_sw_text_line(self):
        out, _ = self.run_up()
        self.assertIn("Sweep: started", out)

    def test_sw_lock_held_is_running(self):
        d = json.loads(self.run_up("--json")[0])
        self.spawns.clear()
        lock = self.wake.try_lock(os.path.join(self.root, "postmaster", "lock",
                                               f"sweep-{d['session_id']}.lock"))
        self.assertIsNotNone(lock)
        self.addCleanup(lock.close)
        d2 = json.loads(self.run_up("--json")[0])
        self.assertEqual(d2["sweep"], "running")
        self.assertEqual(self.spawns, [])
        self.assertIn("Sweep: running", self.run_up()[0])

    def test_sw_no_launch_still_starts(self):
        d = json.loads(self.run_up("--no-launch", "--json")[0])
        self.assertEqual(d["sweep"], "started")
        self.assertEqual(len(self.spawns), 1)

    def test_sw_wake_off(self):
        os.environ["ROOKERY_WAKE"] = "off"
        d = json.loads(self.run_up("--json")[0])
        self.assertEqual(d["sweep"], "off")
        self.assertEqual(self.spawns, [])
        self.assertIn("Sweep: off", self.run_up()[0])

    def test_sw_spawn_false_is_failed_exit_0(self):
        self.spawn_ok = False
        out, err = self.run_up("--json")
        self.assertEqual(json.loads(out)["sweep"], "failed")
        self.assertIn("NOTE:", err)

    def spec(self, **extra):
        return dict(tb.TEAM, **extra)

    def test_sw_screener_true_one_binding(self):
        self.write_team(self.spec(screener=True))
        d = json.loads(self.run_up("--json")[0])
        self.run_up("--json")
        self.assertEqual(self.bindings("screener-enabled"),
                         [("session", d["session_id"], d["session_id"], "1")])

    def test_sw_screener_false_removes(self):
        self.write_team(self.spec(screener=True))
        self.run_up("--json")
        self.assertEqual(len(self.bindings("screener-enabled")), 1)
        self.write_team(self.spec(screener=False))
        self.run_up("--json")
        self.assertEqual(self.bindings("screener-enabled"), [])

    def test_sw_screener_absent_writes_none(self):
        self.run_up("--json")
        self.assertEqual(self.bindings("screener-enabled"), [])

    def test_sw_screener_not_bool_is_team_error(self):
        self.write_team(self.spec(screener="yes"))
        self.run_up("--json", code=2)
        self.assertEqual(self.spawns, [])


if __name__ == "__main__":
    unittest.main()
