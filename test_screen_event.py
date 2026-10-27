#!/usr/bin/env python3
"""005 T014: `rookery screen run --session S --once` is one screening event (FR-010, FR-011, research R6).

    python3 -m unittest test_screen_event -v

The fake backend answers (no model call). The handler runs in this process so that screener.run can be wrapped.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
from unittest import mock

import cli_screen
import rookery
import rookery_wake
import screener
from test_hooks import Base, HERE

FIXTURE = os.path.join(HERE, "fixtures", "screener", "fake_t1.json")


def body(text):
    return json.dumps({"to": "researcher", "task": "T-7", "kind": "ask", "say": text, "status": "in-progress"},
                      separators=(",", ":"), sort_keys=True)


class Event(Base):
    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake)
        self._fake_env = os.environ.get("ROOKERY_SCREENER_FAKE")
        os.environ["ROOKERY_SCREENER_FAKE"] = self.fake
        self.sid = self.alpha["session_id"]
        self.n = 0

    def tearDown(self):
        if self._fake_env is None:
            os.environ.pop("ROOKERY_SCREENER_FAKE", None)
        else:
            os.environ["ROOKERY_SCREENER_FAKE"] = self._fake_env
        super().tearDown()

    def store(self):
        self.n += 1
        return rookery.send(self.conn, self.swe["agent_id"], self.researcher["agent_id"],
                            body("CASE-DELIVER n%d" % self.n), topic="T-7", session_id=self.sid, hold=False)

    def decided(self):
        return [r[0] for r in self.conn.execute("SELECT mail_id FROM screen_mail WHERE state='decided' ORDER BY mail_id")]

    def run_cli(self):
        args = argparse.Namespace(session="alpha", once=True, poll=300.0)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                cli_screen.cmd_run(args)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def lock_path(self):
        return os.path.join(self.root, "postmaster", "lock", f"screen-{self.sid}.lock")

    def test_one_pass_decides_the_mail(self):
        mid = self.store()
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.decided(), [mid])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_decisions WHERE mail_id=?",
                                           (mid,)).fetchone()[0], 1)
        fh = rookery_wake.try_lock(self.lock_path())   # the pass released the lock
        self.assertIsNotNone(fh)
        fh.close()

    def test_held_lock_prints_screen_busy_and_decides_nothing(self):
        self.store()
        fh = rookery_wake.try_lock(self.lock_path())
        self.assertIsNotNone(fh)
        try:
            code, out = self.run_cli()
        finally:
            fh.close()
        self.assertEqual(code, 0)
        self.assertIn("screen-busy", out)
        self.assertEqual(self.decided(), [])

    def test_mail_stored_during_the_pass_is_decided_in_round_two(self):
        first = self.store()
        real = screener.run
        calls, late = [], []

        def wrapped(**kw):
            calls.append(1)
            code = real(**kw)
            if len(calls) == 1:
                late.append(self.store())   # arrives after the pass looked: only round two can see it
            return code

        with mock.patch.object(screener, "run", wrapped):
            code, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.decided(), [first, late[0]])

    def test_loop_stops_at_three_rounds(self):
        self.store()
        calls = []

        def wrapped(**kw):
            calls.append(1)
            self.store()   # new undecided mail after every pass
            return 0

        with mock.patch.object(screener, "run", wrapped):
            code, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)

    def test_failed_pass_is_not_retried_in_the_event(self):
        # a pass that fails (no backend, bad config) returns 2: no second round in this event (wave 10 audit F1)
        self.store()
        calls = []

        def failing(**kw):
            calls.append(1)
            return 2
        with mock.patch.object(screener, "run", failing):
            code, _ = self.run_cli()
        self.assertEqual((code, len(calls)), (2, 1))

    def test_t1_off_stops_after_one_round(self):
        # with t1 off no pass can decide the mail: the recheck says "nothing to do" (wave 10 audit F1)
        self.store()
        os.makedirs(os.path.join(os.environ["HOME"], ".rookery"), exist_ok=True)
        with open(os.path.join(os.environ["HOME"], ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"checks": {"t1": "off"}}}, fh)
        calls = []

        def counting(**kw):
            calls.append(1)
            return 0
        with mock.patch.object(screener, "run", counting):
            self.run_cli()
        self.assertEqual(len(calls), 1)

    def test_bad_config_between_rounds_is_no_traceback(self):
        self.store()

        def breaks_config(**kw):
            os.makedirs(os.path.join(os.environ["HOME"], ".rookery"), exist_ok=True)
            with open(os.path.join(os.environ["HOME"], ".rookery", "config.json"), "w") as fh:
                fh.write("{not json")
            return 0
        with mock.patch.object(screener, "run", breaks_config):
            code, _ = self.run_cli()
        self.assertEqual(code, 0)

    def test_exit_code_is_the_last_round_code(self):
        with mock.patch.object(screener, "run", lambda **kw: 2):
            code, _ = self.run_cli()
        self.assertEqual(code, 2)

    def test_default_poll_is_300(self):
        parser = argparse.ArgumentParser()
        cli_screen.add_parser(parser.add_subparsers())
        self.assertEqual(parser.parse_args(["screen", "run"]).poll, 300.0)


if __name__ == "__main__":
    import unittest
    unittest.main()
