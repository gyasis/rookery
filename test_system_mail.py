#!/usr/bin/env python3
"""Mail gate, T019: postmaster and relay mail goes through mail_gate.system_send.

    python3 -m unittest test_system_mail -v
"""
import unittest

import postmaster
import relay
import rookery as R
import test_hooks


class SystemMail(test_hooks.Base):
    def rows(self, sender):
        return self.conn.execute("SELECT * FROM inbox WHERE sender=?", (sender,)).fetchall()

    def test_grant_mail_stores_one_pending_row_in_legacy(self):
        rid = R.request_credential(self.conn, "bob", "db")
        R.approve_credential(self.conn, rid, "env:DB")
        postmaster._grant_mail(self.conn, R.get_credential(self.conn, rid))
        rows = self.rows("postmaster")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["session_id"], rows[0]["recipient"]),
                         ("pending", "legacy", "bob"))
        self.assertEqual(rows[0]["body"], "CRED_GRANTED:db:env:DB")

    def test_dead_letter_goes_to_sender_in_session_of_dead_row(self):
        sid = self.alpha["session_id"]
        mid = R.send(self.conn, "alice", "x@nowhere", "hi", session_id=sid)
        relay._dead_letter(self.conn, R.get_message(self.conn, mid), "unknown mailroom")
        rows = self.rows("relay")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["recipient"], rows[0]["session_id"]), ("alice", sid))

    def test_mention_mail_uses_session_of_cause_row(self):
        sid = self.alpha["session_id"]
        mid = R.send(self.conn, "alice", "bob", "ping @carol", session_id=sid)
        postmaster._mention_mail(self.conn, "carol", R.get_message(self.conn, mid))
        rows = self.rows("postmaster")
        self.assertEqual((len(rows), rows[0]["session_id"], rows[0]["recipient"]),
                         (1, sid, "carol"))

    def test_mention_mail_legacy_when_cause_has_no_session(self):
        mid = R.send(self.conn, "alice", "bob", "ping @carol", session_id="legacy")
        self.conn.execute("UPDATE inbox SET session_id=NULL WHERE id=?", (mid,))
        self.conn.commit()
        postmaster._mention_mail(self.conn, "carol", R.get_message(self.conn, mid))
        self.assertEqual(self.rows("postmaster")[0]["session_id"], "legacy")


if __name__ == "__main__":
    unittest.main()
