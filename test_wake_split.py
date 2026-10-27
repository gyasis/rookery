#!/usr/bin/env python3
"""Rookery wake split (feature 005, T004): wake_one outcomes, type_notice, file-backed back-off.

    python3 -m unittest test_wake_split -v
"""
import json
import os
import subprocess
import unittest

import postmaster as PM
from test_wake import WakeBase


class Split(WakeBase):
    def ident(self, name="law-counsel"):
        return self.conn.execute(
            "SELECT i.agent_id, i.name, i.session_id, s.name AS sname FROM identities i "
            "JOIN rookery_sessions s ON s.session_id=i.session_id WHERE i.name=?", (name,)).fetchone()

    def one(self, **kw):
        return PM.wake_one(self.conn, self.root, self.config, self.ident(), **kw)

    def bofile(self):
        return os.path.join(self.root, "postmaster", "backoff", self.lc["agent_id"] + ".json")

    def failing(self, sent):
        def run(argv, **kw):
            sent.append(argv)
            return subprocess.CompletedProcess(argv, 1)
        return run


class TestOutcomes(Split):
    def test_nothing_without_mail(self):
        self.assertEqual(self.one(), "nothing")

    def test_pushed(self):
        self.mail()
        self.assertEqual(self.one(), "pushed")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.one(), "nothing")

    def test_busy(self):
        self.set_state("law-counsel", "busy")
        self.mail()
        self.assertEqual(self.one(), "busy")
        self.assertEqual(self.calls(), [])

    def test_no_idle_state(self):
        os.remove(os.path.join(self.root, "agents", "alpha", "law-counsel", "state", "turn.json"))
        self.mail()
        self.assertEqual(self.one(), "no-idle-state")

    def test_no_wake_binding(self):
        other = __import__("rookery_identity").new_identity(self.conn, self.sid, "unbound")
        self.set_state("unbound", "idle")
        self.mail(to=other)
        self.assertEqual(PM.wake_one(self.conn, self.root, self.config, self.ident("unbound")),
                         "no-wake-binding")

    def test_unsafe_name(self):
        self.mail()
        ident = self.ident()
        self.conn.execute("UPDATE identities SET name='bad name' WHERE agent_id=?",
                          (self.lc["agent_id"],))
        ident = dict(ident, name="bad name")
        self.assertEqual(PM.wake_one(self.conn, self.root, self.config, ident), "unsafe-name")

    def test_wake_failed_then_backoff_wait(self):
        self.mail()
        sent = []
        self.assertEqual(self.one(run=self.failing(sent), now=lambda: 1000.0), "wake-failed")
        self.assertEqual(self.one(run=self.failing(sent), now=lambda: 1010.0), "backoff-wait")
        self.assertEqual(len(sent), 1)


class TestFileBackoff(Split):
    def test_failure_writes_file_and_new_call_waits(self):
        self.mail()
        sent = []
        self.one(run=self.failing(sent), now=lambda: 1000.0)
        with open(self.bofile()) as fh:
            d = json.load(fh)
        self.assertEqual(set(d), {"until", "delay", "newest", "logged"})
        self.assertEqual(d["until"], 1030.0)
        self.assertEqual(self.one(now=lambda: 1010.0), "backoff-wait")   # real run: types nothing
        self.assertEqual(self.calls(), [])

    def test_after_window_types_again_and_success_removes_file(self):
        self.mail()
        self.one(run=self.failing([]), now=lambda: 1000.0)
        self.assertEqual(self.one(now=lambda: 1031.0), "pushed")
        self.assertEqual(len(self.calls()), 1)
        self.assertFalse(os.path.exists(self.bofile()))

    def test_second_failure_doubles_and_logs_once(self):
        self.mail()
        self.one(run=self.failing([]), now=lambda: 1000.0)
        self.one(run=self.failing([]), now=lambda: 1031.0)
        with open(self.bofile()) as fh:
            self.assertEqual(json.load(fh)["delay"], 60)
        self.assertEqual(sum(1 for x in self.log_lines() if x.get("outcome") == "wake-failed"), 1)

    def test_file_delay_caps_at_600(self):
        self.mail()
        t = 1000.0
        for _ in range(8):
            self.one(run=self.failing([]), now=lambda: t)
            with open(self.bofile()) as fh:
                t = json.load(fh)["until"] + 1
        with open(self.bofile()) as fh:
            self.assertEqual(json.load(fh)["delay"], PM.BACKOFF_MAX)

    def test_stale_file_for_older_mail_starts_at_30(self):
        self.mail()
        self.one(run=self.failing([]), now=lambda: 1000.0)
        self.one(run=self.failing([]), now=lambda: 1031.0)          # delay 60 for the first mail
        self.mail()                                                  # a newer mail: the record is stale
        self.assertEqual(self.one(run=self.failing([]), now=lambda: 1040.0), "wake-failed")   # no wait
        with open(self.bofile()) as fh:
            self.assertEqual(json.load(fh)["delay"], PM.BACKOFF_START)

    def test_write_failure_is_logged(self):
        self.mail()
        os.makedirs(os.path.join(self.root, "postmaster"), exist_ok=True)
        with open(os.path.join(self.root, "postmaster", "backoff"), "w") as fh:
            fh.write("a file where the folder should be")
        self.assertEqual(self.one(run=self.failing([]), now=lambda: 1000.0), "wake-failed")
        self.assertTrue(any(x.get("outcome") == "backoff-write-failed" for x in self.log_lines()))

    def test_corrupt_file_counts_as_none(self):
        self.mail()
        os.makedirs(os.path.dirname(self.bofile()))
        for junk in ("{not json", "[1]", '{"until": "x"}'):
            with open(self.bofile(), "w") as fh:
                fh.write(junk)
            self.assertIsNone(PM._backoff_load(self.root, self.lc["agent_id"]))
        self.assertEqual(self.one(now=lambda: 1000.0), "pushed")

    def test_dict_backoff_writes_no_file(self):
        self.mail()
        bo = {}
        self.one(run=self.failing([]), backoff=bo, now=lambda: 1000.0)
        self.assertIn(self.lc["agent_id"], bo)
        self.assertFalse(os.path.exists(self.bofile()))

    def test_unsafe_agent_id_is_no_backoff(self):
        self.assertIsNone(PM._backoff_load(self.root, "../x"))
        PM._backoff_save(self.root, "../x", {"until": 1, "delay": 1, "newest": 1, "logged": None})
        self.assertFalse(os.path.exists(os.path.join(self.root, "postmaster", "x.json")))


class TestTypeNotice(Split):
    def test_success_is_none_and_fills_tokens(self):
        sent = []
        def ok(argv, **kw):
            sent.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        self.assertIsNone(PM.type_notice([["herdr", "{value}", "{text}"]], "w1:p2", "hi", ok))
        self.assertEqual(sent, [["herdr", "w1:p2", "hi"]])

    def test_exit_code_text(self):
        self.assertEqual(PM.type_notice([["herdr", "x"]], "v", "t", self.failing([])), "herdr exited 1")

    def test_exception_text_and_stops(self):
        sent = []
        def boom(argv, **kw):
            sent.append(argv)
            raise OSError("nope")
        err = PM.type_notice([["a"], ["b"]], "v", "t", boom)
        self.assertEqual(err, "a: nope")
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
