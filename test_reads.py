#!/usr/bin/env python3
"""Unread reads are scoped to the reader's session (spec 002, T031, FR-015, FR-016, SC-004, SC-010).

    python3 -m unittest test_reads -v

Rows are inserted with `rookery.send(..., session_id=...)` in this test only.
SC-009: `rookery inbox` and `rookery peek` are already proven read-only by test_hooks.py:369
(prompt notice changes no inbox row) and test_notices.py:143. This file does not repeat them.
"""
import rookery as R
import rookery_identity as RI
import turn_block as TB
import mail_gate as G
from test_hooks import Base


class ReadsTest(Base):
    def setUp(self):
        super().setUp()
        self.beta_s = RI.new_session(self.conn, name="beta")
        self.sid_b = self.beta_s["session_id"]
        self.sid_a = self.alpha["session_id"]
        self.beta = RI.new_identity(self.conn, self.sid_b, "beta")
        self.bid = self.beta["agent_id"]
        self.beta_peer = RI.new_identity(self.conn, self.sid_b, "peer")
        self.alpha_sender = RI.new_identity(self.conn, self.sid_a, "outsider")

    def put(self, sender, recipient, sid, topic="T"):
        return R.send(self.conn, sender, recipient, "body-" + topic, topic=topic, hold=False,
                      session_id=sid)

    def test_other_session_mail_to_beta_id_is_not_seen(self):
        self.put(self.alpha_sender["agent_id"], self.bid, self.sid_a, "ALPHA")
        own = self.put(self.beta_peer["agent_id"], self.bid, self.sid_b, "BETA")
        rows = G.unread(self.conn, self.beta)
        self.assertEqual([r["id"] for r in rows], [own])
        self.assertEqual(TB.unread_summary(self.conn, self.bid)["count"], 1)
        self.assertEqual([r["id"] for r in TB.unread(self.conn, self.bid)], [own])

    def test_notice_names_only_own_session_sender(self):
        self.put(self.alpha_sender["agent_id"], self.bid, self.sid_a, "ALPHA")
        self.put(self.beta_peer["agent_id"], self.bid, self.sid_b, "BETA")
        line = TB.format_unread_line(TB.unread_summary(self.conn, self.bid))
        self.assertIn("peer", line)
        self.assertNotIn("outsider", line)
        self.assertIn("1 unread", line)

    def test_sender_shown_by_identity_name(self):
        self.put(self.beta_peer["agent_id"], self.bid, self.sid_b)
        r = G.unread(self.conn, self.beta)[0]
        self.assertEqual(r["sender_name"], "peer")
        self.assertEqual(r["sender"], self.beta_peer["agent_id"])

    def test_row_shape_and_body_only_on_request(self):
        self.put(self.beta_peer["agent_id"], self.bid, self.sid_b)
        r = G.unread(self.conn, self.beta)[0]
        self.assertEqual(set(r), {"id", "sender", "sender_name", "topic", "created_at", "session_id"})
        r = G.unread(self.conn, self.beta, with_body=True)[0]
        self.assertEqual(r["body"], "body-T")

    def test_unknown_sender_shown_as_stored(self):
        self.put("rookery", self.bid, self.sid_b)
        self.assertEqual(G.unread(self.conn, self.beta)[0]["sender_name"], "rookery")

    def test_legacy_to_name_or_agent_id_shows_in_list_and_count(self):
        a = self.put("somenode", "beta", G.LEGACY, "BYNAME")
        b = self.put("somenode", self.bid, G.LEGACY, "BYID")
        self.assertEqual({r["id"] for r in G.unread(self.conn, self.beta)}, {a, b})
        self.assertEqual(TB.unread_summary(self.conn, self.bid)["count"], 2)

    def test_legacy_to_other_name_not_shown(self):
        self.put("somenode", "gamma", G.LEGACY)
        self.put("somenode", self.beta_peer["agent_id"], G.LEGACY)
        self.assertEqual(G.unread(self.conn, self.beta), [])
        self.assertEqual(TB.unread_summary(self.conn, self.bid)["count"], 0)

    def test_non_pending_not_shown(self):
        m = self.put(self.beta_peer["agent_id"], self.bid, self.sid_b)
        self.conn.execute("UPDATE inbox SET status='done' WHERE id=?", (m,))
        self.conn.commit()
        self.assertEqual(G.unread(self.conn, self.beta), [])

    def test_unread_legacy_raw_name_only(self):
        a = self.put("somenode", "rawname", G.LEGACY)
        self.put("somenode", "other", G.LEGACY)
        self.put(self.beta_peer["agent_id"], "rawname", self.sid_b)   # not legacy
        rows = G.unread_legacy(self.conn, "rawname")
        self.assertEqual([r["id"] for r in rows], [a])
        self.assertNotIn("body", rows[0])
        self.assertEqual(G.unread_legacy(self.conn, "rawname", with_body=True)[0]["body"], "body-T")


if __name__ == "__main__":
    import unittest
    unittest.main()
