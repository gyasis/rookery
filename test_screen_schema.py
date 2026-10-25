#!/usr/bin/env python3
"""004 schema (T003, D10, D25, FR-035): the thread column and the six screener tables are additive.
A new database has them. An old database (schema of dd56f00, three inbox rows) migrates twice with no error and
keeps every value. The hook path migrates once and stays under 1 s on 10,000 rows. The live-copy case reads only a
copy of the file that ROOKERY_DB names.

    python3 -m unittest test_screen_schema -v
"""
import os
import sqlite3
import subprocess
import tempfile
import unittest

import rookery as R
import test_hooks
import test_sessions_migration as TSM
import turn_block as TB

HERE = os.path.dirname(os.path.abspath(__file__))
OLD_COMMIT = "dd56f00"

NEW_COLUMNS = {
    "session_context": ["session_id", "task", "acceptance", "ask_budget", "updated_at"],
    "agent_scope": ["agent_id", "session_id", "does", "does_not"],
    "threads": ["session_id", "thread", "status", "opened_by", "first_mail", "opened_at",
                "closed_by", "closed_at", "close_reason"],
    "screen_mail": ["mail_id", "session_id", "state", "owner", "claimed_at", "attempts", "retry_at",
                    "envelope", "ask_hash", "final_label", "final_by", "decided_at"],
    "screen_decisions": ["id", "mail_id", "session_id", "stage", "check_name", "attempt", "label",
                         "confidence", "band", "mode", "action", "decided_by", "backend_impl", "failure",
                         "detail", "prompt_tokens", "reasoning_tokens", "created_at"],
    "screen_calls": ["id", "decision_id", "mail_id", "call_label", "verdict", "source", "by", "created_at"],
}


def cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def build_dd56f00_db(path):
    """The schema of master dd56f00 (it already has the 002 screening columns), three inbox rows."""
    schema = subprocess.run(["git", "show", f"{OLD_COMMIT}:schema.sql"], cwd=HERE, check=True,
                            capture_output=True, text=True).stdout
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.executemany(
        "INSERT INTO inbox(sender, recipient, body, topic, status, created_at) VALUES(?,?,?,?,?,?)",
        [("a", "b", "one", "T-1", "pending", 10), ("b", "a", "two", None, "held", 11),
         ("a", "b", "three", "T-2", "quarantined", 12)])
    conn.commit()
    conn.close()


class NewDatabase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        R.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_every_table_and_column_of_the_data_model(self):
        for table, want in NEW_COLUMNS.items():
            self.assertEqual(cols(self.conn, table), want, table)

    def test_inbox_has_thread(self):
        self.assertIn("thread", cols(self.conn, "inbox"))

    def test_unique_mail_check_attempt(self):
        ins = ("INSERT INTO screen_decisions(mail_id, session_id, stage, check_name, attempt, label,"
               " mode, decided_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)")
        self.conn.execute(ins, (5, "s", "t1", "t1", 1, "deliver", "shadow", "screener:m", 1))
        self.conn.execute(ins, (5, "s", "t1", "t1", 2, "deliver", "shadow", "screener:m", 2))   # new try
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(ins, (5, "s", "t1", "t1", 1, "unneeded", "shadow", "screener:m", 3))

    def test_inbox_status_has_no_check_so_escalated_is_storable(self):
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='inbox'").fetchone()[0]
        self.assertNotIn("CHECK", sql.upper())

    def test_schema_current_knows_the_new_tables(self):
        self.assertTrue(TB._schema_current(self.conn))
        self.conn.execute("DROP TABLE screen_calls")
        self.assertFalse(TB._schema_current(self.conn))
        self.conn.execute("ALTER TABLE inbox DROP COLUMN thread")
        self.conn.execute("CREATE TABLE screen_calls(id INTEGER)")
        self.assertFalse(TB._schema_current(self.conn))

    def test_schema_current_needs_the_screen_indexes(self):
        for name in R._SCREEN_INDEXES:
            self.assertTrue(TB._schema_current(self.conn), name)
            self.conn.execute(f"DROP INDEX {name}")
            self.assertFalse(TB._schema_current(self.conn), name)
            R.init_db(self.conn)  # an interrupted migration re-runs and puts the index back


class OldDatabase(unittest.TestCase):
    def prepare(self, path):
        build_dd56f00_db(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "old.db")
        self.prepare(self.path)
        self.conn = sqlite3.connect(self.path)
        self.old_cols = [c for c in cols(self.conn, "inbox")]
        self.before = TSM.dump(self.conn, "inbox", self.old_cols)
        self.assertFalse(TB._schema_current(self.conn))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_migrates_twice_and_keeps_every_inbox_value(self):
        R.init_db(self.conn)
        schema = self.conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        R.init_db(self.conn)
        self.assertEqual(schema, self.conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall())
        self.assertEqual(TSM.dump(self.conn, "inbox", self.old_cols), self.before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], len(self.before))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inbox WHERE thread IS NOT NULL").fetchone()[0], 0)
        for table, want in NEW_COLUMNS.items():
            self.assertEqual(cols(self.conn, table), want, table)
        self.assertTrue(TB._schema_current(self.conn))


@unittest.skipUnless(os.environ.get("ROOKERY_DB") and os.path.exists(os.environ["ROOKERY_DB"]),
                     "ROOKERY_DB not set or missing")
class ScreenSchemaLiveCopy(OldDatabase):
    """Same checks on a COPY of ROOKERY_DB (backup API from a read-only URI). The live file is only read."""

    def prepare(self, path):
        src = sqlite3.connect("file:%s?mode=ro" % os.environ["ROOKERY_DB"], uri=True)
        dst = sqlite3.connect(path)
        src.backup(dst)
        src.close()
        dst.close()
        conn = sqlite3.connect(path)       # a live copy may already be migrated: make it old again
        for t in R._SCREEN_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        if "thread" in cols(conn, "inbox"):
            conn.execute("ALTER TABLE inbox DROP COLUMN thread")
        conn.commit()
        conn.close()


class MigratedHookTiming(test_hooks.HookTiming):
    """D25: after the migration, each hook on 10,000 rows stays under 1 s. The first hook call migrates."""

    def seed(self):
        super().seed()
        for t in R._SCREEN_TABLES:
            self.conn.execute(f"DROP TABLE {t}")
        self.conn.execute("ALTER TABLE inbox DROP COLUMN thread")
        self.conn.commit()
        self.assertFalse(TB._schema_current(self.conn))

    def test_each_hook_under_one_second_with_10000_rows(self):
        super().test_each_hook_under_one_second_with_10000_rows()
        c = sqlite3.connect(self.db)
        try:
            self.assertTrue(TB._schema_current(c))
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
