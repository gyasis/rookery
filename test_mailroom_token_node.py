#!/usr/bin/env python3
"""`--token-node SESSION:NAME`: a /send with the bearer token sends as one fixed identity.

    python3 -m unittest test_mailroom_token_node -v

The server runs with `--token-node duly-remote:duly`. Session `duly-remote` holds `duly` and `razer`.
"""
import json
import os
import sqlite3
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

import rookery_identity as RI
from test_hooks import Base, HERE
from test_paths_mailroom import ServerMixin, TOKEN


class TokenNodeSend(ServerMixin, Base):
    extra_args = ("--token-node", "duly-remote:duly")

    def setUp(self):
        super().setUp()
        self.remote = RI.new_session(self.conn, name="duly-remote")
        self.duly = RI.new_identity(self.conn, self.remote["session_id"], "duly")
        self.razer = RI.new_identity(self.conn, self.remote["session_id"], "razer")

    def raw_post(self, path, payload, token=TOKEN):
        req = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def rows(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT sender, recipient, session_id, status FROM inbox "
                             "WHERE sender<>'rookery' ORDER BY id").fetchall()

    def test_token_send_passes_as_the_node(self):
        code, ans = self.raw_post("/send", {"sender": "duly", "recipient": "razer",
                                            "body": "the wincc-kit handoff", "topic": "wincc-kit"})
        self.assertEqual(code, 200, ans)
        self.assertEqual(ans["outcome"], "sent", ans)
        self.assertEqual(self.rows(), [(self.duly["agent_id"], self.razer["agent_id"],
                                        self.remote["session_id"], "pending")])

    def test_token_send_with_no_sender_passes(self):
        code, ans = self.raw_post("/send", {"recipient": "razer", "body": "hi"})
        self.assertEqual(code, 200, ans)
        self.assertEqual(self.rows()[0][0], self.duly["agent_id"])

    def test_reply_reaches_the_node_inbox(self):
        import mail_gate
        res = mail_gate.submit(self.conn, path="cli", principal=(self.remote, self.razer),
                               raw_sender=self.razer["agent_id"], to="duly", body="got it")
        self.assertEqual(res["outcome"], "sent", res)
        req = urllib.request.Request(self.url + "/inbox?node=duly",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            msgs = json.loads(r.read())["messages"]
        self.assertEqual([m["body"] for m in msgs], ["got it"])

    def test_other_sender_name_is_refused(self):
        code, ans = self.raw_post("/send", {"sender": "razer", "recipient": "duly", "body": "spoof"})
        self.assertEqual((code, ans["error"]), (403, "wrong-sender"), ans)
        self.assertEqual(self.rows(), [])

    def test_other_token_is_refused(self):
        code, ans = self.raw_post("/send", {"sender": "duly", "recipient": "razer", "body": "x"},
                                  token="not-the-token")
        self.assertEqual(code, 401, ans)
        self.assertEqual(self.rows(), [])

    def test_ended_session_is_refused(self):
        RI.end_session(self.conn, "duly-remote")
        code, ans = self.raw_post("/send", {"sender": "duly", "recipient": "razer", "body": "x"})
        self.assertEqual((code, ans["error"]), (403, "no-session"), ans)
        self.assertEqual(self.rows(), [])

    def test_loop_limit_still_applies(self):
        outcomes = [self.raw_post("/send", {"recipient": "razer", "body": f"m{i}", "topic": "t"})[1]["outcome"]
                    for i in range(11)]
        self.assertEqual(outcomes[:10], ["sent"] * 10)
        self.assertEqual(outcomes[10], "loop-held")


class TokenNodeStart(Base):
    def test_needs_a_token(self):
        env = {k: v for k, v in self.env(session=False, node=False).items() if k != "ROOKERY_TOKEN"}
        p = subprocess.run([sys.executable, os.path.join(HERE, "mailroom_server.py"), "--host", "127.0.0.1",
                            "--port", "0", "--token-node", "duly-remote:duly"],
                           env=env, cwd=HERE, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("--token-node needs", p.stderr)


if __name__ == "__main__":
    unittest.main()
