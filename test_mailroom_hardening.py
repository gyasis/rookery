#!/usr/bin/env python3
"""Mail gate, final audit: HTTP hardening of mailroom_server.py (XSS, bad bodies, A2A text and sender).

    python3 -m unittest test_mailroom_hardening -v
"""
import http.client
import io
import json
import re
import sqlite3
import unittest
import urllib.error
import urllib.request

import handshake
import mailroom_handshake
import mailroom_server
from test_hooks import Base
from test_mailroom_gate import GateHttpBase
from test_paths_mailroom import ServerMixin, TOKEN

XSS = "<img src=x onerror=alert(1)>"


class _FakeHandler:
    def __init__(self, path):
        self.path = path
        self.code = None
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.code = code

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass


class ApproveFormEscapes(unittest.TestCase):
    def test_form_has_no_raw_markup_from_stored_values(self):
        entry = handshake.new_request(XSS, "AAAA", "</script><script>alert(2)</script>")
        self.addCleanup(handshake.PENDING.pop, entry["request_id"], None)
        h = _FakeHandler("/approve?request_id=" + entry["request_id"])
        mailroom_handshake.handle_approve_form(h, None, None, None)
        page = h.wfile.getvalue().decode()
        self.assertEqual(h.code, 200)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)
        self.assertEqual(page.count("<script>"), 1, page)
        self.assertEqual(page.count("</script>"), 1, page)

    def test_request_id_in_script_is_json_escaped(self):
        entry = handshake.new_request("n", "AAAA", "s")
        rid = "x'</script><b>"
        handshake.PENDING[rid] = {**entry, "request_id": rid}
        self.addCleanup(handshake.PENDING.pop, rid, None)
        h = _FakeHandler("/approve?request_id=" + __import__("urllib.parse").parse.quote(rid))
        mailroom_handshake.handle_approve_form(h, None, None, None)
        page = h.wfile.getvalue().decode()
        self.assertEqual(page.count("</script>"), 1, page)
        self.assertNotIn("<b>", page)


class JoinAndBodies(GateHttpBase):
    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.putrequest(method, path)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        c.endheaders(body)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw)

    def auth(self, **extra):
        return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", **extra}

    def health(self):
        with urllib.request.urlopen(self.url + "/health", timeout=5) as r:
            self.assertEqual(r.status, 200)

    # fix 1
    def test_join_refuses_markup_in_node_id_and_slug(self):
        for field in ("node_id", "slug"):
            d = {"node_id": "ok-node", "pubkey_b64": "AAAA", "slug": "ok-slug", field: XSS}
            code, ans = self.raw("/join", d, token=None)
            self.assertEqual((code, ans["error"]), (400, "bad-input"), (field, ans))

    def test_join_accepts_normal_names(self):
        code, ans = self.raw("/join", {"node_id": "peer-host.1", "pubkey_b64": "AAAA",
                                       "slug": "red-green-blue"}, token=None)
        self.assertEqual(code, 200, ans)

    # fix 2
    def test_non_ascii_bearer_is_401_and_server_lives(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("GET", "/inbox?node=N")
        c.putheader("Authorization", "Bearer café".encode("utf-8").decode("latin-1"))
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 401)
        r.read()
        c.close()
        self.health()

    # fix 3
    def test_invalid_json_gets_400(self):
        for path in ("/send", "/a2a"):
            code, ans = self.request("POST", path, b"{not json", self.auth(**{"Content-Length": "9"}))
            self.assertEqual((code, ans["error"]), (400, "bad-input"), path)
            self.assertTrue(ans["detail"])

    def test_public_join_with_invalid_json_no_token_gets_400(self):
        code, ans = self.request("POST", "/join", b"{nope", {"Content-Length": "5"})
        self.assertEqual((code, ans["error"]), (400, "bad-input"))

    def test_json_list_body_gets_400(self):
        for path in ("/send", "/a2a", "/claim", "/join"):
            code, ans = self.request("POST", path, b"[1,2]",
                                     self.auth(**{"Content-Length": "5"}))
            self.assertEqual((code, ans["error"]), (400, "bad-input"), path)

    def test_inbox_and_thread_without_node_get_400(self):
        for path in ("/inbox", "/thread"):
            code, ans = self.request("GET", path, None, self.auth())
            self.assertEqual((code, ans["error"]), (400, "bad-input"), path)
        self.health()

    def test_a2a_metadata_as_list_gets_an_answer(self):
        code, ans = self.raw("/a2a", {"jsonrpc": "2.0", "id": 1, "method": "message/send",
                                      "params": {"message": {"parts": [], "metadata": [1]}}})
        self.assertIn(code, (200, 400))
        self.assertTrue("error" in ans or "result" in ans, ans)
        self.health()

    def test_negative_or_bad_content_length_gets_400(self):
        for n in ("-5", "abc"):
            code, ans = self.request("POST", "/send", b"", self.auth(**{"Content-Length": n}))
            self.assertEqual((code, ans["error"]), (400, "bad-input"), n)

    def test_oversize_body_gets_413_without_reading_it(self):
        code, ans = self.request("POST", "/send", b"",
                                 self.auth(**{"Content-Length": str(10 * 1024 * 1024)}))
        self.assertEqual((code, ans["error"]), (413, "too-large"))
        self.health()

    def test_missing_key_gets_400(self):
        code, ans = self.raw("/heartbeat", {})
        self.assertEqual((code, ans["error"]), (400, "bad-input"))
        self.health()

    def test_unexpected_exception_gets_500_fixed_text(self):
        code, ans = self.raw("/register", {"node_id": {"a": 1}})   # sqlite rejects a dict
        self.assertEqual((code, ans["error"]), (500, "server-error"))
        self.assertNotIn("sqlite", json.dumps(ans).lower())
        self.assertNotIn("Error binding", json.dumps(ans))
        self.health()


class A2aTextAndSender(GateHttpBase):
    def rpc(self, text, sender="human", method="message/send"):
        return self.raw("/a2a", {
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": {"message": {"parts": [{"kind": "text", "text": text}],
                                   "metadata": {"recipient": "researcher", "from": sender}}}})[1]

    # fix 4
    def test_send_failed_message_has_no_exception_text(self):
        for method in ("message/send", "message/stream"):
            ans = self.rpc("BOOM-ME", method=method)
            self.assertEqual(ans["error"]["code"], -32603)
            self.assertNotIn("fixture boom", ans["error"]["message"])
            self.assertEqual(ans["error"]["message"],
                             "send-failed: " + mailroom_server._SEND_ERRORS["send-failed"][1])

    # fix 5
    CASES = {"My Client": "a2a-My-Client", "José": "a2a-Jos", "": "a2a-client",
             "https://x.example/a?b=c": "a2a-https-x.example-a-b-c"}

    def last_sender(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT sender FROM inbox ORDER BY id DESC LIMIT 1").fetchone()[0]

    def test_free_form_from_is_sanitized_not_refused(self):
        for sender, want in self.CASES.items():
            ans = self.rpc("hello", sender=sender)
            self.assertNotIn("error", ans, (sender, ans))
            self.assertEqual(ans["result"]["status"]["state"], "submitted", ans)
            self.assertEqual(self.last_sender(), want, sender)

    def test_stream_sanitizes_from_too(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "message/stream",
                           "params": {"message": {"parts": [{"kind": "text", "text": "hi"}],
                                                  "metadata": {"recipient": "researcher",
                                                               "from": "My Client"}}}}).encode()
        req = urllib.request.Request(self.url + "/a2a", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            first = r.readline().decode()      # first SSE event: submitted; do not wait for more
        self.assertIn("submitted", first)
        self.assertEqual(self.last_sender(), "a2a-My-Client")

    def test_long_from_is_truncated_to_64(self):
        ans = self.rpc("hello", sender="a" * 80)
        self.assertEqual(ans["result"]["status"]["state"], "submitted", ans)
        with sqlite3.connect(self.db) as c:
            got = c.execute("SELECT sender FROM inbox ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertTrue(re.fullmatch(r"a2a-[A-Za-z0-9._-]+", got) and len(got) <= 64, got)

    # fix 6 (SC-009)
    def test_tasks_get_changes_no_row(self):
        ans = self.rpc("hello")
        tid = ans["result"]["id"]
        snap = lambda: sqlite3.connect(self.db).execute(
            "SELECT id, status, delivered, claimed_at, delivered_at FROM inbox ORDER BY id"
        ).fetchall()
        before = snap()
        got = self.raw("/a2a", {"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                                "params": {"id": tid}})[1]
        self.assertIn("result", got)
        self.assertEqual(snap(), before)


if __name__ == "__main__":
    unittest.main()
