#!/usr/bin/env python3
"""Rookery terminal node types a notice only (T035): FR-019, FR-020, US5, SC-006.

    python3 -m unittest test_pane -v

fixtures/bin is first on PATH, so the fake tmux (and fake herdr) record calls and no real pane is typed into.
"""
import json
import os
import tempfile
import unittest

import postmaster as PM
import rookery as R
import rookery_identity as RI
import terminal_node as TN
import turn_block as TB
from test_wake import FIXBIN, BIND

HERE = os.path.dirname(os.path.abspath(__file__))
BODY = "SECRET-BODY-123"


class TestPane(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(("ROOKERY_", "HERDR_", "TMUX")):
                os.environ.pop(k)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        self.log = os.path.join(self.tmp.name, "tmux.log")
        self.record = os.path.join(self.tmp.name, "herdr.record")
        os.environ.update(HOME=self.tmp.name, ROOKERY_ROOT=self.root, ROOKERY_FIXTURE_LOG=self.log,
                          ROOKERY_DB=os.path.join(self.tmp.name, "t.db"), HERDR_RECORD=self.record,
                          PATH=FIXBIN + os.pathsep + "/usr/bin:/bin")
        self._db = R.DB_PATH
        R.DB_PATH = os.environ["ROOKERY_DB"]   # rookery.connect() reads the path at import time
        self.conn = TB.open_db()
        self.config = RI.load_config(persist=False)
        sess = RI.new_session(self.conn, name="alpha")
        self.sid = sess["session_id"]
        self.me = RI.new_identity(self.conn, self.sid, "me")
        self.lc = RI.new_identity(self.conn, self.sid, "law-counsel")
        RI.bind(self.conn, self.sid, self.lc["agent_id"], *BIND)
        ws = TB.workspace(self.root, "alpha", "law-counsel")
        os.makedirs(os.path.join(ws, "state"), exist_ok=True)
        TB.write_json_atomic(os.path.join(ws, "state", "turn.json"), {"state": "idle", "since": R.now()})
        self.mid = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], BODY,
                          topic="TASK-7", session_id=self.sid)

    def tearDown(self):
        R.DB_PATH = self._db
        self.conn.close()
        os.environ.clear()
        os.environ.update(self._saved)
        self.tmp.cleanup()

    def lines(self, path):
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def tmux_text(self):
        return [l for l in self.lines(self.log) if "-l" in l]

    def herdr_prompts(self):
        return [l for l in self.lines(self.record) if l[:2] == ["agent", "prompt"]]

    def one_pass(self):
        TN.run(self.lc["agent_id"], "tmux", "sess:0.0", 0, once=True)

    def test_types_notice_without_body_and_keeps_pending(self):
        self.one_pass()
        typed = self.tmux_text()
        self.assertEqual(len(typed), 1, typed)
        text = " ".join(typed[0])
        self.assertIn("me", text)
        self.assertIn("TASK-7", text)
        self.assertIn("rookery inbox", text)
        self.assertNotIn(BODY, json.dumps(self.lines(self.log)))
        st = self.conn.execute("SELECT status FROM inbox WHERE id=?", (self.mid,)).fetchone()["status"]
        self.assertEqual(st, "pending")

    def test_second_pass_types_nothing_more(self):
        self.one_pass()
        self.one_pass()
        self.assertEqual(len(self.tmux_text()), 1)

    def test_no_root_types_one_notice_across_polls(self):
        os.environ.pop("ROOKERY_ROOT")                    # no pushed file: run() keeps the set
        seen = set()
        for _ in range(3):
            TN._pass(self.conn, self.lc["agent_id"], "tmux", "sess:0.0", None, seen)
        self.assertEqual(len(self.tmux_text()), 1)

    def test_status_mail_gets_no_notice(self):
        self.conn.execute("UPDATE inbox SET status='done' WHERE id=?", (self.mid,))
        self.conn.commit()
        R.send(self.conn, self.me["agent_id"], self.lc["agent_id"],
               json.dumps({"kind": "status", "task": "TASK-7", "status": "done"}),
               topic="TASK-7", session_id=self.sid)
        self.one_pass()
        self.assertEqual(self.tmux_text(), [])            # postmaster would not push it either

    def test_terminal_node_then_wake_one_notice(self):
        self.one_pass()
        PM.wake_identities(self.conn, self.root, self.config)
        self.assertEqual(len(self.tmux_text()) + len(self.herdr_prompts()), 1)

    def test_wake_then_terminal_node_one_notice(self):
        PM.wake_identities(self.conn, self.root, self.config)
        self.one_pass()
        self.assertEqual(len(self.tmux_text()) + len(self.herdr_prompts()), 1)

    def test_source_does_not_name_send_mail_script(self):
        with open(os.path.join(HERE, "terminal_node.py")) as fh:
            self.assertNotIn("send_mail.py", fh.read())


if __name__ == "__main__":
    unittest.main()
