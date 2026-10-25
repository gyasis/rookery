#!/usr/bin/env python3
"""sessions migration (FR-029, FR-034, constitution I): an old database gains
rookery_sessions, identities, bindings and inbox.session_id additively, keeps every
old value, and a second init_db() changes nothing.

The default fixture is built from the schema of commit e2bbed5 (before sessions).
If ROOKERY_DB points at a real database, a COPY of it is migrated too; the live
file is never opened for writing.

    python3 test_sessions_migration.py
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest

import rookery as R

OLD_COMMIT = "e2bbed5"
HERE = os.path.dirname(os.path.abspath(__file__))
NEW_TABLES = ("rookery_sessions", "identities", "bindings")


def old_schema():
    return subprocess.run(["git", "show", f"{OLD_COMMIT}:schema.sql"], cwd=HERE,
                          check=True, capture_output=True, text=True).stdout


def build_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(old_schema())
    conn.execute("INSERT INTO nodes(node_id, kind, status, lifecycle, last_seen) "
                 "VALUES('law-counsel','headless','idle','ephemeral',100)")
    rows = [
        ("researcher", "law-counsel", "first", "TASK-1", "pending", 0, None, None, 0, 1000, None),
        ("researcher", "law-counsel", "second", None, "pending", 0, None, None, 0, 1001, None),
        ("swe", "law-counsel", "third", "TASK-2", "done", 1, "n", 1002, 1, 1002, 1003),
        ("law-counsel", "swe", "reply", "TASK-2", "pending", 0, None, None, 0, 1004, None),
    ]
    conn.executemany(
        "INSERT INTO inbox(sender, recipient, body, topic, status, attempts, note, claimed_at,"
        " delivered, created_at, delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def inbox_cols(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(inbox)")]


def dump(conn, table, cols=None):
    cols = ", ".join(cols) if cols else "*"
    return [tuple(r) for r in conn.execute(f"SELECT {cols} FROM {table} ORDER BY 1")]


class SessionsMigration(unittest.TestCase):
    node = "law-counsel"

    def prepare(self, path):
        """Hook for the fixture: leave an old-shape database at `path`."""
        build_old_db(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "old.db")
        self.prepare(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.old_cols = [c for c in inbox_cols(self.conn) if c != "session_id"]
        self.before_inbox = dump(self.conn, "inbox", self.old_cols)
        self.before_nodes = dump(self.conn, "nodes")
        self.before_pending = [tuple(r) for r in R.fetch_undelivered(self.conn, self.node)]
        R.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_new_tables_exist(self):
        for t in NEW_TABLES:
            self.assertIn(t, tables(self.conn))

    def test_inbox_session_id_is_legacy_for_old_rows(self):
        self.assertIn("session_id", inbox_cols(self.conn))
        vals = [r[0] for r in self.conn.execute("SELECT session_id FROM inbox")]
        self.assertEqual(len(vals), len(self.before_inbox))
        self.assertEqual(set(vals), {"legacy"})

    def test_one_legacy_session_row(self):
        rows = self.conn.execute(
            "SELECT * FROM rookery_sessions WHERE session_id='legacy'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "open")

    def test_old_column_values_identical(self):
        self.assertEqual(dump(self.conn, "inbox", self.old_cols), self.before_inbox)
        self.assertEqual(dump(self.conn, "nodes"), self.before_nodes)

    def test_second_init_db_changes_nothing(self):
        snap = {t: dump(self.conn, t) for t in sorted(tables(self.conn))
                if not t.startswith("sqlite_")}
        schema = [tuple(r) for r in self.conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        R.init_db(self.conn)
        self.assertEqual(schema, [tuple(r) for r in self.conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name")])
        for t, rows in snap.items():
            self.assertEqual(dump(self.conn, t), rows, t)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM rookery_sessions WHERE session_id='legacy'").fetchone()[0], 1)

    def test_fetch_undelivered_same_rows(self):
        after = [tuple(r) for r in R.fetch_undelivered(self.conn, self.node)]
        self.assertTrue(self.before_pending)
        # same rows in the same order; compare the columns the old database had
        idx = [i for i, c in enumerate(inbox_cols(self.conn)) if c not in ("session_id", "thread")]
        self.assertEqual([tuple(r[i] for i in idx) for r in after], self.before_pending)


@unittest.skipUnless(os.environ.get("ROOKERY_DB") and os.path.exists(os.environ["ROOKERY_DB"]),
                     "ROOKERY_DB not set or missing")
class SessionsMigrationLiveCopy(SessionsMigration):
    """Same checks on a COPY of ROOKERY_DB. The live file is only read."""

    def prepare(self, path):
        src = sqlite3.connect("file:%s?mode=ro" % os.environ["ROOKERY_DB"], uri=True)
        dst = sqlite3.connect(path)
        src.backup(dst)
        src.close()
        dst.close()
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT recipient FROM inbox WHERE status='pending' LIMIT 1").fetchall()
        self.node = rows[0][0] if rows else "law-counsel"
        conn.close()

    def test_fetch_undelivered_same_rows(self):
        if not self.before_pending:
            self.skipTest("live copy has no pending rows")
        super().test_fetch_undelivered_same_rows()


class ConcurrentInitDb(unittest.TestCase):
    """Fix 1: concurrent init_db on an old DB must not raise 'duplicate column name'."""

    WORKER = ("import sys, sqlite3, time, rookery\n"
              "c = sqlite3.connect(sys.argv[1], timeout=10)\n"
              "c.execute('PRAGMA busy_timeout=10000')\n"
              "time.sleep(max(0, float(sys.argv[2]) - time.time()))\n"
              "rookery.init_db(c)\n")

    def test_concurrent_init_db_on_old_db(self):
        import sys
        for rnd in range(8):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "race.db")
                build_old_db(path)
                go = str(time.time() + 1.0)   # all workers start together
                procs = [subprocess.Popen([sys.executable, "-c", self.WORKER, path, go],
                                          cwd=HERE, stderr=subprocess.PIPE, text=True)
                         for _ in range(6)]
                errs = [p.communicate()[1] for p in procs]
                self.assertEqual([e for e in errs if e], [], f"round {rnd}")
                self.assertTrue(all(p.returncode == 0 for p in procs))
                conn = sqlite3.connect(path)
                try:
                    self.assertIn("session_id", inbox_cols(conn))
                    for t in NEW_TABLES:
                        self.assertIn(t, tables(conn))
                    self.assertEqual(conn.execute(
                        "SELECT COUNT(*) FROM rookery_sessions WHERE session_id='legacy'"
                    ).fetchone()[0], 1)
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
