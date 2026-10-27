#!/usr/bin/env python3
"""Rookery remote wake, T021: the Stop hook and a successful remote MCP send start the bounded follower.

    python3 -m unittest test_wake_remote_triggers -v

Seam: rookery_remote.start_follow is replaced by a recorder; rookery_wake.spawn by a recorder too.
"""
import http.server
import importlib
import json
import os
import sys
import threading
import unittest
import unittest.mock as mock

import rookery_remote
import rookery_wake
import test_wake_stop


class StopTrigger(test_wake_stop.StopWake):
    """Inherited StopWake cases are skipped here; test_wake_stop runs them."""

    def run_with(self, boom=False, **env):
        os.environ.update(env)
        follows, spawns = [], []

        def fake(node, now=None):
            follows.append(node)
            if boom:
                raise RuntimeError("boom")
            return "follower-started"
        with mock.patch.object(rookery_remote, "start_follow", fake):
            self.run_stop(lambda args, log_root=None: spawns.append(args) or True)
        return follows, spawns

    def test_remote_calls_start_follow_once_and_no_local_wake(self):
        self.mail()
        follows, spawns = self.run_with(ROOKERY_URL="http://127.0.0.1:1")
        self.assertEqual(follows, [self.lc["agent_id"]])
        self.assertFalse([s for s in spawns if s and s[0] == "wake"])

    def test_without_url_no_follow(self):
        self.mail()
        follows, _ = self.run_with()
        self.assertEqual(follows, [])

    def test_wake_off_no_follow(self):
        follows, _ = self.run_with(ROOKERY_URL="http://127.0.0.1:1", ROOKERY_WAKE="off")
        self.assertEqual(follows, [])

    def test_start_follow_raising_is_silent(self):
        follows, _ = self.run_with(boom=True, ROOKERY_URL="http://127.0.0.1:1")
        self.assertEqual(len(follows), 1)       # run_stop asserts stdout is empty


class McpSend(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        self.srv = None

    def tearDown(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
        os.environ.clear()
        os.environ.update(self._saved)
        sys.modules.pop("rookery_mcp", None)

    def serve(self, code):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.dumps({"id": 7} if code == 200 else {"error": "x"}).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_port}"

    def send(self, url, **env):
        os.environ.update(ROOKERY_URL=url, ROOKERY_TOKEN="t", ROOKERY_NODE="n1", **env)
        sys.modules.pop("rookery_mcp", None)
        m = importlib.import_module("rookery_mcp")
        follows = []
        with mock.patch.object(rookery_remote, "start_follow", lambda n, now=None: follows.append(n)):
            out = m.send("bob", "hi", mock.Mock())
        return out, follows

    def test_successful_send_triggers_once(self):
        out, follows = self.send(self.serve(200))
        self.assertTrue(out.startswith("sent #7"), out)
        self.assertEqual(follows, ["n1"])

    def test_failed_send_triggers_nothing(self):
        out, follows = self.send(self.serve(500))
        self.assertTrue(out.startswith("refused"), out)
        self.assertEqual(follows, [])

    def test_refused_connection_triggers_nothing(self):
        out, follows = self.send("http://127.0.0.1:1")
        self.assertTrue(out.startswith("refused"), out)
        self.assertEqual(follows, [])

    def test_wake_off_triggers_nothing(self):
        out, follows = self.send(self.serve(200), ROOKERY_WAKE="off")
        self.assertTrue(out.startswith("sent"), out)
        self.assertEqual(follows, [])


if __name__ == "__main__":
    unittest.main()
