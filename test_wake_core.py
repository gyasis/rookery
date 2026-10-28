#!/usr/bin/env python3
"""rookery_wake core helpers (feature 005, T002): spawn, try_lock, resolve_root, sweep_interval.

    python3 -m unittest test_wake_core -v

Tests use a temp HOME, ROOKERY_DB and ROOKERY_ROOT. No model, no network, no herdr or tmux.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import bootstrap
import rookery_identity as RI
import rookery_wake as W
import turn_block as TB

HERE = os.path.dirname(os.path.abspath(__file__))


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(("ROOKERY_", "HERDR_", "TMUX")):
                os.environ.pop(k)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = os.path.join(self.tmp.name, "t.db")
        os.environ["ROOKERY_ROOT"] = self.root
        self._argv = W.program_argv
        del W._children[:]

    def tearDown(self):
        W.program_argv = self._argv
        for p in W._children:
            try:
                p.kill()
                p.wait(5)
            except Exception:
                pass
        del W._children[:]
        os.environ.clear()
        os.environ.update(self._saved)
        self.tmp.cleanup()

    def child(self, code):
        W.program_argv = lambda: [sys.executable, "-c", code]

    def log_lines(self):
        path = os.path.join(self.root, "postmaster", "postmaster.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(x) for x in fh if x.strip()]


class ProgramArgv(Base):
    def test_checkout(self):
        argv = W.program_argv()
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("rookery_cli.py"))
        self.assertTrue(os.path.isfile(argv[1]))

    def test_zipapp(self):
        arch = os.path.join(self.tmp.name, "r.pyz")
        bootstrap.build_pyz(arch)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import rookery_wake; "
                "print(rookery_wake.program_argv()[1])")
        out = subprocess.run([sys.executable, "-c", code, arch], capture_output=True, text=True,
                             cwd=self.tmp.name, check=True).stdout.strip()
        self.assertEqual(out, arch)


class Spawn(Base):
    def test_returns_fast_and_child_runs(self):
        mark = os.path.join(self.tmp.name, "mark")
        self.child("import time,sys; open(%r,'w').write('x'); time.sleep(2)" % mark)
        t0 = time.monotonic()
        self.assertTrue(W.spawn([]))
        self.assertLess(time.monotonic() - t0, 0.25)   # SC-006 is 50 ms; margin for a loaded test box
        for _ in range(40):
            if os.path.exists(mark):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(mark))

    def test_bad_executable_logs_and_returns_false(self):
        W.program_argv = lambda: [os.path.join(self.tmp.name, "no-such-binary")]
        self.assertFalse(W.spawn([], log_root=self.root))
        self.assertTrue(any(r.get("outcome") == "wake-spawn-failed" and r.get("reason")
                            for r in self.log_lines()))

    def test_fifty_spawns_leave_few_zombies(self):
        self.child("pass")
        for _ in range(50):
            self.assertTrue(W.spawn([]))
        for _ in range(100):                 # wait up to 10 s for the children to end, even on a slow box
            if all(p.poll() is not None for p in W._children):
                break
            time.sleep(0.1)
        W.spawn([])
        time.sleep(0.5)
        zombies = 0
        for p in W._children:
            try:
                with open("/proc/%d/stat" % p.pid) as fh:
                    if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                        zombies += 1
            except OSError:
                pass
        self.assertLessEqual(zombies, 2)
        self.assertLessEqual(len(W._children), 2)


class TryLock(Base):
    def test_exclusive_and_released(self):
        path = os.path.join(self.tmp.name, "a", "b", "x.lock")
        first = W.try_lock(path)
        self.assertIsNotNone(first)
        self.assertIsNone(W.try_lock(path))
        first.close()
        again = W.try_lock(path)
        self.assertIsNotNone(again)
        again.close()

    def test_killed_holder_frees_lock(self):
        path = os.path.join(self.tmp.name, "k.lock")
        code = ("import sys,time; sys.path.insert(0, %r); import rookery_wake as W; "
                "f=W.try_lock(%r); print('held' if f else 'no', flush=True); time.sleep(30)"
                % (HERE, path))
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            self.assertIsNone(W.try_lock(path))
        finally:
            p.kill()
            p.wait(5)
        again = W.try_lock(path)
        self.assertIsNotNone(again)
        again.close()


class ResolveRoot(Base):
    def setUp(self):
        super().setUp()
        self.conn = TB.open_db()
        self.sid = RI.new_session(self.conn, name="alpha")["session_id"]

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_binding_wins(self):
        self.conn.execute(
            "INSERT INTO bindings(session_id, owner_type, owner_id, kind, value, created_at) "
            "VALUES(?,?,?,?,?,?)", (self.sid, "session", self.sid, "rookery-root", "/bound", "t"))
        self.conn.commit()
        self.assertEqual(W.resolve_root(self.conn, self.sid), "/bound")

    def test_env(self):
        self.assertEqual(W.resolve_root(self.conn, self.sid), self.root)

    def test_none(self):
        os.environ.pop("ROOKERY_ROOT")
        self.assertIsNone(W.resolve_root(self.conn, self.sid))
        os.environ["ROOKERY_ROOT"] = ""
        self.assertIsNone(W.resolve_root(self.conn, self.sid))


class SweepInterval(Base):
    def test_values(self):
        # feature 006 D5/FR-013: default 120 s, never below 60 s
        self.assertEqual(W.sweep_interval({"sweep_s": 0.5}), (60, True))
        self.assertEqual(W.sweep_interval({"sweep_s": 60}), (60, False))
        self.assertEqual(W.sweep_interval({"sweep_s": 300}), (300, False))
        self.assertEqual(W.sweep_interval({"sweep_s": 600}), (600, False))
        self.assertEqual(W.sweep_interval({}), (120, False))
        self.assertEqual(W.sweep_interval({"sweep_s": "x"}), (120, False))
        self.assertEqual(W.sweep_interval({"sweep_s": True}), (120, False))

    def test_session_override_wins(self):
        # feature 006 FR-014: the session's sweep-s binding wins over config sweep_s
        self.assertEqual(W.sweep_interval({"sweep_s": 600}, 90), (90, False))
        self.assertEqual(W.sweep_interval({"sweep_s": 600}, 10), (60, True))
        self.assertEqual(W.sweep_interval("x", 90), (90, False))


if __name__ == "__main__":
    unittest.main()


class Audit(Base):
    def test_sweep_interval_refuses_nan_inf_and_non_dict(self):
        self.assertEqual(W.sweep_interval({"sweep_s": float("nan")}), (120, True))
        self.assertEqual(W.sweep_interval({"sweep_s": float("inf")}), (120, True))
        self.assertEqual(W.sweep_interval("x"), (120, False))
        self.assertEqual(W.sweep_interval([1]), (120, False))

    def test_try_lock_logs_when_it_cannot_open(self):
        ro = os.path.join(self.tmp.name, "ro")
        os.makedirs(ro)
        path = os.path.join(ro, "x.lock")
        open(path, "w").close()
        os.chmod(path, 0o444)
        try:
            if os.access(path, os.W_OK):
                self.skipTest("running as a user who can write a read-only file")
            self.assertIsNone(W.try_lock(path, log_root=self.root))
            self.assertTrue(any(r.get("outcome") == "lock-failed" for r in self.log_lines()))
        finally:
            os.chmod(path, 0o644)
