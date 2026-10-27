"""T017: `rookery up --session NAME`, a made name, and resume by name (US7, FR-023..FR-026)."""
import json
import os
import re
import sqlite3
import unittest
from unittest import mock

import test_up_build as tb

NAME_RE = re.compile(r"[a-z]+(-[a-z]+){2}")


class UpSession(tb.UpBuild):
    def run(self, result=None):
        if self._testMethodName.startswith("test_sess_"):
            return super().run(result)
        return None

    def nameless(self):
        spec = {k: v for k, v in tb.TEAM.items() if k != "session"}
        self.write_team(spec)

    def up_json(self, *args, code=0):
        r = self.up(self.team, "--no-launch", "--json", *args, code=code)
        return json.loads(r.stdout)

    def ids(self):
        c = sqlite3.connect(self.db)
        try:
            return sorted(c.execute("SELECT agent_id FROM identities").fetchall())
        finally:
            c.close()

    def test_sess_no_name_makes_one(self):
        self.nameless()
        out = self.up_json()
        self.assertTrue(out["name_made"])
        self.assertRegex(out["session_name"], NAME_RE)
        self.assertTrue(NAME_RE.fullmatch(out["session_name"]))

    def test_sess_text_output_has_name(self):
        self.nameless()
        r = self.up(self.team, "--no-launch")
        m = re.search(r"^Session: (\S+)", r.stdout, re.M)
        self.assertTrue(m, r.stdout)
        self.assertRegex(m.group(1), NAME_RE)

    def test_sess_flag_wins_over_file(self):
        out = self.up_json("--session", "from-flag")
        self.assertEqual(out["session_name"], "from-flag")
        self.assertFalse(out["name_made"])
        self.assertTrue(os.path.isdir(os.path.join(self.root, "agents", "from-flag")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "agents", "auth-fix")))
        r = self.up(self.team, "--no-launch", "--json", "--session", "from-flag")
        self.assertIn("NOTE: --session from-flag replaces the session auth-fix", r.stderr)

    def test_sess_file_name_kept_without_flag(self):
        out = self.up_json()
        self.assertEqual(out["session_name"], "auth-fix")
        self.assertFalse(out["name_made"])

    def test_sess_resume_with_made_name(self):
        self.nameless()
        first = self.up_json()
        ids = self.ids()
        second = self.up_json("--session", first["session_name"])
        self.assertFalse(second["name_made"])
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(self.ids(), ids)
        r = self.up(self.team, "--no-launch", "--session", first["session_name"])
        self.assertIn("scout: reused", r.stdout)

    def test_sess_ended_refused(self):
        self.up_json("--session", "done-one")
        c = sqlite3.connect(self.db)
        c.execute("UPDATE rookery_sessions SET state='ended' WHERE name='done-one'")
        c.commit()
        c.close()
        r = self.up(self.team, "--no-launch", "--session", "done-one", code=2)
        self.assertIn("ended", r.stderr)

    def test_sess_bad_flag_values_exit_2(self):
        for bad in ("legacy", "../x", "rs-0123abcd", "ag-0123456789", "a b", ".."):
            with self.subTest(bad=bad):
                r = self.up(self.team, "--no-launch", "--session", bad, code=2)
                self.assertTrue(r.stderr.strip(), bad)

    def test_sess_bad_flag_json_errors(self):
        r = self.up(self.team, "--no-launch", "--json", "--session", "legacy", code=2)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_sess_file_without_name_or_flag_still_ok_when_made(self):
        self.nameless()
        self.up_json()
        self.assertEqual(self.count("rookery_sessions"), 2)  # legacy + made


class FreshName(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ROOKERY_DB": os.path.join(self.tmp.name, "r.db"),
                                                "HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_five_clashes_exit_2(self):
        import cli_workspace as cw
        import rookery_identity as ri
        import turn_block
        conn = turn_block.open_db()
        turn_block.ensure_up_tables(conn)
        ri._new_session(conn, "taken-one-here")
        conn.commit()
        conn.close()
        calls = []
        def make():
            calls.append(1)
            return "taken-one-here"
        with self.assertRaises(cw._Bad) as cm:
            cw._fresh_name(make)
        self.assertEqual(len(calls), 5)
        self.assertEqual(cm.exception.code, 2)

    def test_retry_then_success(self):
        import cli_workspace as cw
        import rookery_identity as ri
        import turn_block
        conn = turn_block.open_db()
        turn_block.ensure_up_tables(conn)
        ri._new_session(conn, "taken-one-here")
        conn.commit()
        conn.close()
        names = iter(["taken-one-here", "taken-one-here", "free-one-here"])
        self.assertEqual(cw._fresh_name(lambda: next(names)), "free-one-here")


if __name__ == "__main__":
    unittest.main()
