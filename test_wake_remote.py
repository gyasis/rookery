#!/usr/bin/env python3
"""Rookery remote check, T019 (research R8): `rookery check` against a local fake mailroom.

    python3 -m unittest test_wake_remote -v
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

import rookery_identity as RI
import rookery_remote as RR

HERE = os.path.dirname(os.path.abspath(__file__))
FIXBIN = os.path.join(HERE, "fixtures", "bin")
TOKEN = "s3cret"
SECRET_BODY = "TOPSECRETBODYTEXT"


def row(i, sender="boss", kind="ask", topic="TASK-1"):
    return {"id": i, "sender": sender, "topic": topic,
            "body": json.dumps({"kind": kind, "text": SECRET_BODY})}


class Mailroom(http.server.BaseHTTPRequestHandler):
    rows, status, seen, redirect, big = [], 200, [], None, False

    def log_message(self, *a):
        pass

    def do_GET(self):
        Mailroom.seen.append((self.path, self.headers.get("Authorization")))
        if Mailroom.redirect and self.path.startswith("/inbox"):
            self.send_response(302)
            self.send_header("Location", Mailroom.redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if Mailroom.big:
            raw = b'{"messages": [' + b" " * (RR.MAX_REPLY + 10) + b"]}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            code, data = 401, {"error": "unauthorized"}
        elif Mailroom.status != 200:
            code, data = Mailroom.status, {"error": "boom"}
        else:
            code, data = 200, {"messages": Mailroom.rows}
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class RemoteBase(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(("ROOKERY_", "HERDR_", "TMUX")):
                os.environ.pop(k)
        self.tmp = tempfile.TemporaryDirectory()
        self.record = os.path.join(self.tmp.name, "herdr.record")
        os.environ.update(HOME=self.tmp.name, HERDR_RECORD=self.record,
                          PATH=FIXBIN + os.pathsep + "/usr/bin:/bin")
        Mailroom.rows, Mailroom.status, Mailroom.seen, Mailroom.redirect, Mailroom.big = [], 200, [], None, False
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Mailroom)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.config = RI.load_config(persist=False)
        self.env = {"HERDR_PANE_ID": "w1:p2"}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.environ.clear()
        os.environ.update(self._saved)
        self.tmp.cleanup()

    def check(self, env=None, token=TOKEN):
        return RR.check_once(self.url, token, "remote1", self.config,
                             env=self.env if env is None else env)

    def pushes(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def cli(self, *args, env=None):
        e = dict(os.environ)
        e.update(env or {})
        return subprocess.run([sys.executable, "rookery_cli.py", "check", *args], cwd=HERE, env=e,
                              capture_output=True, text=True)


class TestRemoteCheck(RemoteBase):
    def test_ask_pushes_once_then_nothing(self):
        Mailroom.rows = [row(1)]
        r = self.check()
        self.assertEqual((r["outcome"], r["count"]), ("pushed", 1))
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(self.pushes()[0][:3], ["agent", "prompt", "w1:p2"])
        self.assertEqual(self.check()["outcome"], "nothing")
        self.assertEqual(len(self.pushes()), 1)

    def test_token_sent_as_bearer(self):
        Mailroom.rows = [row(1)]
        self.check()
        self.assertEqual(Mailroom.seen[0], ("/inbox?node=remote1", f"Bearer {TOKEN}"))

    def test_status_and_self_send_do_not_push(self):
        Mailroom.rows = [row(1, kind="status"), row(2, sender="remote1")]
        self.assertEqual(self.check()["outcome"], "nothing")
        self.assertEqual(self.pushes(), [])

    def test_answer_pushes(self):
        Mailroom.rows = [row(1, kind="answer")]
        self.assertEqual(self.check()["outcome"], "pushed")

    def test_new_mail_after_announced_pushes_again_and_state_is_bounded(self):
        Mailroom.rows = [row(1)]
        self.check()
        Mailroom.rows = [row(2)]       # mail 1 was read meanwhile
        self.assertEqual(self.check()["outcome"], "pushed")
        with open(os.path.join(self.tmp.name, ".rookery", "remote", "remote1.json")) as fh:
            self.assertEqual(json.load(fh)["announced"], [2])

    def test_notice_has_no_body(self):
        Mailroom.rows = [row(1)]
        self.check()
        text = " ".join(self.pushes()[0])
        self.assertIn("rookery inbox", text)
        self.assertNotIn(SECRET_BODY, text)

    def test_server_500_is_check_failed(self):
        Mailroom.status = 500
        r = self.check()
        self.assertEqual(r["outcome"], "check-failed")
        self.assertTrue(r["reason"])

    def test_no_pane_binding(self):
        Mailroom.rows = [row(1)]
        self.assertEqual(self.check(env={})["outcome"], "no-wake-binding")
        self.assertEqual(self.pushes(), [])

    def test_bad_node(self):
        r = RR.check_once(self.url, TOKEN, "../x", self.config, env=self.env)
        self.assertEqual(r["outcome"], "bad-node")

    def test_wake_failed_does_not_record(self):
        Mailroom.rows = [row(1)]
        r = RR.check_once(self.url, TOKEN, "remote1", self.config, env=self.env,
                          run=lambda *a, **k: subprocess.CompletedProcess(a, 1))
        self.assertEqual(r["outcome"], "wake-failed")
        self.assertEqual(self.check()["outcome"], "backoff-wait")      # no retype right after a failure
        self.assertEqual(self.pushes(), [])
        later = RR.check_once(self.url, TOKEN, "remote1", self.config, env=self.env, now=lambda: 9e12)
        self.assertEqual(later["outcome"], "pushed")                     # after the window: types once

    def test_redirect_is_refused_and_the_token_does_not_follow(self):
        Mailroom.rows = [row(1)]
        Mailroom.redirect = self.url.replace("127.0.0.1", "localhost") + "/elsewhere"
        r = self.check()
        self.assertEqual(r["outcome"], "check-failed")
        self.assertFalse([p for p, auth in Mailroom.seen if p.startswith("/elsewhere")])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_oversize_reply_is_refused(self):
        Mailroom.big = True
        r = self.check()
        self.assertEqual(r["outcome"], "check-failed")
        self.assertIn("larger than", r["reason"])

    def test_dripping_reply_hits_the_deadline(self):
        class Drip:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read1(self, n):
                import time as _t
                _t.sleep(0.05)
                return b" "
        saved = RR.CHECK_DEADLINE
        RR.CHECK_DEADLINE = 0.3
        try:
            r = RR.check_once(self.url, TOKEN, "remote1", self.config, env=self.env,
                              opener=lambda req, timeout: Drip())
        finally:
            RR.CHECK_DEADLINE = saved
        self.assertEqual(r["outcome"], "check-failed")
        self.assertIn("took more than", r["reason"])


class TestRemoteCli(RemoteBase):
    def test_cli_pushed_text_and_json(self):
        Mailroom.rows = [row(1)]
        env = {"ROOKERY_URL": self.url, "ROOKERY_NODE": "remote1", "ROOKERY_TOKEN": TOKEN,
               "HERDR_PANE_ID": "w1:p2"}
        p = self.cli(env=env)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "check remote1: pushed"))
        p = self.cli("--json", env=env)
        self.assertEqual(json.loads(p.stdout)["outcome"], "nothing")

    def test_cli_500_exits_zero(self):
        Mailroom.status = 500
        p = self.cli(env={"ROOKERY_URL": self.url, "ROOKERY_NODE": "remote1", "ROOKERY_TOKEN": TOKEN})
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "check remote1: check-failed"))

    def test_cli_missing_url_or_node_exits_2(self):
        self.assertEqual(self.cli(env={"ROOKERY_NODE": "remote1"}).returncode, 2)
        self.assertEqual(self.cli(env={"ROOKERY_URL": self.url}).returncode, 2)


if __name__ == "__main__":
    unittest.main()
