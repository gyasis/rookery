#!/usr/bin/env python3
"""Mail gate, T032: a mention notice only for pending mail, in the same session, with no body.

    python3 -m unittest test_mentions -v
"""
import unittest

import mail_gate
import postmaster
import rookery as R
import rookery_identity as RI
import test_hooks

BODY = "zebrafish secret plan @carol"
NOTICE = ("MENTION: alice named you in mail to bob, topic budget. "
          "You cannot read that mail.")


class Mentions(test_hooks.Base):
    def setUp(self):
        super().setUp()
        self.sid = self.alpha["session_id"]
        self.ids = {n: RI.new_identity(self.conn, self.sid, n) for n in ("alice", "bob", "carol")}

    def notices(self):
        return self.conn.execute("SELECT * FROM inbox WHERE sender='postmaster'").fetchall()

    def mail(self, hold=False, session=None, body=BODY):
        # session mail stores agent IDs, as the gate does; a legacy row keeps raw names
        sender, to = ((self.ids["alice"]["agent_id"], self.ids["bob"]["agent_id"])
                      if session in (None, self.sid) else ("alice", "bob"))
        return R.send(self.conn, sender, to, body, topic="budget",
                      hold=hold, session_id=session or self.sid)

    def test_held_mail_gives_no_notice(self):
        mid = self.mail(hold=True)
        self.assertEqual(R.get_message(self.conn, mid)["status"], "held")
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.notices(), [])

    def test_quarantined_mail_gives_no_notice(self):
        mid = self.mail(hold=True)
        R.quarantine(self.conn, mid, "unneeded", "human", bounce=False)
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.notices(), [])

    def test_inflight_mail_gives_one_notice(self):
        mid = self.mail()
        self.conn.execute("UPDATE inbox SET status='inflight' WHERE id=?", (mid,))
        self.conn.commit()
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(len(self.notices()), 1)

    def test_pending_mail_gives_one_notice_without_body(self):
        mid = self.mail()
        last = postmaster._mention_pass(self.conn, 0, [])
        rows = self.notices()
        self.assertEqual(len(rows), 1)
        carol = self.ids["carol"]
        self.assertEqual((rows[0]["recipient"], rows[0]["session_id"]), (carol["agent_id"], self.sid))
        self.assertEqual(rows[0]["body"], NOTICE)                    # names, not agent IDs
        self.assertNotIn("zebrafish", rows[0]["body"])
        self.assertGreaterEqual(last, mid)
        seen = [m["id"] for m in mail_gate.unread(self.conn, carol)]  # carol can read the notice
        self.assertIn(rows[0]["id"], seen)

    def test_mention_of_the_recipient_gives_no_notice(self):
        self.mail(body="note for @bob")
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.notices(), [])

    def test_other_session_gives_no_notice(self):
        other = RI.new_session(self.conn, name="beta")
        RI.new_identity(self.conn, other["session_id"], "dave")
        self.mail(session=other["session_id"])
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.notices(), [])

    def test_row_scanned_once(self):
        self.mail()
        last = postmaster._mention_pass(self.conn, 0, [])
        postmaster._mention_pass(self.conn, last, [])
        self.assertEqual(len(self.notices()), 1)

    def test_legacy_row_uses_node_names(self):
        R.send(self.conn, "alice", "bob", BODY, topic="budget", hold=False, session_id="legacy")
        postmaster._mention_pass(self.conn, 0, [])
        self.assertEqual(self.notices(), [])
        postmaster._mention_pass(self.conn, 0, ["carol"])
        self.assertEqual(len(self.notices()), 1)


if __name__ == "__main__":
    unittest.main()
