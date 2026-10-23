#!/usr/bin/env python3
"""Mail gate, T024: HTTP status codes and A2A answers of mailroom_server.py for each gate outcome.

    python3 -m unittest test_mailroom_gate -v

Each test starts mailroom_server.py (with a token) on a free local port against the test's temp DB.
"""
import json
import os
import sqlite3
import unittest
import urllib.error
import urllib.request

import mailroom_server
from test_hooks import Base
from test_paths_mailroom import ServerMixin, TOKEN


class GateHttpBase(ServerMixin, Base):
    def env(self, **kw):
        return super().env(ROOKERY_SECURITY="fixtures.policy_gate_http:GateHttpPolicy", **kw)

    def raw(self, path, payload, token=TOKEN):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def count(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def gate_log(self):
        path = os.path.join(self.root, "logs", "gate.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def send(self, sender="human", body="please review", **kw):
        return self.raw("/send", {"sender": sender, "recipient": "researcher", "body": body}, **kw)


class SendStatus(GateHttpBase):
    def test_no_token_and_bad_token_get_401_and_store_nothing(self):
        for token in (None, "wrong"):
            code, ans = self.send(token=token)
            self.assertEqual((code, ans), (401, {"error": "unauthorized"}))
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.gate_log(), [])

    def test_system_sender_gets_403_with_body(self):
        code, ans = self.send(sender="rookery")
        self.assertEqual(code, 403)
        self.assertEqual(ans["error"], "system-name")
        self.assertTrue(ans["detail"])
        self.assertEqual(self.count(), 0)

    def test_bad_sender_gets_400_bad_input(self):
        code, ans = self.send(sender="x; rm")
        self.assertEqual(code, 400)
        self.assertEqual(ans["error"], "bad-input")
        self.assertTrue(ans["detail"])
        self.assertEqual(self.count(), 0)

    def test_empty_body_gets_400_bad_input(self):
        code, ans = self.send(body="")
        self.assertEqual((code, ans["error"]), (400, "bad-input"))

    def test_other_agents_name_is_sent_and_logged_as_asked_sender(self):
        code, ans = self.send(sender="swe")
        self.assertEqual(code, 200)
        self.assertEqual(ans["outcome"], "sent")
        rec = self.gate_log()[-1]
        self.assertEqual((rec["asked_sender"], rec["outcome"]), ("swe", "sent"))
        self.assertEqual(self.count(), 1)

    def test_one_log_record_per_refusal(self):
        self.send(sender="rookery")
        self.send(sender="x; rm")
        recs = self.gate_log()
        self.assertEqual([(r["outcome"], r["reason"]) for r in recs],
                         [("refused", "system-name"), ("refused", "bad-input")])

    def test_quarantined_stays_200(self):
        code, ans = self.send(body="DENY-ME please")
        self.assertEqual(code, 200)
        self.assertEqual((ans["outcome"], ans["reason"]), ("quarantined", "injection-suspect"))

    def test_loop_held_stays_200(self):
        self.set_loop_limit(1)
        self.assertEqual(self.send()[1]["outcome"], "sent")
        code, ans = self.send()
        self.assertEqual(code, 200)
        self.assertEqual((ans["outcome"], ans["reason"]), ("loop-held", "loop"))

    def test_db_busy_gets_503_with_error_body(self):
        code, ans = self.send(body="LOCK-ME")
        self.assertEqual(code, 503)
        self.assertEqual(ans["error"], "db-busy")
        self.assertTrue(ans["detail"])
        self.assertEqual(self.count(), 0)

    def test_send_failed_gets_500_with_error_body(self):
        code, ans = self.send(body="BOOM-ME")
        self.assertEqual(code, 500)
        self.assertEqual(ans["error"], "send-failed")
        self.assertIn("fixture boom", ans["detail"])
        self.assertEqual(self.count(), 0)

    # helpers
    def set_loop_limit(self, n):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": n}, fh)


class A2aMapping(GateHttpBase):
    """Contract "A2A outcomes": one case for each row, on message/send and message/stream."""

    def rpc(self, text, sender="human", recipient="researcher", method="message/send"):
        return self.raw("/a2a", {
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": {"message": {"parts": [{"kind": "text", "text": text}],
                                   "metadata": {"recipient": recipient, "from": sender}}}})[1]

    def test_sent_is_submitted(self):
        ans = self.rpc("hello")
        self.assertEqual(ans["result"]["status"]["state"], "submitted", ans)
        self.assertEqual(self.count(), 1)

    def test_quarantined_is_rejected_with_reason(self):
        ans = self.rpc("DENY-ME")
        self.assertEqual(ans["result"]["status"]["state"], "rejected", ans)
        self.assertIn("injection-suspect", ans["result"]["status"]["message"])

    def test_loop_held_is_rejected_with_reason(self):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": 1}, fh)
        self.assertEqual(self.rpc("one")["result"]["status"]["state"], "submitted")
        ans = self.rpc("two")
        self.assertEqual(ans["result"]["status"]["state"], "rejected", ans)
        self.assertIn("loop", ans["result"]["status"]["message"])

    def test_refused_is_32602(self):
        # the sender is `a2a-<from>`, so only bad-input can be refused here, never system-name
        for sender in ("x; rm", "john smith"):
            ans = self.rpc("hello", sender=sender)
            self.assertEqual(ans["error"]["code"], -32602, ans)
        self.assertEqual(self.count(), 0)

    def test_authorize_deny_is_32003(self):
        ans = self.rpc("hello", recipient="forbidden")
        self.assertEqual(ans["error"]["code"], -32003, ans)
        self.assertEqual(self.count(), 0)

    def test_db_busy_is_32006(self):
        ans = self.rpc("LOCK-ME")
        self.assertEqual(ans["error"]["code"], -32006, ans)

    def test_send_failed_is_32603(self):
        ans = self.rpc("BOOM-ME")
        self.assertEqual(ans["error"]["code"], -32603, ans)
        self.assertEqual(self.count(), 0)

    def test_stream_uses_the_same_mapping(self):
        for method in ("message/send", "message/stream"):
            self.assertEqual(self.rpc("hello", sender="x; rm", method=method)["error"]["code"],
                             -32602)
            self.assertEqual(self.rpc("hello", recipient="forbidden", method=method
                                      )["error"]["code"], -32003)
            self.assertEqual(self.rpc("BOOM-ME", method=method)["error"]["code"], -32603)


if __name__ == "__main__":
    unittest.main()
