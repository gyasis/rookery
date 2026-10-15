#!/usr/bin/env python3
"""Rookery turn block, US1 part 1 (T014): dispatch_text() on the fixtures of fixtures/turns/.

    python3 -m unittest test_turn_block -v

ASSUMED API (research D3 + tasks.md T018; change only `dispatch()` below if it differs):
    turn_block.dispatch_text(conn, text, ctx) -> {"outcome": str, "msg_id": int|None, "reason": str|None}
    ctx = {"root": <Rookery root>, "session": <rookery_sessions row>,
           "identity": <sender identities row>, "adapter": "stop" | "turn" | "outbox"}
    outcome is one of: sent | no-block | quarantined | handoff-pending | send-failed |
                       remote-unsupported | no-session | db-busy
    One log_event() line with the key "outcome" is written per call (FR-022, D15).
The Stop-hook path is run as `python3 rookery_cli.py hook stop --project p` (subprocess, JSON on stdin).
Tests use a temp ROOKERY_DB, HOME and ROOKERY_ROOT and never touch the real database.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import rookery as R
import rookery_identity as RI
import turn_block as TB

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "turns")
ENV_KEYS = ("ROOKERY_DB", "ROOKERY_ROOT", "HOME", "ROOKERY_URL", "ROOKERY_HOLD",
            "ROOKERY_SESSION", "ROOKERY_NODE")

# fixture -> (outcome, rows written for the block: 0 | 1 | "q" = 1 quarantined + 1 bounce)
# "q" entries add the quarantine reason and a word the bounce note must contain.
OK = "sent"
FIXTURE_TABLE = {
    "no-block.txt": ("no-block", 0),
    "unclosed-fence.txt": ("no-block", 0),
    "valid-ask.txt": (OK, 1),
    "two-blocks.txt": (OK, 1),
    "example-then-real.txt": (OK, 1),
    "only-example-block.txt": (OK, 1),
    "backticks-in-say.txt": (OK, 1),
    "handoff-in-progress.txt": ("handoff-pending", 0),
    "handoff-done.txt": (OK, 1),
}
MALFORMED = {   # fixture -> text the first-failed-rule note must contain
    "malformed-json.txt": "JSON",
    "malformed-missing-to.txt": '"to"',
    "malformed-kind-veto.txt": "veto",
    "malformed-call-field.txt": "call",
    "malformed-unknown-field.txt": "field",
    "malformed-status.txt": "status",
    "malformed-oversize.txt": "4,096",
    "malformed-say-long.txt": "say",
    "malformed-read-multiline.txt": "read",
    "malformed-read-long.txt": "read",
    "malformed-read-outside.txt": "read",
    "malformed-read-dotdot.txt": "read",
    "malformed-task-path.txt": "task",
}
NEXT_STEP = re.compile(r"\b(again|Fix|Put|Add|Use|Give|Ask|Shorten|Remove|Send)\b")


def fixture_text(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def dispatch(conn, text, ctx):
    return TB.dispatch_text(conn, text, ctx)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ("ROOKERY_URL", "ROOKERY_HOLD", "ROOKERY_SESSION", "ROOKERY_NODE"):
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = os.path.join(self.tmp.name, "t.db")
        os.environ["ROOKERY_ROOT"] = self.root
        self.conn = TB.open_db()
        self.alpha = RI.new_session(self.conn, name="alpha")
        self.beta = RI.new_session(self.conn, name="beta")
        self.me = RI.new_identity(self.conn, self.alpha["session_id"], "me")
        self.lc = RI.new_identity(self.conn, self.alpha["session_id"], "law-counsel")
        self.beta_only = RI.new_identity(self.conn, self.beta["session_id"], "beta-only")
        self.ctx = {"root": self.root, "session": self.alpha, "identity": self.me,
                    "adapter": "stop"}

    def tearDown(self):
        self.conn.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # helpers
    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM inbox ORDER BY id")]

    def log_lines(self):
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def outcome_lines(self):
        return [x for x in self.log_lines() if "outcome" in x]

    def failed_files(self):
        return glob.glob(os.path.join(self.root, "agents", "alpha", "me", "outbox", "failed", "*.json"))

    def all_files(self):
        return [os.path.join(d, f) for d, _, fs in os.walk(self.root) for f in fs
                if not f.endswith(".jsonl")]

    def run_fixture(self, name, ctx=None):
        self.assertTrue(os.path.exists(os.path.join(FIXTURES, name)),
                        f"missing fixture file fixtures/turns/{name}")
        return dispatch(self.conn, fixture_text(name), ctx or self.ctx)

    def split(self):
        """(original, bounce) of a quarantined block."""
        rows = self.rows()
        self.assertEqual(len(rows), 2, rows)
        orig, bounce = rows
        self.assertEqual(bounce["sender"], "rookery")
        return orig, bounce


class FixtureTable(Base):
    """Every fixture against the outcome table of data-model.md; one log line each."""

    def test_table_fixtures(self):
        for name, (outcome, nrows) in FIXTURE_TABLE.items():
            with self.subTest(fixture=name):
                self.setUp_fresh()
                res = self.run_fixture(name)
                self.assertEqual(res["outcome"], outcome)
                self.assertEqual(len(self.rows()), nrows)
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], [outcome])

    def test_malformed_fixtures(self):
        for name, word in MALFORMED.items():
            with self.subTest(fixture=name):
                self.setUp_fresh()
                res = self.run_fixture(name)
                self.assertEqual(res["outcome"], "quarantined")
                orig, bounce = self.split()
                self.assertEqual((orig["status"], orig["reason"], orig["decided_by"]),
                                 ("quarantined", "malformed", "turn-block-parser"))
                note = orig["note"] or ""
                self.assertIn(word, note)
                self.assertRegex(note, NEXT_STEP)
                self.assertEqual(json.loads(bounce["body"])["note"], note)
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["quarantined"])

    def setUp_fresh(self):
        """Empty the mail and the log between subtests (sessions and identities stay)."""
        self.conn.execute("DELETE FROM inbox")
        self.conn.commit()
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        if os.path.exists(p):
            open(p, "w").close()


class ValidRow(Base):
    def test_row_fields(self):
        res = self.run_fixture("valid-ask.txt")
        self.assertEqual(res["outcome"], "sent")
        (row,) = self.rows()
        self.assertEqual(res["msg_id"], row["id"])
        self.assertEqual((row["sender"], row["recipient"]), (self.me["agent_id"], self.lc["agent_id"]))
        self.assertEqual(row["topic"], "TASK-12")
        self.assertEqual(row["session_id"], self.alpha["session_id"])
        self.assertEqual(row["status"], "pending")
        block = json.loads(row["body"])
        self.assertEqual(row["body"], json.dumps(block, sort_keys=True, separators=(",", ":")))
        self.assertEqual(block["thread"], "lease-review")   # carried unchanged (C4)
        raw = re.search(r"```rookery\s*\n(.*?)\n```", fixture_text("valid-ask.txt"), re.S)
        self.assertEqual(block, json.loads(raw.group(1)))    # no field dropped or added

    def test_last_block_wins(self):
        self.run_fixture("two-blocks.txt")
        (row,) = self.rows()
        self.assertIn("SECOND-BLOCK", json.loads(row["body"])["say"])
        self.assertNotIn("FIRST-BLOCK", row["body"])

    def test_example_fence_is_not_the_block(self):
        self.run_fixture("example-then-real.txt")
        (row,) = self.rows()
        block = json.loads(row["body"])
        self.assertEqual((block["to"], block["task"]), ("law-counsel", "TASK-12"))
        self.assertTrue(block["say"].startswith("REAL-BLOCK"), block["say"])

    def test_only_block_in_the_middle(self):
        self.run_fixture("only-example-block.txt")
        (row,) = self.rows()
        self.assertTrue(json.loads(row["body"])["say"].startswith("MIDDLE-BLOCK"))

    def test_hold_makes_row_held(self):
        os.environ["ROOKERY_HOLD"] = "1"
        res = self.run_fixture("valid-ask.txt")
        self.assertEqual(res["outcome"], "sent")
        (row,) = self.rows()
        self.assertEqual(row["status"], "held")

    def test_handoff_in_progress_sends_nothing(self):
        res = self.run_fixture("handoff-in-progress.txt")
        self.assertEqual(res["outcome"], "handoff-pending")
        self.assertEqual(self.rows(), [])

    def test_handoff_done_sends_one_row(self):            # FR-007
        res = self.run_fixture("handoff-done.txt")
        self.assertEqual(res["outcome"], "sent")
        (row,) = self.rows()
        self.assertEqual(json.loads(row["body"])["kind"], "handoff")
        self.assertEqual(row["status"], "pending")

    def test_handoff_done_held(self):
        os.environ["ROOKERY_HOLD"] = "1"
        self.run_fixture("handoff-done.txt")
        self.assertEqual(self.rows()[0]["status"], "held")


class Quarantine(Base):
    def test_bounce_inherits_session(self):               # FR-034
        self.run_fixture("malformed-json.txt")
        orig, bounce = self.split()
        self.assertEqual(orig["session_id"], self.alpha["session_id"])
        self.assertEqual(bounce["session_id"], orig["session_id"])
        self.assertEqual(bounce["recipient"], self.me["agent_id"])
        self.assertEqual(orig["sender"], self.me["agent_id"])

    def test_missing_to_uses_rookery_recipient(self):     # D4
        self.run_fixture("malformed-missing-to.txt")
        orig, _ = self.split()
        self.assertEqual(orig["recipient"], "rookery")

    def test_oversize_note_states_limit(self):
        self.run_fixture("malformed-oversize.txt")
        orig, _ = self.split()
        self.assertIn("4,096 bytes", orig["note"])

    def test_wrong_recipient_suggests_close_name(self):
        res = self.run_fixture("wrong-recipient.txt")
        self.assertEqual(res["outcome"], "quarantined")
        orig, bounce = self.split()
        self.assertEqual((orig["status"], orig["reason"], orig["decided_by"]),
                         ("quarantined", "wrong-recipient", "turn-block-parser"))
        self.assertEqual(orig["recipient"], "law-cousel")   # raw `to` text (D22)
        self.assertEqual(bounce["session_id"], self.alpha["session_id"])
        body = json.loads(bounce["body"])
        self.assertEqual(body["reason"], "wrong-recipient")
        suggest = body["suggest"]
        self.assertEqual(suggest if isinstance(suggest, list) else json.loads(suggest), ["law-counsel"])

    def test_off_team_has_no_suggest(self):
        # fixtures/turns/off-team.txt names "beta-only", an identity that exists only in session beta
        self.assertIn('"beta-only"', fixture_text("off-team.txt"))
        res = self.run_fixture("off-team.txt")
        self.assertEqual(res["outcome"], "quarantined")
        orig, bounce = self.split()
        self.assertEqual((orig["status"], orig["reason"], orig["decided_by"]),
                         ("quarantined", "off-team", "turn-block-parser"))
        body = json.loads(bounce["body"])
        self.assertEqual(body["reason"], "off-team")
        self.assertFalse(body.get("suggest"))
        self.assertEqual(bounce["session_id"], self.alpha["session_id"])


class RemoteAndNoSession(Base):
    def test_remote_unsupported_direct(self):
        os.environ["ROOKERY_URL"] = "http://127.0.0.1:9"
        res = self.run_fixture("valid-ask.txt")
        self.assertEqual(res["outcome"], "remote-unsupported")
        self.assertEqual(self.rows(), [])
        files = self.failed_files()
        self.assertEqual(len(files), 1, self.all_files())
        self.assertRegex(os.path.basename(files[0]), r"^TASK-12-.+\.json$")
        self.assertEqual(json.load(open(files[0]))["task"], "TASK-12")
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["remote-unsupported"])

    def test_remote_unsupported_via_hook(self):
        out = self.hook(fixture_text("valid-ask.txt"), {"ROOKERY_URL": "http://127.0.0.1:9"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.failed_files()), 1, self.all_files())
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["remote-unsupported"])

    def test_no_session_via_hook(self):
        before = set(self.all_files())
        out = self.hook(fixture_text("valid-ask.txt"), {}, session=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertEqual(self.rows(), [])
        self.assertEqual(set(self.all_files()), before)

    def test_no_session_writes_nothing_at_all(self):
        """Fix 1: no session + no DB -> no file anywhere (DB, logs, jsonl)."""
        base = tempfile.mkdtemp(dir=self.tmp.name)
        home, root, dbdir = (os.path.join(base, n) for n in ("home", "root", "dbdir"))
        for d in (home, root, dbdir):
            os.makedirs(d)
        env = dict(os.environ, HOME=home, ROOKERY_ROOT=root,
                   ROOKERY_DB=os.path.join(dbdir, "none.db"))
        for k in ("ROOKERY_SESSION", "ROOKERY_NODE", "ROOKERY_URL", "ROOKERY_HOLD"):
            env.pop(k, None)
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "rookery_cli.py"), "hook", "stop", "--project", "p"],
            input=json.dumps({"last_assistant_message": "x"}), capture_output=True,
            text=True, env=env, cwd=HERE, timeout=30)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        for d in (home, root, dbdir):
            self.assertEqual([f for _, _, fs in os.walk(d) for f in fs], [], d)

    def test_bad_stdin_logs_exactly_one_line(self):
        """Fix 2: garbage stdin -> one outcome line, bad-input."""
        env = dict(os.environ, ROOKERY_SESSION="alpha", ROOKERY_NODE="me")
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "rookery_cli.py"), "hook", "stop", "--project", "p"],
            input="not json {", capture_output=True, text=True, env=env, cwd=HERE, timeout=30)
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["bad-input"])

    def test_hook_bad_args_exit_zero_silently(self):
        """Fix 3: a hook never exits 2 / prints usage."""
        for argv in (["hook", "stop"], ["hook", "stop", "--bogus"],
                     ["hook", "stop", "--project", "p", "--bogus"], ["hook"]):
            out = subprocess.run([sys.executable, os.path.join(HERE, "rookery_cli.py")] + argv,
                                 input="", capture_output=True, text=True, cwd=HERE, timeout=30)
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""), argv)

    def test_hook_valid_ask_matches_direct(self):
        out = self.hook(fixture_text("valid-ask.txt"), {})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "")
        (row,) = self.rows()
        self.assertEqual((row["sender"], row["recipient"], row["topic"]),
                         (self.me["agent_id"], self.lc["agent_id"], "TASK-12"))
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["sent"])

    def test_hook_malformed_matches_direct(self):
        out = self.hook(fixture_text("malformed-json.txt"), {})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "")
        orig, _ = self.split()
        self.assertEqual((orig["status"], orig["reason"]), ("quarantined", "malformed"))

    def hook(self, message, extra_env, session=True):
        env = dict(os.environ)
        for k in ("ROOKERY_URL", "ROOKERY_HOLD", "ROOKERY_SESSION", "ROOKERY_NODE"):
            env.pop(k, None)
        if session:
            env.update(ROOKERY_SESSION="alpha", ROOKERY_NODE="me")
        env.update(extra_env)
        stop_json = json.dumps({"hook_event_name": "Stop", "session_id": "claude-test",
                                "cwd": self.tmp.name, "last_assistant_message": message})
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "rookery_cli.py"), "hook", "stop", "--project", "p"],
            input=stop_json, capture_output=True, text=True, env=env, cwd=HERE, timeout=30)


class KeepUnresolved(Base):
    """Fix 4: _keep_unresolved filename safety and collisions (stub validate, restored)."""
    def _call(self, task):
        import cli_hook
        saved = getattr(TB, "validate", None)
        had = hasattr(TB, "validate")
        saved_find = getattr(TB, "find_last_block", None)
        had_find = hasattr(TB, "find_last_block")
        TB.find_last_block = lambda text: "raw"
        TB.validate = lambda raw, root: ({"task": task, "to": "x"}, None)
        try:
            cli_hook._keep_unresolved(TB, self.root, self.alpha, "text")
        finally:
            for name, had_, val in (("validate", had, saved), ("find_last_block", had_find, saved_find)):
                if had_:
                    setattr(TB, name, val)
                else:
                    delattr(TB, name)

    def _files(self):
        return glob.glob(os.path.join(self.root, "unresolved", "*"))

    def test_same_second_calls_do_not_overwrite(self):
        self._call("TASK-1")
        self._call("TASK-1")
        self.assertEqual(len(self._files()), 2, self._files())

    def test_unsafe_task_keeps_nothing(self):
        self._call("../x")
        self.assertEqual(self._files(), [])
        self.assertEqual(glob.glob(os.path.join(self.root, "x*")), [])
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["no-identity"])
        self.assertFalse(self.outcome_lines()[0]["kept"])


if __name__ == "__main__":
    unittest.main()
