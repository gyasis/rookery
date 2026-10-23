"""Mail gate, T012: the shared 3-try matrix for every send path (SC-001). Not a test module.

A driver is a test class that mixes in `SendMatrix` BEFORE `test_hooks.Base`, and defines
`send(self, to)`: it sends one message to `to` through its path (block, `rookery send`, ...).
Set `with_session = False` for a path that has no session.

    class BlockPath(SendMatrix, Base):
        def send(self, to): ...

The mixin adds session `beta` (with the name `beta-only`, which exists only there) and 3 tests:
unknown name, peer only in beta, valid peer. Each test checks the stored rows, the gate log and
that the `identities` table did not change (FR-011).

  with a session   : wrong-recipient, off-team, sent (pending, sender's session)
  with no session  : sent in session `legacy` for all 3, with the `no-session` warning
"""
import json
import os
import sqlite3

import rookery_identity as RI


class SendMatrix:
    with_session = True
    PEER = "researcher"       # a valid peer in alpha (made by test_hooks.Base)
    ONLY_IN_BETA = "beta-only"
    UNKNOWN = "nobody"

    def send(self, to):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.beta = RI.new_session(self.conn, name="beta")
        self.beta_only = RI.new_identity(self.conn, self.beta["session_id"], self.ONLY_IN_BETA)

    # data helpers (a fresh connection: the path may have run in another process)
    def _query(self, sql):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(sql)]
        finally:
            c.close()

    def stored(self):
        return self._query("SELECT * FROM inbox ORDER BY id")

    def gate_records(self):
        p = os.path.join(self.root, "logs", "gate.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def _try(self, to, outcome, reason):
        n_identities = len(self._query("SELECT agent_id FROM identities"))
        self.send(to)
        self.assertEqual(len(self._query("SELECT agent_id FROM identities")), n_identities,
                         "FR-011: a send must not create an identity")
        recs = self.gate_records()
        self.assertTrue(recs, "no gate log record")
        rec = recs[-1]
        self.assertEqual((rec["outcome"], rec["reason"]), (outcome, reason), rec)
        if self.with_session:
            self.assertIsNone(rec["warning"], rec)
        else:
            self.assertEqual((rec["warning"], rec["session"]), ("no-session", "legacy"), rec)
        return self.stored()

    def _assert_session_rows(self, rows, to, outcome):
        me, sid = self.me["agent_id"], self.alpha["session_id"]
        if outcome == "sent":
            [row] = rows
            self.assertEqual((row["sender"], row["recipient"], row["status"], row["session_id"]),
                             (me, self.researcher["agent_id"], "pending", sid))
            return
        orig = rows[0]
        self.assertEqual((orig["sender"], orig["status"], orig["session_id"]), (me, "quarantined", sid))
        self.assertFalse(any(r["recipient"] == self.beta_only["agent_id"] for r in rows))
        self.assertTrue(any(r["recipient"] == me and r["status"] == "pending" for r in rows[1:]),
                        "no bounce to the sender")

    def _run(self, to, outcome, reason):
        rows = self._try(to, outcome, reason if self.with_session else None)
        if self.with_session:
            self._assert_session_rows(rows, to, outcome)
        else:
            [row] = rows
            self.assertEqual((row["recipient"], row["status"], row["session_id"]),
                             (to, "pending", "legacy"))

    def test_unknown_name(self):
        self._run(self.UNKNOWN, "quarantined" if self.with_session else "sent", "wrong-recipient")

    def test_peer_only_in_beta(self):
        self._run(self.ONLY_IN_BETA, "quarantined" if self.with_session else "sent", "off-team")

    def test_valid_peer(self):
        self._run(self.PEER, "sent", None)
