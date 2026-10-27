#!/usr/bin/env python3
"""Rookery turn block, US5 (T044): the postmaster outbox scan, turn_block.scan_outbox(conn, root).

    python3 -m unittest test_outbox -v

ASSUMED API (research D3 + D11; change only `scan()` below if it differs):
    turn_block.scan_outbox(conn, root)   scans <root>/agents/*/*/outbox/*.json; return value unused here.
Layout (data-model.md): a claimed file goes to <root>/postmaster/outbox/<session>/<name>/sending/,
then to sent/ or failed/ as <TASK-ID>-<file>, with a .result.json beside it ({task, outcome, msg_id, reason}).
Tests use a temp ROOKERY_DB, HOME and ROOKERY_ROOT and never touch the real database.
"""
import glob
import json
import os
import tempfile
import threading
import time
import unittest

import rookery_identity as RI
import turn_block as TB

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "turns")
ENV_KEYS = ("ROOKERY_DB", "ROOKERY_ROOT", "HOME", "ROOKERY_URL", "ROOKERY_HOLD",
            "ROOKERY_SESSION", "ROOKERY_NODE", "ROOKERY_WAKE")
INTERRUPTED = "interrupted: check the mailroom before you send again"


def scan(conn, root, now=None):
    # files written by the tests are "fresh"; look at them from the future (F6 ignores < 2 s old)
    return TB.scan_outbox(conn, root, now=time.time() + 10 if now is None else now)


def fixture_block(name="valid-ask.txt"):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return TB.find_last_block(fh.read())     # raw JSON text, no fence


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        for k in [k for k in os.environ if k.startswith(("ROOKERY_", "HERDR_", "TMUX"))]:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = os.path.join(self.tmp.name, "t.db")
        os.environ["ROOKERY_ROOT"] = self.root
        os.environ["ROOKERY_WAKE"] = "off"   # no real detached wake child from non-wake tests
        self.conn = TB.open_db()
        self.alpha = RI.new_session(self.conn, name="alpha")
        self.me = RI.new_identity(self.conn, self.alpha["session_id"], "me")
        self.lc = RI.new_identity(self.conn, self.alpha["session_id"], "law-counsel")
        self._saved_all = None

    def tearDown(self):
        self.conn.close()
        for k in [k for k in os.environ if k.startswith(("ROOKERY_", "HERDR_", "TMUX"))]:
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # helpers
    def ws(self, name="me"):
        return os.path.join(self.root, "agents", "alpha", name)

    def drop(self, fname, raw, name="me"):
        d = os.path.join(self.ws(name), "outbox")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, fname)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(raw)
        return p

    def pm(self, state, name="me"):
        return os.path.join(self.root, "postmaster", "outbox", "alpha", name, state)

    def pm_files(self, state, name="me"):
        """(payload files, result files) in a postmaster state folder, as basenames."""
        names = sorted(os.listdir(self.pm(state, name))) if os.path.isdir(self.pm(state, name)) else []
        res = [n for n in names if n.endswith(".result.json")]
        return [n for n in names if n not in res], res

    def result(self, state, name="me"):
        _, res = self.pm_files(state, name)
        self.assertEqual(len(res), 1, os.listdir(self.pm(state, name)))
        with open(os.path.join(self.pm(state, name), res[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def outbox_left(self):
        """Files still in any agent outbox folder (drop box, put-back or kept)."""
        return glob.glob(os.path.join(self.root, "agents", "*", "*", "outbox", "**", "*.json"),
                         recursive=True)

    def payloads(self):
        """Every input file wherever it lives now (agent outbox + postmaster), results excluded."""
        found = []
        for pat in (os.path.join(self.root, "agents", "*", "*", "outbox", "**", "*.json"),
                    os.path.join(self.root, "postmaster", "outbox", "**", "*.json")):
            found += [p for p in glob.glob(pat, recursive=True) if not p.endswith(".result.json")]
        return sorted(found)

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM inbox ORDER BY id")]


class Delivery(Base):
    def test_valid_file_one_row_moved_to_sent(self):
        src = self.drop("a.json", fixture_block())
        scan(self.conn, self.root)
        (row,) = self.rows()
        self.assertEqual((row["sender"], row["recipient"]), (self.me["agent_id"], self.lc["agent_id"]))
        self.assertEqual(row["topic"], "TASK-12")
        self.assertEqual(row["status"], "pending")
        self.assertFalse(os.path.exists(src))
        self.assertEqual(self.pm_files("sent")[0], ["TASK-12-a.json"])
        self.assertTrue(os.path.isfile(os.path.join(self.pm("sent"), "TASK-12-a.json")))
        res = self.result("sent")
        for key in ("task", "outcome", "msg_id", "reason"):
            self.assertIn(key, res)
        self.assertEqual((res["task"], res["outcome"], res["msg_id"]), ("TASK-12", "sent", row["id"]))
        self.assertEqual(self.pm_files("failed"), ([], []))
        self.assertEqual(self.pm_files("sending"), ([], []))

    def test_malformed_file_one_malformed_row_moved_to_failed(self):
        src = self.drop("bad.json", '{"task": "TASK-12", "kind": "ask"')
        scan(self.conn, self.root)
        rows = self.rows()
        originals = [r for r in rows if r["sender"] != "rookery"]
        bounces = [r for r in rows if r["sender"] == "rookery"]
        self.assertEqual(len(originals), 1, rows)
        self.assertEqual(len(bounces), 1, rows)
        self.assertEqual((originals[0]["status"], originals[0]["reason"]), ("quarantined", "malformed"))
        self.assertEqual(originals[0]["sender"], self.me["agent_id"])
        self.assertFalse(os.path.exists(src))
        payload, results = self.pm_files("failed")
        self.assertEqual(len(payload), 1, payload)
        self.assertTrue(payload[0].endswith("bad.json"), payload)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.pm_files("sent"), ([], []))
        self.assertEqual(self.outbox_left(), [])

    def test_two_scans_one_row(self):
        self.drop("a.json", fixture_block())
        scan(self.conn, self.root)
        scan(self.conn, self.root)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.pm_files("sent")[0], ["TASK-12-a.json"])

    def test_sender_is_the_folders_identity(self):
        mine = json.loads(fixture_block())
        theirs = dict(mine, to="me", say="reply from the lawyer")
        self.drop("a.json", json.dumps(mine), name="me")
        self.drop("b.json", json.dumps(theirs), name="law-counsel")
        scan(self.conn, self.root)
        by_sender = {r["sender"]: r for r in self.rows()}
        self.assertEqual(set(by_sender), {self.me["agent_id"], self.lc["agent_id"]})
        self.assertEqual(by_sender[self.me["agent_id"]]["recipient"], self.lc["agent_id"])
        self.assertEqual(by_sender[self.lc["agent_id"]]["recipient"], self.me["agent_id"])
        self.assertEqual(self.pm_files("sent", "law-counsel")[0], ["TASK-12-b.json"])
        self.assertEqual(self.pm_files("sent", "me")[0], ["TASK-12-a.json"])


class Failures(Base):
    def test_interrupted_sending_file_goes_to_failed_no_row(self):
        os.makedirs(self.pm("sending"))
        with open(os.path.join(self.pm("sending"), "a.json"), "w") as fh:
            fh.write(fixture_block())
        scan(self.conn, self.root)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.pm_files("sending"), ([], []))
        payload, results = self.pm_files("failed")
        self.assertEqual(len(payload), 1, payload)
        self.assertTrue(payload[0].endswith("a.json"), payload)
        self.assertEqual(len(results), 1)
        with open(os.path.join(self.pm("failed"), results[0]), encoding="utf-8") as fh:
            self.assertIn(INTERRUPTED, fh.read())
        scan(self.conn, self.root)                     # never resent
        self.assertEqual(self.rows(), [])

    def test_send_failed_goes_to_failed_and_is_not_retried(self):
        self.conn.execute("CREATE TRIGGER boom BEFORE INSERT ON inbox "
                          "BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.conn.commit()
        self.drop("a.json", fixture_block())
        scan(self.conn, self.root)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.outbox_left(), [])       # nothing put back into outbox/ (D3)
        self.assertEqual(self.pm_files("failed")[0], ["TASK-12-a.json"])
        self.assertIn("send-failed", json.dumps(self.result("failed")))
        self.conn.execute("DROP TRIGGER boom")         # the database is healthy again
        self.conn.commit()
        scan(self.conn, self.root)
        self.assertEqual(self.rows(), [])              # D3: no resend loop
        self.assertEqual(self.pm_files("failed")[0], ["TASK-12-a.json"])
        self.assertEqual(self.outbox_left(), [])

    def test_remote_unsupported_one_copy(self):
        os.environ["ROOKERY_URL"] = "http://127.0.0.1:9"
        self.drop("a.json", fixture_block())
        scan(self.conn, self.root)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.outbox_left(), [])
        self.assertEqual(self.pm_files("failed")[0], ["TASK-12-a.json"])
        self.assertIn("remote-unsupported", json.dumps(self.result("failed")))
        self.assertEqual(len(self.payloads()), 1, self.payloads())   # exactly one copy


class NoFileLost(Base):
    def test_every_input_file_ends_in_exactly_one_place(self):
        self.drop("ok.json", fixture_block())
        self.drop("bad.json", "{not json")
        self.drop("bad2.json", fixture_block("malformed-status.txt"))
        os.makedirs(self.pm("sending"))
        with open(os.path.join(self.pm("sending"), "old.json"), "w") as fh:
            fh.write(fixture_block())
        self.drop("lc.json", json.dumps(dict(json.loads(fixture_block()), to="me")), name="law-counsel")
        before = len(self.payloads())
        self.assertEqual(before, 5)
        scan(self.conn, self.root)
        scan(self.conn, self.root)
        self.assertEqual(len(self.payloads()), before)
        self.assertEqual(self.outbox_left(), [])
        self.assertEqual(self.pm_files("sending"), ([], []))
        sent, failed = self.pm_files("sent")[0], self.pm_files("failed")[0]
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(len(failed), 3, failed)
        self.assertEqual(len(self.pm_files("sent", "law-counsel")[0]), 1)


class HandoffUntouched(Base):
    def handoff_path(self):
        return os.path.join(self.ws("me"), "runs", "TASK-12", "HANDOFF.md")

    def test_scan_never_writes_handoff(self):
        self.drop("a.json", fixture_block())
        scan(self.conn, self.root)
        self.assertEqual(glob.glob(os.path.join(self.root, "**", "HANDOFF.md"), recursive=True), [])

    def test_putback_from_turn_n_does_not_overwrite_turn_n1_handoff(self):    # D11
        old = dict(json.loads(fixture_block()), say="TURN-N put-back block")
        self.drop("TASK-12-20260101T000000000000Z.json", json.dumps(old))     # as the Stop hook would
        newer = dict(old, say="TURN-N1 newer block", status="in-progress")
        ctx = {"root": self.root, "session": self.alpha, "identity": self.me, "adapter": "stop"}
        text = "turn N+1\n\n```rookery\n" + json.dumps(newer) + "\n```\n"
        res = TB.dispatch_text(self.conn, text, ctx)
        self.assertEqual(res["outcome"], "sent", res)
        with open(self.handoff_path(), encoding="utf-8") as fh:
            handoff = fh.read()
        self.assertIn("TURN-N1 newer block", handoff)
        self.assertNotIn("TURN-N put-back", handoff)
        scan(self.conn, self.root)
        with open(self.handoff_path(), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), handoff)
        self.assertEqual(len(self.rows()), 2)                                 # newer + put-back
        self.assertEqual(self.outbox_left(), [])


class WaveNineteen(Base):
    def log_rows(self):
        p = os.path.join(self.root, "postmaster", "postmaster.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def test_f1_concurrent_scans_do_not_interrupt_each_other(self):
        self.drop("a.json", fixture_block())
        orig = TB.dispatch_block
        started = threading.Event()

        def slow(conn, raw, ctx):
            started.set()
            time.sleep(0.8)
            return orig(conn, raw, ctx)

        TB.dispatch_block = slow
        try:
            t = threading.Thread(target=lambda: scan(TB.open_db(), self.root))
            t.start()
            self.assertTrue(started.wait(5))
            scan(self.conn, self.root)          # second scan while the first is mid-delivery
            t.join()
        finally:
            TB.dispatch_block = orig
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.pm_files("sent")[0], ["TASK-12-a.json"])
        self.assertEqual(self.result("sent")["outcome"], "sent")
        self.assertEqual(self.pm_files("failed"), ([], []))

    def test_f4_long_filename_lands_in_sent(self):
        fname = "x" * 245 + ".json"
        self.drop(fname, fixture_block())
        scan(self.conn, self.root)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.pm_files("sending"), ([], []))
        payload, res = self.pm_files("sent")
        self.assertEqual((len(payload), len(res)), (1, 1))
        self.assertTrue(all(len(n.encode()) <= 255 for n in payload + res))
        self.assertFalse(any(r.get("outcome") == "outbox-error" for r in self.log_rows()))

    def test_f5_symlinked_outbox_dir_is_skipped(self):
        foreign = os.path.join(self.tmp.name, "foreign")
        os.makedirs(foreign)
        with open(os.path.join(foreign, "a.json"), "w") as fh:
            fh.write(fixture_block())
        os.makedirs(self.ws("me"))
        os.symlink(foreign, os.path.join(self.ws("me"), "outbox"))
        scan(self.conn, self.root)
        self.assertEqual(self.rows(), [])
        self.assertTrue(os.path.exists(os.path.join(foreign, "a.json")))

    def test_f6_fresh_file_skipped_until_two_seconds_old(self):
        src = self.drop("a.json", fixture_block())
        now = os.path.getmtime(src)
        scan(self.conn, self.root, now=now + 0.5)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(self.rows(), [])
        scan(self.conn, self.root, now=now + 3)
        self.assertFalse(os.path.exists(src))
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
