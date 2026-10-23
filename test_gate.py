#!/usr/bin/env python3
"""Mail gate, T008: mail_gate.submit, system_send, quarantine_malformed, principal_from_env.

    python3 -m unittest test_gate -v

Contract: specs/002-mail-gate/contracts/gate.md. Order of checks: data-model.md.
Each test uses a temp database and a temp Rookery root (test_hooks.Base).
"""
import json
import os
import threading
import unittest

import mail_gate as G
import rookery as R
import rookery_identity as RI
import security
import turn_block as TB
from test_hooks import Base


class GateBase(Base):
    def setUp(self):
        super().setUp()
        for k in ("ROOKERY_SECURITY", "ROOKERY_HOLD"):
            self._saved.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)
        security._POLICY = None
        self.beta = RI.new_session(self.conn, name="beta")
        self.beta_only = RI.new_identity(self.conn, self.beta["session_id"], "beta-only")
        self.beta_me = RI.new_identity(self.conn, self.beta["session_id"], "me")   # same name as alpha's
        self.principal = (self.alpha, self.me)

    def tearDown(self):
        security._POLICY = None
        super().tearDown()

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM inbox ORDER BY id")]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def gate_log(self):
        p = os.path.join(self.root, "logs", "gate.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def submit(self, to="researcher", body="hello", principal="default", **kw):
        kw.setdefault("path", "send")
        kw.setdefault("raw_sender", "human")
        if principal == "default":
            principal = self.principal
        return G.submit(self.conn, principal=principal, to=to, body=body, root=self.root, **kw)

    def use_deny(self):
        os.environ["ROOKERY_SECURITY"] = "fixtures.policy_deny:DenyPolicy"
        security._POLICY = None


class RemoteAddress(GateBase):
    def test_at_address_is_stored_as_given_and_plain_unknown_is_not(self):
        r = self.submit(to="bob@peer1")
        self.assertEqual(r["outcome"], "sent", r)
        row = self.conn.execute("SELECT * FROM inbox WHERE id=?", (r["msg_id"],)).fetchone()
        self.assertEqual((row["recipient"], row["status"]), ("bob@peer1", "pending"))
        self.assertEqual(self.submit(to="nobody")["reason"], "wrong-recipient")


class Sender(GateBase):
    def test_system_name_refused(self):
        r = self.submit(asked_sender="rookery")
        self.assertEqual((r["outcome"], r["reason"], r["msg_id"]), ("refused", "system-name", None))
        self.assertEqual(self.count(), 0)

    def test_system_raw_name_refused(self):
        r = self.submit(principal=None, raw_sender="postmaster")
        self.assertEqual((r["outcome"], r["reason"]), ("refused", "system-name"))
        self.assertEqual(self.count(), 0)

    def test_bad_name_refused(self):
        r = self.submit(asked_sender="x; rm -rf ~")
        self.assertEqual((r["outcome"], r["reason"]), ("refused", "bad-input"))
        self.assertEqual(self.count(), 0)

    def test_system_name_any_case_or_peer_refused(self):
        for name in ("Rookery", "POSTMASTER", "postmaster@x", "Relay@peer1"):
            r = self.submit(asked_sender=name)
            self.assertEqual((r["outcome"], r["reason"]), ("refused", "system-name"), name)
        self.assertEqual(self.count(), 0)

    def test_non_text_sender_refused(self):
        r = self.submit(asked_sender=["x"])
        self.assertEqual((r["outcome"], r["reason"]), ("refused", "bad-input"))

    def test_empty_or_non_text_recipient_and_body_refused(self):
        for principal in ("default", None):
            for to, body in (("", "hi"), (None, "hi"), ("researcher", ""), ("researcher", None)):
                r = self.submit(to=to, body=body, principal=principal)
                self.assertEqual((r["outcome"], r["reason"]), ("refused", "bad-input"), (to, body))
        self.assertEqual(self.count(), 0)

    def test_double_at_refused(self):
        r = self.submit(asked_sender="alice@@x")
        self.assertEqual((r["outcome"], r["reason"]), ("refused", "bad-input"))
        self.assertEqual(self.count(), 0)

    def test_peer_sender_sent(self):
        r = self.submit(asked_sender="alice@peer1")
        self.assertEqual(r["outcome"], "sent")
        self.assertEqual(self.rows()[0]["sender"], "alice@peer1")

    def test_asked_sender_differs_from_principal(self):
        r = self.submit(asked_sender="someone")
        self.assertEqual(r["outcome"], "sent")
        self.assertEqual(self.rows()[0]["sender"], "someone")
        rec = self.gate_log()[-1]
        self.assertEqual(rec["asked_sender"], "someone")
        self.assertEqual(rec["principal"], self.me["agent_id"])
        self.assertNotEqual(rec["asked_sender"], rec["principal"])

    def test_principal_sender_is_agent_id(self):
        r = self.submit()
        row = self.rows()[0]
        self.assertEqual((r["outcome"], row["sender"]), ("sent", self.me["agent_id"]))


class NoSession(GateBase):
    def test_legacy_pending_with_warning(self):
        r = self.submit(principal=None, raw_sender="old-node", to="bob", asked_sender=None)
        self.assertEqual((r["outcome"], r["warning"]), ("sent", "no-session"))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["session_id"], row["sender"], row["recipient"]),
                         ("pending", "legacy", "old-node", "bob"))
        self.assertEqual(self.count(), 1)           # no quarantine row, no bounce
        rec = self.gate_log()[-1]
        self.assertEqual((rec["warning"], rec["principal"], rec["session"]), ("no-session", None, "legacy"))

    def test_asked_sender_without_principal(self):
        self.submit(principal=None, asked_sender="old-node", to="bob")
        self.assertEqual(self.rows()[0]["sender"], "old-node")

    def test_unknown_and_other_session_recipient_not_checked(self):
        a = self.submit(principal=None, to="nobody-here")
        b = self.submit(principal=None, to="beta-only")
        self.assertEqual((a["outcome"], b["outcome"]), ("sent", "sent"))
        self.assertEqual([r["recipient"] for r in self.rows()], ["nobody-here", "beta-only"])
        self.assertEqual({r["session_id"] for r in self.rows()}, {"legacy"})

    def test_recipient_name_is_not_rewritten(self):
        self.submit(principal=None, to="researcher")
        self.assertEqual(self.rows()[0]["recipient"], "researcher")

    def test_warning_is_none_with_principal(self):
        self.assertIsNone(self.submit()["warning"])


class Recipient(GateBase):
    def test_off_team(self):
        r = self.submit(to="beta-only")
        self.assertEqual((r["outcome"], r["reason"]), ("quarantined", "off-team"))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["reason"]), ("quarantined", "off-team"))
        self.assertIsNotNone(r["bounce_id"])
        self.assertEqual(self.rows()[1]["recipient"], self.me["agent_id"])

    def test_wrong_recipient_with_suggestion(self):
        r = self.submit(to="researchr")
        self.assertEqual((r["outcome"], r["reason"]), ("quarantined", "wrong-recipient"))
        bounce = json.loads(R.get_message(self.conn, r["bounce_id"])["body"])
        self.assertEqual(bounce["suggest"], ["researcher"])

    def test_name_rewritten_to_agent_id(self):
        r = self.submit(to="researcher")
        row = R.get_message(self.conn, r["msg_id"])
        self.assertEqual((row["recipient"], row["status"], row["session_id"]),
                         (self.researcher["agent_id"], "pending", self.alpha["session_id"]))

    def test_name_in_two_sessions_resolves_in_sender_session(self):
        r = self.submit(to="me", raw_sender="x", principal=(self.alpha, self.swe))
        self.assertEqual(R.get_message(self.conn, r["msg_id"])["recipient"], self.me["agent_id"])
        r = self.submit(to="me", principal=(self.beta, self.beta_only))
        self.assertEqual(R.get_message(self.conn, r["msg_id"])["recipient"], self.beta_me["agent_id"])

    def test_pre_store_hold(self):
        seen = []

        def pre(row):
            seen.append(row["agent_id"])
            return ("held", "handoff-pending")
        r = self.submit(pre_store=pre)
        row = R.get_message(self.conn, r["msg_id"])
        self.assertEqual((row["status"], row["reason"]), ("held", "handoff-pending"))
        self.assertEqual(seen, [self.researcher["agent_id"]])

    def test_pre_store_none_sends(self):
        self.assertEqual(self.submit(pre_store=lambda row: None)["outcome"], "sent")

    def test_quarantine_comes_before_hold(self):
        called = []
        r = self.submit(to="beta-only", pre_store=lambda row: called.append(1) or ("held", "x"))
        self.assertEqual(r["reason"], "off-team")
        self.assertEqual(called, [])


class Screening(GateBase):
    def test_deny_with_session(self):
        self.use_deny()
        r = self.submit(body="DENY-ME")
        self.assertEqual((r["outcome"], r["reason"]), ("quarantined", "injection-suspect"))
        self.assertIsNotNone(r["bounce_id"])
        b = R.get_message(self.conn, r["bounce_id"])
        self.assertEqual((b["recipient"], b["session_id"]), (self.me["agent_id"], self.alpha["session_id"]))

    def test_deny_without_session_goes_to_legacy(self):
        self.use_deny()
        r = self.submit(principal=None, raw_sender="old-node", to="bob", body="DENY-ME")
        self.assertEqual((r["outcome"], r["reason"]), ("quarantined", "injection-suspect"))
        self.assertEqual(R.get_message(self.conn, r["msg_id"])["session_id"], "legacy")
        b = R.get_message(self.conn, r["bounce_id"])
        self.assertEqual((b["recipient"], b["session_id"]), ("old-node", "legacy"))

    def test_allow_passes(self):
        self.use_deny()
        self.assertEqual(self.submit(body="fine")["outcome"], "sent")

    def test_screening_runs_before_the_write_lock(self):
        entered, release = threading.Event(), threading.Event()

        class Slow(security.SecurityPolicy):
            def inspect_inbound(self, sender, recipient, body):
                entered.set()
                release.wait(10)
                return security.ALLOW
        security.set_policy(Slow())
        out = {}

        def run():
            conn = TB.open_db()
            try:
                out["r"] = G.submit(conn, path="send", principal=self.principal, raw_sender="h",
                                    to="researcher", body="x", root=self.root)
            finally:
                conn.close()
        t = threading.Thread(target=run)
        t.start()
        try:
            self.assertTrue(entered.wait(5))
            c2 = TB.open_db()
            c2.execute("PRAGMA busy_timeout=300")
            c2.execute("INSERT INTO inbox(sender, recipient, body, status, delivered, created_at) "
                       "VALUES('a','b','c','pending',0,0)")
            c2.commit()                     # a held write lock would raise "database is locked"
            c2.close()
        finally:
            release.set()
            t.join(10)
        self.assertEqual(out["r"]["outcome"], "sent")


class Loop(GateBase):
    def test_loop_limit_holds_without_bounce(self):
        limit, _ = TB.loop_limits()
        for _ in range(limit):
            self.assertEqual(self.submit(path="mcp-stdio", task="t1")["outcome"], "sent")
        r = self.submit(path="mcp-stdio", task="t1")
        self.assertEqual((r["outcome"], r["reason"], r["bounce_id"]), ("loop-held", "loop", None))
        row = R.get_message(self.conn, r["msg_id"])
        self.assertEqual((row["status"], row["reason"]), ("held", "loop"))
        self.assertEqual(self.count(), limit + 1)

    def test_no_session_sender_counted_in_legacy(self):
        limit, _ = TB.loop_limits()
        for _ in range(limit):
            self.submit(principal=None, raw_sender="n1", to="n2", task="t")
        r = self.submit(principal=None, raw_sender="n1", to="n2", task="t")
        self.assertEqual(r["outcome"], "loop-held")
        sessions = [x[0] for x in self.conn.execute("SELECT DISTINCT session_id FROM loop_counts")]
        self.assertEqual(sessions, ["legacy"])


class Peer(GateBase):
    def test_valid_peer_pending_with_session(self):
        r = self.submit(path="mcp-stdio")
        row = R.get_message(self.conn, r["msg_id"])
        self.assertEqual((r["outcome"], row["status"], row["session_id"], row["sender"]),
                         ("sent", "pending", self.alpha["session_id"], self.me["agent_id"]))
        self.assertIsNone(r["bounce_id"])


class Failure(GateBase):
    def test_locked_database_gives_db_busy(self):
        other = TB.open_db()
        other.execute("BEGIN IMMEDIATE")
        try:
            r = self.submit()
        finally:
            other.rollback()
            other.close()
        self.assertEqual(r["outcome"], "db-busy")
        self.assertIsNone(r["msg_id"])
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.gate_log()[-1]["outcome"], "db-busy")

    def test_unexpected_error_gives_send_failed_and_stores_nothing(self):
        def boom(row):
            raise RuntimeError("boom")
        r = self.submit(pre_store=boom)
        self.assertEqual(r["outcome"], "send-failed")
        self.assertIn("boom", r["reason"])
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.conn.in_transaction)


class Log(GateBase):
    def test_one_record_per_call_with_all_fields(self):
        self.submit()
        self.submit(asked_sender="rookery")
        recs = self.gate_log()
        self.assertEqual(len(recs), 2)
        for k in ("ts", "path", "principal", "sender", "asked_sender", "recipient", "session",
                  "outcome", "reason", "warning", "msg_id", "bounce_id", "decided_by"):
            self.assertIn(k, recs[0])
        self.assertEqual((recs[0]["outcome"], recs[0]["path"], recs[0]["decided_by"]),
                         ("sent", "send", "gate"))
        self.assertEqual((recs[1]["outcome"], recs[1]["reason"]), ("refused", "system-name"))

    def test_system_send_logs(self):
        G.system_send(self.conn, "postmaster", "bob", "hi", session_id="legacy", root=self.root)
        rec = self.gate_log()[-1]
        self.assertEqual((rec["sender"], rec["outcome"], rec["path"]), ("postmaster", "sent", "system"))


class SystemSend(GateBase):
    def test_non_system_sender_raises(self):
        with self.assertRaises(ValueError):
            G.system_send(self.conn, "alice", "bob", "hi", session_id="legacy", root=self.root)
        self.assertEqual(self.count(), 0)

    def test_refused_and_failed_system_send_are_logged(self):
        with self.assertRaises(ValueError):
            G.system_send(self.conn, "alice", "bob", "hi", session_id="legacy", root=self.root)
        self.assertEqual((self.gate_log()[-1]["outcome"], self.gate_log()[-1]["reason"]),
                         ("refused", "system-name"))
        with self.assertRaises(Exception):
            G.system_send(self.conn, "postmaster", "bob", None, session_id="legacy", root=self.root)
        self.assertEqual(self.gate_log()[-1]["outcome"], "send-failed")

    def test_each_system_sender_stores_one_row(self):
        for name in ("postmaster", "relay", "rookery"):
            before = self.count()
            mid = G.system_send(self.conn, name, "bob", "hi", topic="t", session_id="legacy",
                                root=self.root)
            self.assertEqual(self.count(), before + 1)
            row = R.get_message(self.conn, mid)
            self.assertEqual((row["sender"], row["status"], row["topic"]), (name, "pending", "t"))


class LowLevelInsert(GateBase):
    def test_send_needs_a_session_id(self):                  # FR-005
        with self.assertRaises(TypeError):
            R.send(self.conn, "a", "b", "x")
        with self.assertRaises(ValueError):
            R.send(self.conn, "a", "b", "x", session_id=None)
        self.assertEqual(self.count(), 0)


class Malformed(GateBase):
    def test_quarantine_malformed(self):
        ctx = {"session": self.alpha, "identity": self.me, "root": self.root}
        mid = G.quarantine_malformed(self.conn, ctx, self.researcher["agent_id"], "{bad", None,
                                     "malformed", "Fix it.")
        row = R.get_message(self.conn, mid)
        self.assertEqual((row["status"], row["reason"], row["recipient"]),
                         ("quarantined", "malformed", "rookery"))    # a real agent ID is masked
        self.assertEqual(self.count(), 2)                            # + the bounce
        self.assertIn("addressed the block", row["note"])
        rec = self.gate_log()[-1]                                    # FR-030: every quarantine is logged
        self.assertEqual((rec["path"], rec["outcome"], rec["reason"], rec["msg_id"]),
                         ("block", "quarantined", "malformed", mid))


class PrincipalFromEnv(GateBase):
    def env(self, **kw):
        e = {"ROOKERY_SESSION": "alpha", "ROOKERY_NODE": self.me["agent_id"]}
        e.update(kw)
        return e

    def test_open_session_and_identity(self):
        s, i = G.principal_from_env(self.conn, self.env(), self.root)
        self.assertEqual((s["name"], i["agent_id"]), ("alpha", self.me["agent_id"]))

    def test_node_by_name(self):
        s, i = G.principal_from_env(self.conn, self.env(ROOKERY_NODE="swe"), self.root)
        self.assertEqual(i["agent_id"], self.swe["agent_id"])

    def test_no_identity_gives_none_and_legacy_row(self):
        env = self.env(ROOKERY_NODE="ghost")
        self.assertIsNone(G.principal_from_env(self.conn, env, self.root))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM identities WHERE name='ghost'")
                         .fetchone()[0], 0)               # nothing is created (FR-011)
        r = self.submit(principal=None, raw_sender="ghost", to="bob")
        self.assertEqual((r["outcome"], r["warning"]), ("sent", "no-session"))
        self.assertEqual(self.rows()[0]["session_id"], "legacy")

    def test_no_node_gives_none(self):
        self.assertIsNone(G.principal_from_env(self.conn, {"ROOKERY_SESSION": "alpha"}, self.root))

    def test_no_session_gives_none(self):
        self.assertIsNone(G.principal_from_env(self.conn, {}, self.root))

    def test_session_not_open_gives_none(self):
        RI.end_session(self.conn, "alpha")
        self.assertIsNone(G.principal_from_env(self.conn, self.env(), self.root))
        r = self.submit(principal=None, raw_sender="me", to="bob")
        self.assertEqual((r["outcome"], r["warning"]), ("sent", "no-session"))
        self.assertEqual(self.rows()[0]["session_id"], "legacy")

    def test_name_in_two_sessions_uses_the_session_of_env(self):
        s, i = G.principal_from_env(self.conn, self.env(ROOKERY_SESSION="beta", ROOKERY_NODE="me"),
                                    self.root)
        self.assertEqual(i["agent_id"], self.beta_me["agent_id"])


class Ack(GateBase):
    """T038: ack acts on exactly the rows `unread` returns for the identity."""

    def put(self, sender, recipient, sid, status="pending"):
        i = R.send(self.conn, sender, recipient, "b", hold=False, session_id=sid)
        if status != "pending":
            self.conn.execute("UPDATE inbox SET status=? WHERE id=?", (status, i))
            self.conn.commit()
        return i

    def status(self, i):
        self.conn.rollback()
        return self.conn.execute("SELECT status FROM inbox WHERE id=?", (i,)).fetchone()[0]

    def test_ack_marks_only_the_identity_session(self):
        a = self.alpha["session_id"]
        mine = self.put(self.swe["agent_id"], self.me["agent_id"], a)
        other = self.put(self.swe["agent_id"], self.me["agent_id"], self.beta["session_id"])
        res = G.ack(self.conn, self.me, [mine, other])
        self.assertEqual(res, {"ok": [mine], "already": [], "skipped": [other]})
        self.assertEqual((self.status(mine), self.status(other)), ("done", "pending"))

    def test_ack_legacy_row_to_the_readers_name(self):
        l = self.put("old", "me", "legacy")
        self.assertEqual(G.ack(self.conn, self.me, [l])["ok"], [l])
        self.assertEqual(self.status(l), "done")

    def test_ack_inflight_depends_on_flag(self):
        i = self.put(self.swe["agent_id"], self.me["agent_id"], self.alpha["session_id"], "inflight")
        self.assertEqual(G.ack(self.conn, self.me, [i]),
                         {"ok": [], "already": [i], "skipped": []})
        self.assertEqual(self.status(i), "inflight")
        self.assertEqual(G.ack(self.conn, self.me, [i], from_inflight=True)["ok"], [i])
        self.assertEqual(self.status(i), "done")

    def test_ack_done_already_unknown_skipped(self):
        d = self.put(self.swe["agent_id"], self.me["agent_id"], self.alpha["session_id"], "done")
        res = G.ack(self.conn, self.me, [d, 99999])
        self.assertEqual(res, {"ok": [], "already": [d], "skipped": [99999]})

    def test_ack_legacy_only_raw_name_pending_legacy(self):
        l = self.put("old", "ghost", "legacy")
        other_name = self.put("old", "someone", "legacy")
        sess = self.put(self.swe["agent_id"], self.me["agent_id"], self.alpha["session_id"])
        res = G.ack_legacy(self.conn, "ghost", [l, other_name, sess])
        self.assertEqual(res, {"ok": [l], "already": [], "skipped": [other_name, sess]})
        self.assertEqual(G.ack_legacy(self.conn, "ghost", [l])["already"], [l])

    def test_cli_ack_legacy_row_clears_inbox_and_notice(self):
        import subprocess
        import sys
        from test_hooks import CLI
        l = self.put("old", "me", "legacy")
        self.assertEqual(TB.unread_summary(self.conn, self.me["agent_id"])["count"], 1)

        def run(*a):
            return subprocess.run([sys.executable, "-W", "ignore", CLI, *a], env=self.env(),
                                  cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(run("inbox", "--json").stdout)["ids"], [l])
        r = run("ack", str(l))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 marked read.", r.stdout)
        self.assertEqual(json.loads(run("inbox", "--json").stdout)["ids"], [])
        self.assertEqual(TB.unread_summary(self.conn, self.me["agent_id"])["count"], 0)


class LoopReset(GateBase):
    """T041a, FR-037: `rookery loop reset [NAME] [--json]`."""

    def cli(self, *a):
        import subprocess
        import sys
        from test_hooks import CLI
        return subprocess.run([sys.executable, "-W", "ignore", CLI, *a], env=self.env(),
                              cwd=self.root, capture_output=True, text=True, timeout=60)

    def hold(self):
        limit, _ = TB.loop_limits()
        for _ in range(limit):
            self.assertEqual(self.submit(path="mcp-stdio", task="t1")["outcome"], "sent")
        r = self.submit(path="mcp-stdio", task="t1")
        self.assertEqual(r["outcome"], "loop-held")
        return r["msg_id"]

    def test_reset_release_then_send(self):
        held = self.hold()
        inbox_before = self.cli("inbox", "--json").stdout
        r = self.cli("loop", "reset")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"^Reset \d+ loop counters for me\.$")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM loop_counts WHERE count>0").fetchone()[0], 0)
        R.release(self.conn, held, "test")
        self.assertEqual(R.get_message(self.conn, held)["status"], "pending")
        self.assertEqual(self.submit(path="mcp-stdio", task="t1")["outcome"], "sent")
        self.assertEqual(self.cli("inbox", "--json").stdout, inbox_before)   # no bounce

    def test_by_name_and_json(self):
        self.hold()
        r = self.cli("loop", "reset", "me", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual((o["name"], o["agent_id"], o["session"]),
                         ("me", self.me["agent_id"], self.alpha["session_id"]))
        self.assertGreater(o["reset"], 0)
        self.assertEqual(json.loads(self.cli("loop", "reset", "--json").stdout)["reset"], 0)

    def test_other_session_untouched(self):
        self.hold()
        for _ in range(2):
            self.submit(principal=(self.beta, self.beta_me), to="beta-only", path="mcp-stdio", task="t1")
        self.cli("loop", "reset")
        rows = self.conn.execute(
            "SELECT count FROM loop_counts WHERE session_id=? AND count>0",
            (self.beta["session_id"],)).fetchall()
        self.assertTrue(rows)

    def test_legacy_reset_lets_a_sessionless_sender_send_again(self):
        limit, _ = TB.loop_limits()
        legacy = lambda: self.submit(principal=None, path="mailroom", to="node-a", task="t1")
        for _ in range(limit):
            self.assertEqual(legacy()["outcome"], "sent")
        self.assertEqual(legacy()["outcome"], "loop-held")
        self.submit(principal=(self.beta, self.beta_me), to="beta-only", path="mcp-stdio", task="t1")
        r = self.cli("loop", "reset", "--legacy", "node-a", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual((o["name"], o["session"]), ("node-a", "legacy"))
        self.assertGreater(o["reset"], 0)
        self.assertEqual(legacy()["outcome"], "sent")
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM loop_counts WHERE session_id=? AND count>0",
            (self.beta["session_id"],)).fetchall())
        recs = [x for x in self.gate_log() if x.get("outcome") == "loop-reset"]
        self.assertEqual(recs[-1]["session"], "legacy")

    def test_unknown_name_exits_2(self):
        r = self.cli("loop", "reset", "nobody")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.cli("loop", "reset", "nobody").stdout, "")

    def test_one_gate_log_record(self):
        self.hold()
        self.cli("loop", "reset")
        recs = [x for x in self.gate_log() if x.get("outcome") == "loop-reset"]
        self.assertEqual(len(recs), 1)
        self.assertEqual((recs[0]["name"], recs[0]["session"]),
                         ("me", self.alpha["session_id"]))

    def test_text_passes_ste_lint(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ste-lint"):
            self.skipTest("ste-lint is not on PATH")
        self.hold()
        out = self.cli("loop", "reset").stdout
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(out)
        try:
            r = subprocess.run(["ste-lint", fh.name, "--max-score", "2.0", "--glossary",
                                "ste-glossary.txt"], capture_output=True, text=True,
                               cwd=os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            os.unlink(fh.name)


class TextLint(GateBase):
    """T046, FR-031: every user-visible text of the feature passes ste-lint. Each text is taken
    from the code that builds it, so a later wording change is linted too."""

    cli = LoopReset.cli
    hold = LoopReset.hold

    def _collect(self):
        import argparse
        import contextlib
        import io
        import json as _json
        import sqlite3
        import sys
        import urllib.error
        from unittest import mock

        import mailctl
        import mailroom_server as MS
        import postmaster as PM
        import send_mail

        t = {}
        sid = self.alpha["session_id"]
        # bounce notes (the gate builds them)
        _, (_, t["wrong-recipient"], _) = G._find_recipient(
            self.conn, sid, self.me["agent_id"], "researchr")
        _, (_, t["wrong-recipient-no-suggest"], _) = G._find_recipient(
            self.conn, sid, self.me["agent_id"], "zzzzzzzz")
        _, (_, t["off-team"], _) = G._find_recipient(
            self.conn, sid, self.me["agent_id"], "beta-only")
        t["injection-suspect"] = G._INJECTION_NOTE
        # refusal texts
        for key, (_code, detail) in MS._SEND_ERRORS.items():
            t["send-error-" + key] = detail
        t["bad-ids"] = MS._BAD_IDS
        try:
            RI.new_identity(self.conn, sid, "postmaster")
        except ValueError as exc:
            t["system-name-identity"] = str(exc)
        else:
            self.fail("new_identity accepted a system name")
        # mailctl error message, built from every /send error body
        for key in MS._SEND_ERRORS:
            _status, body = MS._send_error({"outcome": "refused" if key in (
                "system-name", "bad-input") else key, "reason": key})
            err = urllib.error.HTTPError("http://x/send", 400, "Bad", {},
                                         io.BytesIO(_json.dumps(body).encode()))
            t["mailctl-" + key] = mailctl._http_error_text(err)
        # mention notice and pane notice, from sample rows
        cause = {"sender": self.researcher["agent_id"], "recipient": self.swe["agent_id"],
                 "topic": "TASK-1", "session_id": sid}
        mid = PM._mention_mail(self.conn, self.me["agent_id"], cause)
        mid = mid["msg_id"] if isinstance(mid, dict) else mid
        t["mention"] = _json.loads(R.get_message(self.conn, mid)["body"]).get("say") \
            if R.get_message(self.conn, mid)["body"].startswith("{") \
            else R.get_message(self.conn, mid)["body"]
        rows = [{"sender": self.researcher["agent_id"], "topic": "TASK-1"},
                {"sender": self.swe["agent_id"], "topic": ""}]
        t["pane"] = PM._notice(self.conn, self.me, rows)
        # rookery loop reset
        self.hold()
        t["loop-reset"] = self.cli("loop", "reset").stdout
        t["loop-reset-unknown"] = self.cli("loop", "reset", "nobody").stderr
        # mailroom start messages and warnings
        def start(**kw):
            a = argparse.Namespace(host="127.0.0.1", token=None, allow_public=False,
                                   allow_no_auth=False)
            a.__dict__.update(kw)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ROOKERY_TOKEN", None)
                try:
                    MS.check_start(a)
                except SystemExit:
                    pass
            return out.getvalue() + err.getvalue()
        t["start-refuse"] = start(host="10.0.0.5")
        t["start-warn"] = start(host="10.0.0.5", allow_public=True, allow_no_auth=True)
        # send_mail.py outcome and busy messages
        def sm(result=None, exc=None, extra=()):
            out, err = io.StringIO(), io.StringIO()
            res = result and dict({"reason": None, "warning": None, "msg_id": 7,
                                   "bounce_id": None}, **result)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(sys, "argv", ["send_mail.py", "--to", "swe", "--body", "x",
                                                    *extra]), \
                    mock.patch.object(G, "submit", side_effect=exc, return_value=res):
                send_mail.main()
            return out.getvalue() + err.getvalue()
        t["send-mail-sent"] = sm({"outcome": "sent"})
        t["send-mail-busy"] = sm({"outcome": "db-busy"})
        t["send-mail-locked"] = sm(exc=sqlite3.OperationalError("database is locked"))
        t["send-mail-failed"] = sm({"outcome": "send-failed", "reason": "disk-error"})
        t["send-mail-refused"] = sm({"outcome": "refused", "reason": "system-name"})
        t["send-mail-held"] = sm({"outcome": "loop-held"})
        return t

    def test_all_user_text_passes_ste_lint(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ste-lint"):
            self.skipTest("ste-lint is not on PATH")
        texts = self._collect()
        self.assertGreaterEqual(len(texts), 20)
        for k, v in texts.items():
            self.assertTrue(v.strip(), f"empty text: {k}")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n\n".join(v.strip() for v in texts.values()) + "\n")
        try:
            r = subprocess.run(["ste-lint", fh.name, "--max-score", "2.0", "--glossary",
                                "ste-glossary.txt"], capture_output=True, text=True,
                               cwd=os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(r.returncode, 0, "texts: " + ", ".join(texts) + "\n"
                             + r.stdout + r.stderr)
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
