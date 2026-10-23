#!/usr/bin/env python3
"""Mail gate, T006: the `gate` log stream (research D15).

    python3 -m unittest test_gate_log -v

A gate record goes to <ROOKERY_ROOT>/logs/gate.jsonl, else ~/.rookery/logs/gate.jsonl.
A failed write prints one line to stderr and never raises.
"""
import contextlib
import io
import json
import os
import threading
import unittest

import turn_block as TB
from test_hooks import Base


class GateLog(Base):
    def read(self, path):
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_root_set_writes_under_root(self):
        TB.log_event(None, "gate", path="send", outcome="sent")
        recs = self.read(os.path.join(self.root, "logs", "gate.jsonl"))
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["path"], recs[0]["outcome"]), ("send", "sent"))
        self.assertIn("ts", recs[0])

    def test_concurrent_first_use_adds_one_handler(self):
        n, errs = 16, []
        barrier = threading.Barrier(n)

        def go(i):
            try:
                barrier.wait()
                TB.log_event(None, "gate", path="t", n=i)
            except Exception as exc:       # pragma: no cover
                errs.append(exc)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        p = os.path.join(self.root, "logs", "gate.jsonl")
        self.assertEqual(len(self.read(p)), n)
        self.assertEqual(len(TB._logger_for(p).handlers), 1)

    def test_root_unset_writes_under_home(self):
        del os.environ["ROOKERY_ROOT"]
        TB.log_event(None, "gate", path="send", outcome="sent")
        recs = self.read(os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl"))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["outcome"], "sent")

    def test_unwritable_folder_prints_one_line_and_does_not_raise(self):
        # a file where the logs folder should be, so the folder cannot be made
        with open(os.path.join(self.root, "logs"), "w") as f:
            f.write("x")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            TB.log_event(None, "gate", path="send", outcome="sent")
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("gate", lines[0])

    def test_other_streams_stay_silent_on_failure(self):
        with open(os.path.join(self.root, "logs"), "w") as f:
            f.write("x")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            TB.log_event(self.root, "turn-block", outcome="x")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
