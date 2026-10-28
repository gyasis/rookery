#!/usr/bin/env python3
"""Feature 006: hookless wake through herdr, the session watcher interval, and kimi in rookery up.

    python3 -m unittest test_wake_hookless -v

A harness with no Rookery turn hook writes no state/turn.json. The wake step then reads the pane
state from herdr (FR-001..FR-006). Uses the fixtures/bin/herdr test double: HERDR_STATUS sets the
status that `agent list` reports for pane w1:p2. Never talks to a real herdr.
"""
import json
import os
import tempfile
import unittest

import cli_rookery_session
import cli_workspace
import postmaster as PM
import rookery_identity as RI
import rookery_team
import rookery_wake
import turn_block as TB
from test_wake import WakeBase


class HooklessBase(WakeBase):
    def setUp(self):
        super().setUp()
        import plugins
        self.plugins = plugins
        self._probes = dict(plugins._PROBES)
        self._loaded = plugins._loaded
        plugins._PROBES.clear()
        plugins._loaded = False
        plugins.load("plugin_herdr")
        os.environ["HERDR_ENV"] = "1"
        os.remove(os.path.join(TB.workspace(self.root, "alpha", "law-counsel"), "state", "turn.json"))

    def tearDown(self):
        self.plugins._PROBES.clear()
        self.plugins._PROBES.update(self._probes)
        self.plugins._loaded = self._loaded
        super().tearDown()

    def herdr(self, status):
        os.environ["HERDR_STATUS"] = status

    def events(self, outcome):
        return [x for x in self.log_lines() if x.get("outcome") == outcome]


class TestHerdrFallback(HooklessBase):
    def test_idle_pushes_with_source_herdr(self):
        self.herdr("idle")
        self.mail()
        self.loop()
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][:3], ["agent", "prompt", "w1:p2"])
        pushed = self.events("pushed")
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["source"], "herdr")

    def test_done_counts_as_idle(self):
        self.herdr("done")
        self.mail()
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_notice_names_session_and_node(self):
        self.herdr("idle")
        self.mail()
        self.loop()
        text = self.calls()[0][3]
        self.assertIn("ROOKERY_SESSION=alpha ROOKERY_NODE=law-counsel rookery inbox", text)
        self.assertLessEqual(len(text), TB.MAX_NOTICE_LEN)

    def test_working_is_busy_no_push(self):
        self.herdr("working")
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.has_log("no-idle-state"))

    def test_blocked_and_unknown_do_not_push(self):
        for status in ("blocked", "unknown"):
            with self.subTest(status=status):
                self.herdr(status)
                self.mail(topic="T-" + status)
                PM.wake_identities(self.conn, self.root, self.config)
                self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("no-idle-state"))

    def test_herdr_silent_is_no_idle_state(self):
        self.herdr("")                           # agent list prints nothing
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("no-idle-state"))

    def test_pane_with_no_agent_is_pane_gone(self):
        other = RI.new_identity(self.conn, self.sid, "gone")
        RI.bind(self.conn, self.sid, other["agent_id"], "herdr-pane", "w1:p9")
        self.herdr("idle")                       # herdr lists w1:p2 only
        self.mail(to=other)
        self.loop()
        self.assertEqual([c for c in self.calls() if c[2] == "w1:p9"], [])
        self.assertTrue(self.has_log("pane-gone"), self.log_lines())

    def test_probe_off_keeps_005_behaviour(self):
        self.herdr("idle")
        self.mail()
        PM.wake_identities(self.conn, self.root, dict(self.config, herdr_probe=False))
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("no-idle-state"))

    def test_turn_json_wins_over_herdr(self):
        self.set_state("law-counsel", "busy")    # a hooked harness: turn.json says busy
        self.herdr("idle")
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_hooked_notice_is_unchanged(self):
        self.set_state("law-counsel", "idle")
        self.herdr("idle")
        self.mail()
        self.loop()
        text = self.calls()[0][3]
        self.assertTrue(text.endswith(". Read it with: rookery inbox"), text)
        self.assertEqual(self.events("pushed")[0]["source"], "turn")


    def test_no_idle_state_names_the_herdr_state(self):
        # audit #4: spec US1-3 says the log gives the herdr state as reason
        self.herdr("blocked")
        self.mail()
        self.loop()
        rows = self.events("no-idle-state")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get("reason"), "herdr:blocked")

    def test_listed_pane_with_empty_status_is_not_pane_gone(self):
        # audit #6: herdr lists the pane but gives no status -> no-idle-state, not pane-gone
        import plugin_herdr
        real = plugin_herdr.H._run_json
        plugin_herdr.H._run_json = lambda *a, **k: {"result": {"agents": [
            {"pane_id": "w1:p2", "agent_status": ""}]}}
        try:
            self.mail()
            self.loop()
        finally:
            plugin_herdr.H._run_json = real
        self.assertFalse(self.has_log("pane-gone"), self.log_lines())
        self.assertTrue(self.has_log("no-idle-state"))


class TestDraftInBox(HooklessBase):
    """Feature 006, T011: the wake waits while the input box has text (user decision 2026-10-28)."""
    BOX = "old reply\n\u276f earlier prompt\n\u2500\u2500\u2500\n\u276f %s\n\u2500\u2500\u2500\n  status line"

    def setUp(self):
        super().setUp()
        self.herdr("idle")
        os.environ["HERDR_KIND"] = "claude"

    def tearDown(self):
        for k in ("HERDR_KIND", "HERDR_SCREEN", "HERDR_STATUS"):
            os.environ.pop(k, None)
        super().tearDown()

    def test_draft_skips_the_wake(self):
        os.environ["HERDR_SCREEN"] = self.BOX % "half a question"
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        rows = self.events("draft-in-box")
        self.assertEqual(len(rows), 1, self.log_lines())
        self.assertEqual(rows[0]["reason"], "15 chars")          # the length, never the text

    def test_empty_box_pushes(self):
        os.environ["HERDR_SCREEN"] = self.BOX % ""
        self.mail()
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_next_round_pushes_once_the_box_is_empty(self):
        os.environ["HERDR_SCREEN"] = self.BOX % "typing"
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        os.environ["HERDR_SCREEN"] = self.BOX % ""
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_dim_placeholder_is_empty(self):
        os.environ["HERDR_KIND"] = "codex"
        os.environ["HERDR_SCREEN"] = ("\x1b[1m\x1b[48;2;52;53;54m\u203a\x1b[0m\x1b[48;2;52;53;54m \x1b[0m"
                                       "\x1b[2m\x1b[48;2;52;53;54mAsk Codex to do anything\x1b[0m\n  footer")
        self.mail()
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_truecolor_codes_are_not_dim(self):
        # 48;2;r;g;b holds a 2 that is a color mode, not the dim attribute
        os.environ["HERDR_KIND"] = "codex"
        os.environ["HERDR_SCREEN"] = "\x1b[48;2;52;53;54m\u203a my draft\x1b[0m"
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("draft-in-box"))

    def test_kimi_box(self):
        os.environ["HERDR_KIND"] = "kimi"
        os.environ["HERDR_SCREEN"] = " \u256d\u2500\u2500\n \u2502 > draft text   \u2502\n \u2570\u2500\u2500"
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("draft-in-box"))

    def test_unknown_kind_or_no_input_line_pushes(self):
        for kind, screen in (("omp", "\u276f looks like a draft"), ("claude", "no input line here")):
            with self.subTest(kind=kind):
                os.environ["HERDR_KIND"] = kind
                os.environ["HERDR_SCREEN"] = screen
                self.mail(topic="T-" + kind)
                PM.wake_identities(self.conn, self.root, self.config)
        self.assertEqual(len(self.calls()), 2)

    def test_draft_wait_off(self):
        os.environ["HERDR_SCREEN"] = self.BOX % "half a question"
        self.mail()
        PM.wake_identities(self.conn, self.root, dict(self.config, draft_wait=False))
        self.assertEqual(len(self.calls()), 1)


class TestWatcherInterval(WakeBase):
    def write_config(self, **kw):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump(dict(self.config, **kw), fh)

    def test_session_binding_sets_interval(self):
        RI.set_session_binding(self.conn, self.sid, "sweep-s", "90")
        sleeps = []
        PM.run_session_sweep(self.root, self.config, "alpha", sleep=sleeps.append, rounds=2)
        self.assertEqual(sum(sleeps), 90)

    def test_binding_is_replaced_not_added(self):
        RI.set_session_binding(self.conn, self.sid, "sweep-s", "90")
        RI.set_session_binding(self.conn, self.sid, "sweep-s", "200")
        n = self.conn.execute("SELECT COUNT(*) FROM bindings WHERE session_id=? AND kind='sweep-s'",
                              (self.sid,)).fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(PM._session_sweep_s(self.conn, self.sid), 200)

    def test_set_sweep_s_raises_low_value_with_note(self):
        notes = cli_workspace._set_sweep_s(self.sid, 10)
        self.assertEqual(len(notes), 1)
        self.assertIn("60", notes[0])
        self.assertEqual(PM._session_sweep_s(self.conn, self.sid), 60)

    def test_sweep_flag_refuses_non_numbers(self):
        import argparse
        for bad in ("x", "nan", "inf", "0", "-5"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                cli_workspace._sweep_seconds(bad)
        self.assertEqual(cli_workspace._sweep_seconds("90"), 90.0)


    def test_raised_note_names_the_real_floor(self):
        # audit #2: the sweep's raised note said 300 s while it slept 60 s
        self.write_config(sweep_s=30)
        import io, contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            PM.run_session_sweep(self.root, RI.load_config(persist=False), "alpha",
                                 sleep=lambda s: None, rounds=1)
        self.assertIn("60", err.getvalue())
        self.assertNotIn("300", err.getvalue())
        rows = [x for x in self.log_lines() if x.get("outcome") == "poll-raised"]
        self.assertEqual(len(rows), 1)
        self.assertIn("60", rows[0]["reason"])

    def test_resume_without_flag_keeps_the_stored_interval(self):
        # audit #5 / FR-014: a resume with no flag keeps the session value over the team file
        self.assertEqual(cli_workspace._sweep_to_store(None, 300, stored=90), None)
        self.assertEqual(cli_workspace._sweep_to_store(None, 300, stored=None), 300)
        self.assertEqual(cli_workspace._sweep_to_store(45.0, 300, stored=90), 45.0)
        self.assertEqual(cli_workspace._sweep_to_store(None, None, stored=None), None)


class TestWatcherState(WakeBase):
    def test_running_when_lock_held(self):
        RI.end_session(self.conn, "alpha")       # the watcher state does not depend on the session state
        lock = rookery_wake.try_lock(os.path.join(self.root, "postmaster", "lock", f"sweep-{self.sid}.lock"))
        try:
            self.assertEqual(cli_rookery_session._watcher_state(self.conn, self.sid), "running")
        finally:
            lock.close()
        self.assertEqual(cli_rookery_session._watcher_state(self.conn, self.sid), "none")

    def test_end_creates_no_files(self):
        # audit #1: session end must not recreate a deleted root or drop a lock into another root
        import shutil
        shutil.rmtree(self.root)
        self.assertEqual(cli_rookery_session._watcher_state(self.conn, self.sid), "none")
        self.assertFalse(os.path.exists(self.root))

    def test_no_lock_file_is_none_and_writes_nothing(self):
        before = sorted(os.listdir(self.root))
        self.assertEqual(cli_rookery_session._watcher_state(self.conn, self.sid), "none")
        self.assertEqual(sorted(os.listdir(self.root)), before)

    def test_unknown_without_root(self):
        os.environ.pop("ROOKERY_ROOT")
        self.assertEqual(cli_rookery_session._watcher_state(self.conn, self.sid), "unknown")


class TestTeamFile(unittest.TestCase):
    def load(self, **extra):
        team = {"session": "t1", "agents": [{"name": "k", "harness": "kimi", "role": "reader"}]}
        team.update(extra)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(team, fh)
        try:
            return rookery_team.load(fh.name, RI.load_config(persist=False))
        finally:
            os.remove(fh.name)

    def test_kimi_harness_is_accepted(self):
        self.assertEqual(self.load()["agents"][0]["harness"], "kimi")
        self.assertEqual(RI.load_config(persist=False)["harness"]["kimi"]["cmd"], ["kimi"])

    def test_kimi_reaches_a_user_config_with_its_own_harness_table(self):
        # audit #3: an existing ~/.rookery/config.json harness table lacks kimi
        user = {"harness": {"claude": {"cmd": ["my-claude"]}}}
        merged = cli_workspace._up_config(user)
        self.assertEqual(merged["harness"]["claude"]["cmd"], ["my-claude"])   # the user's entry wins
        self.assertEqual(merged["harness"]["kimi"]["cmd"], ["kimi"])
        self.assertEqual(user["harness"], {"claude": {"cmd": ["my-claude"]}})  # input not changed

    def test_codex_still_waits_for_r19(self):
        with self.assertRaises(rookery_team.TeamError) as cm:
            self.load(agents=[{"name": "c", "harness": "codex", "role": "reader"}])
        self.assertIn("R19", str(cm.exception.errors))

    def test_sweep_s_must_be_a_positive_number(self):
        self.assertEqual(self.load(sweep_s=90)["sweep_s"], 90)
        for bad in ("90", True, 0, -1):
            with self.subTest(bad=bad), self.assertRaises(rookery_team.TeamError):
                self.load(sweep_s=bad)


if __name__ == "__main__":
    unittest.main()
