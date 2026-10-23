#!/usr/bin/env python3
"""Mail gate, T016: the 3 tries on the mailroom paths (no session, so all land in `legacy`).

    python3 -m unittest test_paths_mailroom -v

3 paths x 3 tries = 9 cases: POST /send, `mailctl.py send` and A2A `message/send`. Each test class
starts mailroom_server.py on a free local port against the test's temp DB and kills it in tearDown.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from gate_matrix import SendMatrix
from test_hooks import Base, HERE

TOKEN = "T"
STARTUP_LIMIT = 10.0


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerMixin:
    with_session = False

    def setUp(self):
        super().setUp()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "mailroom_server.py"), "--host", "127.0.0.1",
             "--port", str(self.port), "--token", TOKEN],
            env=self.env(session=False, node=False), cwd=HERE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self._stop)
        deadline = time.time() + STARTUP_LIMIT
        while True:
            if self.proc.poll() is not None:
                self.fail(f"server exited: {self.proc.stderr.read().decode()}")
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1).read()
                break
            except (urllib.error.URLError, OSError):
                if time.time() > deadline:
                    self.fail("server did not answer within 10 s")
                time.sleep(0.05)

    def _stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        self.proc.stderr.close()

    def post(self, path, payload):
        req = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())


class HttpSendPath(ServerMixin, SendMatrix, Base):
    def send(self, to):
        ans = self.post("/send", {"sender": "human", "recipient": to, "body": "please review"})
        self.assertEqual(set(ans), {"id", "outcome", "reason"}, ans)
        self.assertEqual(ans["outcome"], "sent")


class MailctlPath(ServerMixin, SendMatrix, Base):
    def send(self, to):
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "mailctl.py"), "send", "--url", self.url,
             "--token", TOKEN, "--to", to, "--from", "human", "--body", "please review"],
            capture_output=True, text=True, env=self.env(session=False, node=False), cwd=HERE,
            timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)


class A2aPath(ServerMixin, SendMatrix, Base):
    def send(self, to):
        ans = self.post("/a2a", {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"parts": [{"kind": "text", "text": "please review"}],
                                   "metadata": {"recipient": to, "from": "human"}}}})
        self.assertIn("result", ans, ans)


class A2aOutcomes(ServerMixin, Base):
    """Contract "A2A outcomes": a mail the gate did not send is never reported as submitted."""

    def env(self, **kw):
        return super().env(ROOKERY_SECURITY="fixtures.policy_deny:DenyPolicy", **kw)

    def rpc(self, method, sender, text):
        return self.post("/a2a", {
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": {"message": {"parts": [{"kind": "text", "text": text}],
                                   "metadata": {"recipient": "researcher", "from": sender}}}})

    def count(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def test_refused_sender_is_an_error_and_stores_nothing(self):
        for method in ("message/send", "message/stream"):
            ans = self.rpc(method, "john smith", "hello")
            self.assertEqual(ans.get("error", {}).get("code"), -32602, ans)
        self.assertEqual(self.count(), 0)

    def test_quarantined_mail_is_rejected_not_submitted(self):
        ans = self.rpc("message/send", "human", "DENY-ME please")
        self.assertEqual(ans["result"]["status"]["state"], "rejected", ans)
        self.assertIn("injection-suspect", ans["result"]["status"]["message"])


if __name__ == "__main__":
    unittest.main()
