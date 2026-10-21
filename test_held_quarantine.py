#!/usr/bin/env python3
"""held / quarantined mail: hold on send, release, quarantine + bounce,
unquarantine, system-sender exemption, migration of an old DB, labels export.

    python3 test_held_quarantine.py
"""
import json
import os
import sqlite3
import tempfile
import unittest

import rookery as R


class HeldQuarantine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = R.connect(os.path.join(self.tmp.name, "t.db"))
        os.environ["ROOKERY_HOLD"] = "1"

    def tearDown(self):
        os.environ.pop("ROOKERY_HOLD", None)
        self.conn.close()
        self.tmp.cleanup()

    def _status(self, mid):
        return R.get_message(self.conn, mid)["status"]

    def test_hold_off_by_default(self):
        os.environ.pop("ROOKERY_HOLD")
        mid = R.send(self.conn, "researcher", "law-counsel", "hi", "TASK-1")
        self.assertEqual(self._status(mid), "pending")

    def test_agent_mail_held_and_invisible_to_recipient(self):
        mid = R.send(self.conn, "researcher", "law-counsel", "hi", "TASK-1")
        self.assertEqual(self._status(mid), "held")
        self.assertEqual(R.fetch_undelivered(self.conn, "law-counsel"), [])
        self.assertEqual([r["id"] for r in R.list_held(self.conn)], [mid])

    def test_system_senders_never_held(self):
        for s in R.SYSTEM_SENDERS:
            self.assertEqual(self._status(R.send(self.conn, s, "x", "notice")), "pending")

    def test_release_delivers(self):
        mid = R.send(self.conn, "researcher", "law-counsel", "hi", "TASK-1")
        R.release(self.conn, mid, "tev1")
        r = R.get_message(self.conn, mid)
        self.assertEqual((r["status"], r["verdict"], r["decided_by"]), ("pending", "release", "tev1"))
        self.assertEqual(len(R.fetch_undelivered(self.conn, "law-counsel")), 1)

    def test_quarantine_bounces_to_sender(self):
        mid = R.send(self.conn, "researcher", "swe", "is this legal?", "TASK-1")
        bid = R.quarantine(self.conn, mid, "wrong-recipient", "tev1", note="legal q", suggest="law-counsel")
        r = R.get_message(self.conn, mid)
        self.assertEqual((r["status"], r["reason"]), ("quarantined", "wrong-recipient"))
        self.assertEqual(R.fetch_undelivered(self.conn, "swe"), [])
        b = R.get_message(self.conn, bid)
        self.assertEqual((b["sender"], b["recipient"], b["status"], b["topic"]),
                         ("rookery", "researcher", "pending", "TASK-1"))
        body = json.loads(b["body"])
        self.assertEqual((body["kind"], body["msg_id"], body["suggest"]), ("bounce", mid, "law-counsel"))

    def test_quarantine_no_bounce(self):
        mid = R.send(self.conn, "researcher", "swe", "x")
        self.assertIsNone(R.quarantine(self.conn, mid, "unneeded", "human", bounce=False))
        self.assertEqual(R.fetch_undelivered(self.conn, "researcher"), [])

    def test_bad_reason_rejected(self):
        mid = R.send(self.conn, "researcher", "swe", "x")
        with self.assertRaises(ValueError):
            R.quarantine(self.conn, mid, "spam", "human")
        self.assertEqual(self._status(mid), "held")

    def test_decide_only_from_held(self):
        mid = R.send(self.conn, "researcher", "swe", "x")
        R.release(self.conn, mid, "human")
        with self.assertRaises(ValueError):
            R.quarantine(self.conn, mid, "unneeded", "human")
        with self.assertRaises(ValueError):
            R.release(self.conn, mid, "human")

    def test_unquarantine_keeps_reason_as_correction_label(self):
        mid = R.send(self.conn, "researcher", "swe", "x")
        R.quarantine(self.conn, mid, "unneeded", "tev1", bounce=False)
        R.unquarantine(self.conn, mid, "human")
        r = R.get_message(self.conn, mid)
        self.assertEqual((r["status"], r["verdict"], r["reason"], r["decided_by"]),
                         ("pending", "unquarantine", "unneeded", "human"))
        with self.assertRaises(ValueError):
            R.unquarantine(self.conn, mid, "human")

    def test_labels_export(self):
        a = R.send(self.conn, "researcher", "law-counsel", "a")
        b = R.send(self.conn, "researcher", "swe", "b")
        R.send(self.conn, "researcher", "swe", "undecided")
        R.release(self.conn, a, "tev1")
        R.quarantine(self.conn, b, "wrong-recipient", "tev1", bounce=False)
        got = {r["id"]: r["verdict"] for r in R.screening_labels(self.conn)}
        self.assertEqual(got, {a: "release", b: "quarantine"})
        self.assertEqual([r["id"] for r in R.list_quarantined(self.conn, "wrong-recipient")], [b])

    def test_migrates_old_db(self):
        path = os.path.join(self.tmp.name, "old.db")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL, "
                    "recipient TEXT NOT NULL, body TEXT NOT NULL, topic TEXT, status TEXT NOT NULL "
                    "DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, note TEXT, claimed_at INTEGER, "
                    "delivered INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, delivered_at INTEGER)")
        old.execute("INSERT INTO inbox(sender,recipient,body,created_at) VALUES('a','b','old',1)")
        old.commit()
        old.close()
        c = R.connect(path)
        cols = {r[1] for r in c.execute("PRAGMA table_info(inbox)")}
        self.assertTrue({"verdict", "reason", "decided_by", "decided_at"} <= cols)
        self.assertEqual(c.execute("SELECT body FROM inbox").fetchone()[0], "old")
        c.close()


if __name__ == "__main__":
    unittest.main()
