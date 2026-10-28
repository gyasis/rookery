#!/usr/bin/env python3
"""Feature 005 final audit F1, F5, F6: sweep vs event wake lock, DB replaced, root deleted.

    python3 -m unittest test_wake_audit_sweep -v
"""
import os
import shutil
import sqlite3
import subprocess
import threading
import time

import postmaster as PM
import rookery_identity as RI
import rookery_wake
import turn_block as TB
from test_wake_sweep import SweepBase


class TestSweepLock(SweepBase):
    def lock_path(self):
        return os.path.join(self.root, "postmaster", "lock", self.lc["agent_id"] + ".lock")

    def test_sweep_skips_identity_locked_by_event_wake(self):
        self.mail()
        os.environ["ROOKERY_WAKE"] = "off"
        calls = []

        def slow(*a, **k):
            calls.append(1)
            time.sleep(1.5)
            return subprocess.run(*a, **k)

        def event():
            conn = TB.open_db()
            try:
                rookery_wake.run_wake(conn, self.root, self.config, self.sid, self.lc["agent_id"], run=slow)
            finally:
                conn.close()
        t = threading.Thread(target=event)
        t.start()
        time.sleep(0.5)

        def counting(*a, **k):
            calls.append(2)
            return subprocess.run(*a, **k)
        pushed = PM.wake_identities(self.conn, self.root, self.config, run=counting, session_id=self.sid)
        t.join()
        self.assertEqual(pushed, 0)
        self.assertTrue(self.has_log("wake-busy"), self.log_lines())
        self.assertNotIn(2, calls)
        self.assertEqual(len(self.calls()), 1)

    def test_sweep_with_lock_held_does_not_push(self):
        self.lost_mail()
        lock = rookery_wake.try_lock(self.lock_path())
        self.assertIsNotNone(lock)
        try:
            self.assertEqual(self.sweep(1), 0)
        finally:
            lock.close()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("wake-busy"), self.log_lines())

    def test_lock_released_after_wake(self):
        self.lost_mail()
        self.assertEqual(self.sweep(1), 0)
        lock = rookery_wake.try_lock(self.lock_path())
        self.assertIsNotNone(lock)
        lock.close()


class TestSweepEnv(SweepBase):
    def test_db_replaced_ends_sweep(self):
        self.sleeps = []
        db = TB.db_path()

        def sleep(s):
            self.sleeps.append(s)
            RI.end_session(self.conn, "alpha")
            bak = db + ".bak"
            src = sqlite3.connect(db)
            dst = sqlite3.connect(bak)
            src.backup(dst)
            dst.close()
            src.close()
            os.replace(bak, db)
            # the copy is the ended state; a stale connection would not notice the swap
        self.assertEqual(PM.run_session_sweep(self.root, self.config, "alpha", sleep=sleep, rounds=9), 0)
        self.assertEqual(self.sleeps, [PM.SESSION_CHECK_S])   # 006 FR-008: caught after one step, not one round
        self.assertTrue(self.has_log("db-replaced"), self.log_lines())

    def test_root_deleted_ends_sweep_without_recreating(self):
        self.sleeps = []

        def sleep(s):
            self.sleeps.append(s)
            shutil.rmtree(self.root)
        self.assertEqual(PM.run_session_sweep(self.root, self.config, "alpha", sleep=sleep, rounds=9), 0)
        self.assertEqual(self.sleeps, [PM.SESSION_CHECK_S])
        self.assertFalse(os.path.exists(self.root))


if __name__ == "__main__":
    import unittest
    unittest.main()
