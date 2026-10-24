#!/usr/bin/env python3
"""Schema of the four rookery-up tables (T003, research D6).

    python3 -m unittest test_up_schema -v
"""
import os
import sqlite3
import tempfile
import unittest

import rookery as R
import turn_block as TB

NEW = {
    "up_agents": ["agent_id", "session_id", "harness", "role", "repo", "task_id",
                  "binding_kind", "updated_at"],
    "worktrees": ["path", "session_id", "agent_id", "repo", "branch", "state",
                  "created_at", "finished_at"],
    "claims": ["session_id", "agent_id", "pattern", "active", "updated_at"],
    "ports": ["agent_id", "name", "port"],
}
OLD_COUNTED = ("inbox", "rookery_sessions", "identities", "bindings")


class UpSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self._old_env = os.environ.get("ROOKERY_DB")
        os.environ["ROOKERY_DB"] = self.db

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("ROOKERY_DB", None)
        else:
            os.environ["ROOKERY_DB"] = self._old_env
        self.tmp.cleanup()

    def cols(self, conn, table):
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    def count(self, conn, table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_new_db_has_four_tables(self):
        conn = R.connect(self.db)
        for table, cols in NEW.items():
            self.assertEqual(self.cols(conn, table), cols, table)
        conn.close()

    def test_defaults(self):
        conn = R.connect(self.db)
        conn.execute("INSERT INTO worktrees(path,session_id,agent_id,repo,branch,created_at) "
                     "VALUES('p','s','a','r','b','t')")
        conn.execute("INSERT INTO claims(session_id,agent_id,pattern,updated_at) "
                     "VALUES('s','a','x/*','t')")
        self.assertEqual(conn.execute("SELECT state FROM worktrees").fetchone()[0], "pending")
        self.assertEqual(conn.execute("SELECT active FROM claims").fetchone()[0], 1)
        conn.close()

    def make_old_db(self):
        conn = R.connect(self.db)
        conn.execute("INSERT INTO inbox(sender, recipient, body, created_at) "
                     "VALUES('a','b','hi',1)")
        conn.commit()
        for t in NEW:
            conn.execute(f"DROP TABLE {t}")
        conn.commit()
        before = {t: self.count(conn, t) for t in OLD_COUNTED}
        self.assertEqual(before["inbox"], 1)
        conn.close()
        return before

    def tables(self, conn):
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_open_db_does_not_need_write_lock(self):
        self.make_old_db()
        holder = sqlite3.connect(self.db, timeout=1, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            c2 = TB.open_db()          # must not raise "database is locked"
            try:
                self.assertTrue(TB._schema_current(c2))
                self.assertFalse(set(NEW) & self.tables(c2))
            finally:
                c2.close()
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_ensure_up_tables_adds_tables_keeps_rows(self):
        before = self.make_old_db()
        c2 = TB.open_db()
        try:
            TB.ensure_up_tables(c2)
            for table, cols in NEW.items():
                self.assertEqual(self.cols(c2, table), cols, table)
            self.assertEqual({t: self.count(c2, t) for t in OLD_COUNTED}, before)
            TB.ensure_up_tables(c2)    # idempotent
        finally:
            c2.close()

    def test_port_is_unique(self):
        conn = R.connect(self.db)
        conn.execute("INSERT INTO ports(agent_id,name,port) VALUES('a','api',9000)")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO ports(agent_id,name,port) VALUES('b','web',9000)")
        conn.close()


if __name__ == "__main__":
    unittest.main()
