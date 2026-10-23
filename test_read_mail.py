#!/usr/bin/env python3
"""Mail gate, T040: `read_mail.py` (legacy script) reads through the gate (FR-032, SC-009).

    python3 -m unittest test_read_mail -v

Each case runs the script as a subprocess against a temp DB. Mail is put in with `rookery.send`.
"""
import json
import os
import subprocess
import sys

import rookery as R
import rookery_identity as RI
from test_hooks import Base, HERE

LEGACY = "legacy"


class ReadMailTest(Base):
    def setUp(self):
        super().setUp()
        self.sid = self.alpha["session_id"]
        self.beta = RI.new_session(self.conn, name="beta")
        self.beta_me = RI.new_identity(self.conn, self.beta["session_id"], "me")

    def put(self, recipient, sid, body, sender="outside"):
        return R.send(self.conn, sender, recipient, body, topic="T", hold=False, session_id=sid)

    def run_script(self, *args, env=None):
        return subprocess.run([sys.executable, os.path.join(HERE, "read_mail.py"), *args],
                              capture_output=True, text=True, env=env or self.env(), cwd=HERE,
                              timeout=30)

    def status(self, mid):
        return self.conn.execute("SELECT status FROM inbox WHERE id=?", (mid,)).fetchone()[0]

    def test_read_without_ack_changes_nothing(self):
        self.put(self.me["agent_id"], self.sid, "one")
        before = self.snapshot()
        out = self.run_script("--node", "me")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("one", out.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_ack_marks_shown_mail_done(self):
        a = self.put(self.me["agent_id"], self.sid, "one")
        out = self.run_script("--node", "me", "--ack")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("acked 1", out.stdout)
        self.assertEqual(self.status(a), "done")

    def test_other_session_mail_not_shown_or_acked(self):
        other = self.put(self.me["agent_id"], self.sid, "mine")
        theirs = self.put(self.beta_me["agent_id"], self.beta["session_id"], "THEIRS")
        out = self.run_script("--node", "me", "--ack")
        self.assertIn("mine", out.stdout)
        self.assertNotIn("THEIRS", out.stdout)
        self.assertEqual(self.status(other), "done")
        self.assertEqual(self.status(theirs), "pending")

    def test_no_identity_reads_and_acks_legacy_mail(self):
        a = self.put("ghost", LEGACY, "legacy-one")
        self.put(self.me["agent_id"], self.sid, "SESSION-MAIL")
        env = self.env(session=False, node=False)
        out = self.run_script("--node", "ghost", "--ack", env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("legacy-one", out.stdout)
        self.assertNotIn("SESSION-MAIL", out.stdout)
        self.assertEqual(self.status(a), "done")

    def test_node_naming_other_identity_exits_2(self):
        out = self.run_script("--node", "researcher")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_all_shows_done_mail_of_same_identity_only(self):
        mine = self.put(self.me["agent_id"], self.sid, "MINE-DONE")
        theirs = self.put(self.beta_me["agent_id"], self.beta["session_id"], "THEIRS-DONE")
        peer = self.put(self.researcher["agent_id"], self.sid, "PEER-DONE")
        for i in (mine, theirs, peer):
            self.conn.execute("UPDATE inbox SET status='done' WHERE id=?", (i,))
        self.conn.commit()
        plain = self.run_script("--node", "me")
        self.assertNotIn("MINE-DONE", plain.stdout)
        out = self.run_script("--node", "me", "--all", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([r["id"] for r in json.loads(out.stdout)], [mine])

    def test_since_filters_ids(self):
        a = self.put(self.me["agent_id"], self.sid, "one")
        b = self.put(self.me["agent_id"], self.sid, "two")
        out = self.run_script("--node", "me", "--since", str(a), "--json")
        self.assertEqual([r["id"] for r in json.loads(out.stdout)], [b])

    def test_help_says_legacy(self):
        out = self.run_script("--help")
        self.assertEqual(out.returncode, 0)
        self.assertIn("legacy", out.stdout.lower())
        self.assertIn("rookery inbox", out.stdout)
        self.assertIn("rookery ack", out.stdout)


if __name__ == "__main__":
    import unittest
    unittest.main()
