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
        self.assertNotIn("fixture boom", ans["detail"])
        self.assertEqual(ans["detail"], mailroom_server._SEND_ERRORS["send-failed"][1])
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
        # the sender is `a2a-<from>` and cleaned, so an empty body is what is refused here
        for text in ("", ""):
            ans = self.rpc(text)
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
            self.assertEqual(self.rpc("", method=method)["error"]["code"],
                             -32602)
            self.assertEqual(self.rpc("hello", recipient="forbidden", method=method
                                      )["error"]["code"], -32003)
            self.assertEqual(self.rpc("BOOM-ME", method=method)["error"]["code"], -32603)


class ReadsByName(GateHttpBase):
    """T033: /inbox, /claim, /ack by node name; SC-009 read paths change no status."""

    def get(self, path):
        req = urllib.request.Request(self.url + path,
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def put(self, recipient, sid, body="b"):
        import rookery as R
        return R.send(self.conn, "x", recipient, body, hold=False, session_id=sid)

    def snap(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT id, status, delivered, claimed_at, delivered_at "
                             "FROM inbox ORDER BY id").fetchall()

    def status(self, i):
        return {r[0]: r[1] for r in self.snap()}[i]

    def setUp(self):
        super().setUp()
        self.sid = self.alpha["session_id"]
        self.legacy = self.put("N", "legacy")
        self.sess = self.put("N", self.sid)
        self.byid = self.put(self.researcher["agent_id"], self.sid)
        self.other = self.put("M", "legacy")

    def test_malformed_ids_get_400_bad_input(self):
        before = self.snap()
        for path in ("/claim", "/ack"):
            for node in ({}, {"node": "N"}):
                for ids in ([[1]], "1", {"a": 1}, [True], [1.5], ["1"], 7):
                    code, ans = self.raw(path, {**node, "ids": ids})
                    self.assertEqual((code, ans["error"]), (400, "bad-input"), (path, node, ids))
                    self.assertTrue(ans["detail"])
        self.assertEqual(self.snap(), before)

    def test_old_mailctl_without_node_still_works(self):
        with sqlite3.connect(self.db) as c:
            mid = c.execute("INSERT INTO inbox(sender, recipient, body, status, delivered, created_at,"
                            " session_id) VALUES('x','oldpeer','hi','pending',0,'2026-01-01','legacy')"
                            ).lastrowid
        self.assertEqual(self.post("/claim", {"ids": [mid]}), {"ok": True})
        self.assertEqual(self.post("/ack", {"ids": [mid]}), {"ok": True})
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status FROM inbox WHERE id=?", (mid,)).fetchone()[0],
                             "done")

    def test_inbox_returns_rows_to_name_and_agent_id_only_and_changes_nothing(self):
        before = self.snap()
        rows = self.get("/inbox?node=N")["messages"]
        self.assertEqual({r["id"] for r in rows}, {self.legacy, self.sess})
        for r in rows:
            self.assertEqual((r["recipient"], r["status"]), ("N", "pending"))
            self.assertIn("body", r)
        rows = self.get("/inbox?node=researcher")["messages"]
        self.assertEqual([r["id"] for r in rows], [self.byid])
        self.assertEqual(self.snap(), before)

    def test_claim_and_ack_skip_ids_of_other_names(self):
        before = self.snap()
        self.assertEqual(self.post("/claim", {"node": "N", "ids": [self.other]})["skipped"],
                         [self.other])
        self.assertEqual(self.post("/ack", {"node": "N", "ids": [self.other]})["skipped"],
                         [self.other])
        self.assertEqual(self.snap(), before)

    def test_claim_all_then_ack_leaves_none_inflight(self):
        ids = [m["id"] for m in self.get("/inbox?node=N")["messages"]]
        self.assertEqual(sorted(self.post("/claim", {"node": "N", "ids": ids})["ok"]), sorted(ids))
        self.assertEqual(self.status(self.legacy), "inflight")
        self.assertEqual(self.status(self.other), "pending")
        res = self.post("/ack", {"node": "N", "ids": ids})
        self.assertEqual(sorted(res["ok"]), sorted(ids))
        self.assertEqual([r for r in self.snap() if r[1] == "inflight"], [])
        self.assertEqual(self.status(self.legacy), "done")

    def test_claim_without_ids_takes_all_pending_of_name(self):
        self.post("/claim", {"node": "N"})
        self.assertEqual({self.status(self.legacy), self.status(self.sess)}, {"inflight"})
        self.assertEqual(self.status(self.byid), "pending")

    def test_read_paths_change_no_row(self):
        import subprocess, sys
        before = self.snap()
        self.get("/inbox?node=N")
        out = subprocess.run([sys.executable, "mailctl.py", "inbox", "--url", self.url,
                              "--node", "N", "--token", TOKEN], capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("N", out.stdout)
        res = self.raw("/a2a", {"jsonrpc": "2.0", "id": 1, "method": "tasks/get",
                                "params": {"id": f"task-{self.legacy}"}})[1]
        self.assertIn("result", res)
        self.assertEqual(self.snap(), before)


if __name__ == "__main__":
    unittest.main()
