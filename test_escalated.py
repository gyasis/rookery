#!/usr/bin/env python3
"""The status `escalated` (004, D14, FR-028, SC-009): only held mail escalates; a person or a later
answer releases or quarantines it; nothing wakes, mentions or mirrors a body for it.

    python3 -m unittest test_escalated -v
"""
import json
import os
import tempfile
import unittest

import postmaster
import rookery as R
import rookery_identity as RI
import rookery_views as V
import test_hooks

BODY = "zebrafish secret plan @carol"


class Escalated(test_hooks.Base):
    def setUp(self):
        super().setUp()
        self.sid = self.alpha["session_id"]
        self.ids = {n: RI.new_identity(self.conn, self.sid, n) for n in ("alice", "bob", "carol")}

    def mail(self, hold=True, body=BODY, topic="budget"):
        return R.send(self.conn, self.ids["alice"]["agent_id"], self.ids["bob"]["agent_id"], body,
                      topic=topic, hold=hold, session_id=self.sid)

    def escalated(self, **kw):
        mid = self.mail(**kw)
        R.escalate(self.conn, mid, "screener:fake", "too-large")
        return mid

    def status(self, mid):
        return R.get_message(self.conn, mid)["status"]

    def test_fetch_thread_hides_unreleased_mail_from_recipient(self):
        for st in ("held", "quarantined", "escalated"):
            mid = self.mail(hold=False, body="SECRET-" + st)
            self.conn.execute("UPDATE inbox SET status=? WHERE id=?", (st, mid))
        self.conn.commit()
        bob, alice = self.ids["bob"]["agent_id"], self.ids["alice"]["agent_id"]
        self.assertEqual([m for m in R.fetch_thread(self.conn, bob)], [])
        self.assertEqual(len(R.fetch_thread(self.conn, alice)), 3)   # the sender still sees its own rows
        ok = self.mail(hold=False, body="fine")
        self.assertEqual([m["id"] for m in R.fetch_thread(self.conn, bob)], [ok])

    def test_escalate_only_from_held(self):
        mid = self.escalated()
        r = R.get_message(self.conn, mid)
        self.assertEqual((r["status"], r["verdict"], r["reason"], r["decided_by"]),
                         ("escalated", "escalate", "too-large", "screener:fake"))
        for start in (self.mail(hold=False), mid):
            with self.assertRaises(ValueError):
                R.escalate(self.conn, start, "human:x", "r")

    def test_release_from_escalated(self):
        mid = self.escalated()
        R.release(self.conn, mid, "human:x")
        self.assertEqual(self.status(mid), "pending")

    def test_quarantine_from_escalated(self):
        mid = self.escalated()
        R.quarantine(self.conn, mid, "unneeded", "human:x", bounce=False)
        self.assertEqual(self.status(mid), "quarantined")

    def test_unquarantine_not_from_escalated(self):
        mid = self.escalated()
        with self.assertRaises(ValueError):
            R.unquarantine(self.conn, mid, "human:x")
        self.assertEqual(self.status(mid), "escalated")

    def test_no_mention_notice(self):
        self.escalated()
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.conn.execute("SELECT * FROM inbox WHERE sender='postmaster'").fetchall(), [])

    def test_no_fetch_and_no_wake_row(self):
        mid = self.escalated()
        self.assertEqual(R.fetch_undelivered(self.conn, self.ids["bob"]["agent_id"]), [])
        self.assertFalse(postmaster.has_mail(self.conn, self.ids["bob"]["agent_id"]))
        self.assertEqual(self.status(mid), "escalated")

    def test_escalated_ask_is_not_an_open_ask(self):
        a, b = self.ids["alice"]["agent_id"], self.ids["bob"]["agent_id"]
        ask = R.send(self.conn, a, b, json.dumps({"kind": "ask"}), topic="t1", hold=True,
                     session_id=self.sid)
        R.escalate(self.conn, ask, "screener:fake", "r")
        ans = R.send(self.conn, b, a, json.dumps({"kind": "answer"}), topic="t1", hold=False,
                     session_id=self.sid)
        row = R.get_message(self.conn, ans)
        self.assertFalse(postmaster._answers_open_ask(self.conn, row, a))
        R.release(self.conn, ask, "human:x")        # control: a delivered ask does count
        self.assertTrue(postmaster._answers_open_ask(self.conn, row, a))

    def test_mirror_lists_it_with_no_body(self):
        mid = self.escalated()
        with tempfile.TemporaryDirectory() as root:
            paths = V.mail_mirror(self.conn, root)
            self.assertEqual([os.path.basename(p) for p in paths], [f"{mid}.md"])
            self.assertIn(os.path.join("_mail", "review"), paths[0])
            text = open(paths[0]).read()
            self.assertIn("State: escalated", text)
            self.assertNotIn("zebrafish", text)
            R.release(self.conn, mid, "human:x")
            V.mail_mirror(self.conn, root)
            self.assertFalse(os.path.exists(paths[0]))

    def test_send_stores_thread(self):
        a, b = self.ids["alice"]["agent_id"], self.ids["bob"]["agent_id"]
        t = R.send(self.conn, a, b, "x", session_id=self.sid, thread="t1")
        n = R.send(self.conn, a, b, "x", session_id=self.sid)
        self.assertEqual(R.get_message(self.conn, t)["thread"], "t1")
        self.assertIsNone(R.get_message(self.conn, n)["thread"])


if __name__ == "__main__":
    unittest.main()
