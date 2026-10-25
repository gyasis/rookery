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
import sqlite3
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


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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


class StopHookPart2(Base):
    """T015 (RQ-1.4, D1, D16): `rookery hook stop --project p` as a subprocess.

    The part-1 fixture table is driven through the hook. send() failure is made REAL
    with a TRIGGER on inbox (BEFORE INSERT -> RAISE(ABORT,'boom')); the db-busy case
    holds a write lock from this process on a second connection."""

    CLI = os.path.join(HERE, "rookery_cli.py")

    def run_hook(self, message="__omit__", env_extra=None, session=True, node="me", timeout=30):
        env = dict(os.environ)
        for k in ("ROOKERY_URL", "ROOKERY_HOLD", "ROOKERY_SESSION", "ROOKERY_NODE"):
            env.pop(k, None)
        # a real pane/terminal binding in the caller's env must not resolve an identity
        for var in (RI.load_config(persist=False).get("session_binding_sources") or {}):
            env.pop(var, None)
        if session:
            env["ROOKERY_SESSION"] = "alpha"
            if node:
                env["ROOKERY_NODE"] = node
        env.update(env_extra or {})
        data = {"hook_event_name": "Stop", "session_id": "claude-test", "cwd": self.tmp.name}
        if message != "__omit__":
            data["last_assistant_message"] = message
        out = subprocess.run([sys.executable, self.CLI, "hook", "stop", "--project", "p"],
                             input=json.dumps(data), capture_output=True, text=True,
                             env=env, cwd=HERE, timeout=timeout)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        return out

    def reset(self):
        self.conn.execute("DELETE FROM inbox")
        self.conn.commit()
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        if os.path.exists(p):
            open(p, "w").close()

    def outbox_files(self):
        return glob.glob(os.path.join(self.root, "agents", "alpha", "me", "outbox", "TASK-12-*.json"))

    def unresolved_files(self):
        return glob.glob(os.path.join(self.root, "unresolved", "*.json"))

    def test_fixture_table_through_hook(self):
        for name, (outcome, nrows) in FIXTURE_TABLE.items():
            with self.subTest(fixture=name):
                self.reset()
                self.run_hook(fixture_text(name))
                self.assertEqual(len(self.rows()), nrows)
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], [outcome])

    def test_malformed_and_routing_through_hook(self):
        for name, word in MALFORMED.items():
            with self.subTest(fixture=name):
                self.reset()
                self.run_hook(fixture_text(name))
                orig, bounce = self.split()
                self.assertEqual((orig["status"], orig["reason"]), ("quarantined", "malformed"))
                self.assertIn(word, orig["note"] or "")
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["quarantined"])
        for name, reason in (("wrong-recipient.txt", "wrong-recipient"), ("off-team.txt", "off-team")):
            with self.subTest(fixture=name):
                self.reset()
                self.run_hook(fixture_text(name))
                orig, _ = self.split()
                self.assertEqual((orig["status"], orig["reason"]), ("quarantined", reason))
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["quarantined"])

    def test_no_message_field(self):
        self.run_hook()                      # Stop JSON without last_assistant_message
        self.assertEqual(self.rows(), [])
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["no-message-field"])
        self.assertEqual(self.outbox_files(), [])

    def test_send_error_goes_to_outbox(self):
        """Method: TRIGGER BEFORE INSERT ON inbox raises ABORT, so rookery.send() really raises."""
        self.conn.execute("CREATE TRIGGER boom BEFORE INSERT ON inbox "
                          "BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.conn.commit()
        self.run_hook(fixture_text("valid-ask.txt"))
        self.assertEqual(self.rows(), [])
        files = self.outbox_files()
        self.assertEqual(len(files), 1, self.all_files())
        block = load_json(files[0])
        self.assertEqual((block["to"], block["task"]), ("law-counsel", "TASK-12"))
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["send-failed"])

    def _db_busy(self, begin):
        import time
        self.reset()
        locker = sqlite3.connect(os.environ["ROOKERY_DB"], timeout=1, isolation_level=None)
        try:
            locker.execute(begin)
            t0 = time.monotonic()
            self.run_hook(fixture_text("valid-ask.txt"))
            elapsed = time.monotonic() - t0
        finally:
            try:
                locker.execute("ROLLBACK")
            finally:
                locker.close()
        self.assertLess(elapsed, 3.0)
        self.assertEqual(self.rows(), [])
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["db-busy"])
        files = self.outbox_files()
        self.assertEqual(len(files), 1, self.all_files())
        self.assertEqual(load_json(files[0])["task"], "TASK-12")

    def test_db_busy_write_lock_immediate(self):
        self._db_busy("BEGIN IMMEDIATE")

    def test_stdout_empty_without_and_with_unread_mail(self):
        out = self.run_hook(fixture_text("valid-ask.txt"))
        self.assertEqual(out.stdout, "")
        # unread mail for `me` (pending row from law-counsel), then another Stop
        R.send(self.conn, self.lc["agent_id"], self.me["agent_id"], "unread note", topic="TASK-9",
               session_id=self.alpha["session_id"])
        pending = [r for r in self.rows() if r["recipient"] == self.me["agent_id"] and r["status"] == "pending"]
        self.assertTrue(pending, self.rows())
        out = self.run_hook(fixture_text("no-block.txt"))
        self.assertEqual((out.stdout, out.stderr), ("", ""))

    def test_db_busy_in_open_keeps_block_unresolved(self):          # audit 6, fix 7
        """Method: make the schema NOT current (delete the 'legacy' session row) so open_db()
        must migrate, while another connection holds BEGIN IMMEDIATE: open_db() raises
        OperationalError('database is locked') before any identity is resolved."""
        self.reset()
        self.conn.execute("DELETE FROM rookery_sessions WHERE session_id='legacy'")
        self.conn.commit()
        locker = sqlite3.connect(os.environ["ROOKERY_DB"], timeout=1, isolation_level=None)
        try:
            locker.execute("BEGIN IMMEDIATE")
            self.run_hook(fixture_text("valid-ask.txt"))
        finally:
            try:
                locker.execute("ROLLBACK")
            finally:
                locker.close()
        files = self.unresolved_files()
        self.assertEqual(len(files), 1, self.all_files())
        self.assertEqual(load_json(files[0])["task"], "TASK-12")
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["db-busy"])
        self.assertEqual(self.rows(), [])

    def test_db_busy_during_quarantine_leaves_nothing(self):
        import time
        self.reset()
        locker = sqlite3.connect(os.environ["ROOKERY_DB"], timeout=1, isolation_level=None)
        try:
            locker.execute("BEGIN IMMEDIATE")
            t0 = time.monotonic()
            self.run_hook(fixture_text("wrong-recipient.txt"))
            elapsed = time.monotonic() - t0
        finally:
            try:
                locker.execute("ROLLBACK")
            finally:
                locker.close()
        self.assertLess(elapsed, 3.0)
        self.assertEqual(self.rows(), [])
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["db-busy"])
        self.assertEqual(len(self.outbox_files()), 1, self.all_files())

    def test_session_without_identity_keeps_block_unresolved(self):
        ids_before = [dict(r) for r in self.conn.execute("SELECT * FROM identities ORDER BY agent_id")]
        self.run_hook(fixture_text("valid-ask.txt"), node=None)
        self.assertEqual(self.rows(), [])
        files = self.unresolved_files()
        self.assertEqual(len(files), 1, self.all_files())
        self.assertRegex(os.path.basename(files[0]), r"^TASK-12-.+\.json$")
        self.assertEqual(load_json(files[0])["task"], "TASK-12")
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["no-identity"])
        ids_after = [dict(r) for r in self.conn.execute("SELECT * FROM identities ORDER BY agent_id")]
        self.assertEqual(ids_after, ids_before)


class ValidateEdgeCases(unittest.TestCase):
    """Direct validate() checks: surrogates, control chars, rule order, kinds."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    BASE = {"to": "law-counsel", "task": "TASK-12", "kind": "ask", "say": "hello", "status": "done"}

    def v(self, raw):
        return TB.validate(raw, self.root)

    def vd(self, **over):
        d = dict(self.BASE)
        d.update(over)
        return self.v(json.dumps(d))

    def assertSafeNote(self, note):
        self.assertIsInstance(note, str)
        note.encode("utf-8")
        sqlite3.connect(":memory:").execute("select ?", (note,))

    def test_base_is_valid(self):
        self.assertEqual(self.vd()[1], None)

    def test_lone_surrogate_key(self):
        block, note = self.v('{"\\ud800":1}')
        self.assertIsNone(block)
        self.assertIn("The block is not valid JSON: ", note)
        self.assertIn("lone surrogate", note)
        self.assertTrue(note.endswith("Fix the JSON, then send the block again."))
        self.assertSafeNote(note)

    def test_lone_surrogate_values(self):
        base = json.dumps(self.BASE)
        for raw in (base.replace('"hello"', '"\\ud800"'),
                    base[:-1] + ', "thread": "\\udc00"}'):
            block, note = self.v(raw)
            self.assertIsNone(block, raw)
            self.assertIn("The block is not valid JSON: ", note)
            self.assertSafeNote(note)

    def test_surrogate_in_nested_artifact(self):
        raw = json.dumps(self.BASE)[:-1] + ', "artifacts": [{"path": "a\\ud800"}]}'
        block, note = self.v(raw)
        self.assertIsNone(block)
        self.assertIn("lone surrogate", note)

    def test_show_strips_controls(self):
        raw = json.dumps(dict(self.BASE, **{"\x1b[31m\nEVIL\u2028\u0085\x7f\x9f": 1}))
        block, note = self.v(raw)
        self.assertIsNone(block)
        self.assertIn("is not allowed in a block", note)
        for ch in note:
            o = ord(ch)
            self.assertFalse(o < 32 or 0x7f <= o <= 0x9f or ch in "\u2028\u2029", repr(ch))
        self.assertSafeNote(note)

    def test_show_is_bounded(self):
        block, note = self.v(json.dumps(dict(self.BASE, **{"\x1b" * 500: 1})))
        self.assertLess(len(note), 400)

    def test_to_value_beats_say_type(self):
        block, note = self.vd(to="bad name!", say=5)
        self.assertIsNone(block)
        self.assertIn('The field "to" value', note)
        self.assertIn("not a safe name", note)

    def test_veto_beats_status_type(self):
        block, note = self.vd(kind="veto", status=5)
        self.assertIsNone(block)
        self.assertIn('The kind "veto" is for the system only', note)

    def test_missing_beats_bad_type(self):
        d = dict(self.BASE, say=5)
        del d["status"]
        block, note = self.v(json.dumps(d))
        self.assertIn('The field "status" is missing', note)

    def test_type_errors_in_own_rule(self):
        self.assertIn('The field "to" must be a string', self.vd(to=5)[1])
        self.assertIn('The field "task" must be a string', self.vd(task=5)[1])
        self.assertIn('The field "kind" must be a string', self.vd(kind=5)[1])
        self.assertIn('The field "status" must be a string', self.vd(status=5)[1])
        self.assertIn('The field "say" must be a string', self.vd(say=5)[1])

    def test_kind_veto_contract_text(self):
        self.assertEqual(self.vd(kind="veto")[1],
            'The kind "veto" is for the system only. Use ask, answer, decline, handoff, '
            "status or recruit, then send the block again.")

    def test_kind_takeover_system_only(self):
        self.assertIn('The kind "takeover" is for the system only.', self.vd(kind="takeover")[1])

    def test_kind_unknown(self):
        self.assertEqual(self.vd(kind="banana")[1],
            'The kind "banana" is not a kind. Use ask, answer, decline, handoff, '
            "status or recruit, then send the block again.")


class AtomicQuarantine(Base):
    """Audit 6, fix 1: hold + quarantine + bounce are ONE transaction (D4)."""

    def outbox_plain(self):
        return glob.glob(os.path.join(self.root, "agents", "alpha", "me", "outbox", "*.json"))

    def _check_rolled_back(self, res):
        self.assertEqual(res["outcome"], "send-failed", res)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.outbox_plain()), 1, self.all_files())
        self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["send-failed"])

    def test_quarantine_update_fails_leaves_nothing(self):
        self.conn.execute("CREATE TRIGGER boom BEFORE UPDATE ON inbox "
                          "WHEN NEW.status='quarantined' BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.conn.commit()
        self._check_rolled_back(self.run_fixture("wrong-recipient.txt"))

    def test_bounce_insert_fails_leaves_nothing(self):
        self.conn.execute("CREATE TRIGGER boom BEFORE INSERT ON inbox "
                          "WHEN NEW.sender='rookery' BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.conn.commit()
        self._check_rolled_back(self.run_fixture("wrong-recipient.txt"))

    def test_quarantine_is_one_commit(self):
        stmts = []   # Connection.commit is read-only, so count COMMITs with a trace callback
        self.conn.set_trace_callback(stmts.append)
        self.run_fixture("wrong-recipient.txt")
        self.conn.set_trace_callback(None)
        self.assertEqual(sum(1 for s in stmts if s.strip().upper() == "COMMIT"), 1, stmts)
        writes = [s for s in stmts if not s.lstrip().upper().startswith("SELECT")]
        self.assertTrue(writes[0].upper().startswith("BEGIN IMMEDIATE"), writes[:2])


class QuarantineHardening(Base):
    """Audit 6, fixes 2, 3, 5b, 6."""

    def test_agent_id_recipient_is_masked(self):                       # fix 2
        res = self.run_fixture_text(self.block(to=self.beta_only["agent_id"], say="SECRET-BODY"))
        self.assertEqual((res["outcome"], res["reason"]), ("quarantined", "off-team"))
        orig, _ = self.split()
        self.assertEqual(orig["recipient"], "rookery")
        self.assertIn(self.beta_only["agent_id"], orig["note"])
        for m in R.fetch_thread(self.conn, self.beta_only["agent_id"]):
            self.assertNotIn("SECRET-BODY", m["body"])

    def test_node_id_recipient_is_masked(self):                        # fix 2 (nodes)
        self.conn.execute("INSERT INTO nodes(node_id, status, last_seen) VALUES('ag-nodeonly01','idle',0)")
        self.conn.commit()
        self.run_fixture_text(self.block(to="ag-nodeonly01"))
        orig, _ = self.split()
        self.assertEqual(orig["recipient"], "rookery")

    def test_malformed_body_and_to_are_capped(self):                   # fix 3
        raw = json.dumps({"to": "x" * 1000, "task": "T-1", "kind": "ask", "say": "y" * 1_000_000,
                          "status": "done"})
        res = TB.dispatch_text(self.conn, "```rookery\n" + raw + "\n```", self.ctx)
        self.assertEqual(res["outcome"], "quarantined", res)
        orig, _ = self.split()
        self.assertLessEqual(len(orig["body"].encode()), 8192 + 100)
        self.assertIn("truncated", orig["body"])
        self.assertLessEqual(len(orig["recipient"]), 64)

    def test_malformed_body_cut_is_utf8_safe(self):
        raw = "\u00e9" * 20000      # 2 bytes each; 8192 is even, try odd-boundary too
        for pad in ("", "a"):
            self.conn.execute("DELETE FROM inbox")
            self.conn.commit()
            TB.dispatch_text(self.conn, "```rookery\n" + pad + raw + "\n```", self.ctx)
            body = self.rows()[0]["body"]
            body.encode("utf-8")
            self.assertLessEqual(len(body.encode()), 8192 + 100)

    def test_agent_id_beats_lookalike_name(self):                      # fix 5b
        # legacy/hand-made row whose NAME equals another identity's agent ID
        self.conn.execute("INSERT INTO identities(agent_id, session_id, name, previous_id, created_at) "
                          "VALUES('ag-zzzzzzzzzz', ?, ?, NULL, '2026-01-01T00:00:00Z')",
                          (self.alpha["session_id"], self.lc["agent_id"]))
        self.conn.commit()
        res = self.run_fixture_text(self.block(to=self.lc["agent_id"]))
        self.assertEqual(res["outcome"], "sent", res)
        self.assertEqual(self.rows()[0]["recipient"], self.lc["agent_id"])

    def test_suggest_excludes_sender_name(self):                       # fix 6
        self.run_fixture_text(self.block(to="mee"))
        _, bounce = self.split()
        body = json.loads(bounce["body"])
        self.assertNotIn("me", body["suggest"] or [])
        self.assertNotIn('"me"', self.rows()[0]["note"])

    def block(self, **kw):
        o = {"to": "law-counsel", "task": "T-1", "kind": "ask", "say": "hi", "status": "done"}
        o.update(kw)
        return json.dumps(o)

    def run_fixture_text(self, raw):
        return TB.dispatch_text(self.conn, "```rookery\n" + raw + "\n```", self.ctx)


class NeverRaises(Base):
    """Audit 6, fix 4."""

    def _check(self, ctx, expect_log=True):
        for fn in (lambda: TB.dispatch_block(self.conn, json.dumps(
                       {"to": "law-counsel", "task": "T-1", "kind": "ask", "say": "hi",
                        "status": "done"}), ctx),
                   lambda: TB.dispatch_text(self.conn, "```rookery\n{}\n```", ctx),
                   lambda: TB.dispatch_text(self.conn, "no block", ctx)):
            if os.path.exists(os.path.join(self.root, "logs")):
                open(os.path.join(self.root, "logs", "turn-block.jsonl"), "w").close()
            res = fn()
            self.assertEqual(res["outcome"], "send-failed", res)
            if expect_log:
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], ["send-failed"])

    def test_identity_none(self):
        self._check(dict(self.ctx, identity=None))

    def test_session_none(self):
        self._check(dict(self.ctx, session=None))

    def test_no_root(self):
        ctx = dict(self.ctx)
        del ctx["root"]
        self._check(ctx, expect_log=False)


SECTIONS = ["## Last thing I did", "## State now (what exists, with paths)",
            "## What is left", "## Open threads"]


class HandoffFile(Base):
    """T022 [US2] (RQ-1.6, RQ-1.7, RQ-1.8, R7, SC-002, SC-008, FR-009, FR-010, D11).

    HANDOFF.md is written by dispatch_block for the `stop` and `turn` adapters only (T025).
    Written test-first: these fail until write_handoff exists and is wired in."""

    def handoff_path(self, task="TASK-12"):
        return os.path.join(self.root, "agents", "alpha", "me", "runs", task, "HANDOFF.md")

    def read_handoff(self):
        p = self.handoff_path()
        self.assertTrue(os.path.exists(p), f"HANDOFF.md missing; files: {self.all_files()}")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def parse(self, text):
        """(frontmatter dict, {section heading: [non-empty body lines]}, heading order)."""
        lines = text.split("\n")
        self.assertEqual(lines[0], "---", text)
        end = lines.index("---", 1)
        front = {}
        for ln in lines[1:end]:
            k, _, v = ln.partition(":")
            front[k.strip()] = v.strip()
        secs, order, cur = {}, [], None
        for ln in lines[end + 1:]:
            if ln.startswith("## "):
                cur = ln.rstrip()
                order.append(cur)
                secs[cur] = []
            elif cur is not None and ln.strip():
                secs[cur].append(ln.strip())
        return front, secs, order

    def block_ctx(self, adapter):
        c = dict(self.ctx)
        c["adapter"] = adapter
        return c

    def test_handoff_write_failure_still_sends_and_logs_once(self):
        # A HANDOFF.md that cannot be written never blocks mail (T025): put a
        # directory where the file must go, so os.replace fails.
        os.makedirs(self.handoff_path())
        res = self.run_fixture("valid-ask.txt")
        self.assertEqual(res["outcome"], "sent")
        self.assertEqual(len(self.rows()), 1)
        lines = self.outcome_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("handoff_error", lines[0])

    def test_handoff_in_progress_writes_in_progress(self):
        res = self.run_fixture("handoff-in-progress.txt")
        self.assertEqual(res["outcome"], "handoff-pending")
        self.assertEqual(self.rows(), [])
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["status"], "in-progress")

    def test_handoff_done_writes_done(self):
        self.run_fixture("handoff-done.txt")
        self.assertEqual(len(self.rows()), 1)
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["status"], "done")

    def test_ask_in_progress_one_row(self):
        res = self.run_fixture("ask-in-progress.txt")
        self.assertEqual(res["outcome"], "sent")
        self.assertEqual(len(self.rows()), 1)
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["status"], "in-progress")

    def test_frontmatter_fields(self):
        self.run_fixture("ask-in-progress.txt")
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["type"], "Handoff")
        self.assertEqual(front["task"], "TASK-12")
        self.assertEqual(front["agent"], "me")
        self.assertEqual(front["agent_id"], self.me["agent_id"])
        self.assertEqual(front["session"], "alpha")
        self.assertTrue(front["updated"])
        self.assertIn(front["status"], ("done", "in-progress"))

    def test_sections_in_order_with_fixture_content(self):
        self.run_fixture("ask-in-progress.txt")
        text = self.read_handoff()
        _, secs, order = self.parse(text)
        self.assertEqual(order, SECTIONS)
        self.assertIn("Does clause 4 of the lease", "\n".join(secs[SECTIONS[0]]))
        state = "\n".join(secs[SECTIONS[1]])
        for needle in ("docs/lease.md", "Lease draft under review",
                       "notes/clause4.md", "My notes on the subletting clause"):
            self.assertIn(needle, state)
        # one line per artifact, then one line per read pointer (read entries repeat the paths)
        self.assertGreaterEqual(len(secs[SECTIONS[1]]), 4)
        self.assertIn("Clause 7 notice period is not yet checked", "\n".join(secs[SECTIONS[2]]))
        threads = "\n".join(secs[SECTIONS[3]])
        for needle in ("lease-review", "law-counsel", "ask"):
            self.assertIn(needle, threads)

    def test_defaults_without_gap_and_thread(self):
        self.run_fixture("handoff-done.txt")      # no self_check, no thread, no artifacts
        _, secs, order = self.parse(self.read_handoff())
        self.assertEqual(order, SECTIONS)
        self.assertEqual(secs[SECTIONS[2]], ["Nothing stated."])
        self.assertEqual(secs[SECTIONS[3]], ["None."])

    def test_malformed_leaves_existing_handoff_unchanged(self):
        p = self.handoff_path()
        os.makedirs(os.path.dirname(p))
        known = "---\ntype: Handoff\ntask: TASK-12\n---\nknown content\n"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(known)
        for name in ("malformed-json.txt", "malformed-status.txt", "malformed-say-long.txt"):
            self.run_fixture(name)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), known)

    def test_malformed_creates_no_handoff(self):
        for name in ("malformed-json.txt", "malformed-missing-to.txt", "no-block.txt"):
            self.run_fixture(name)
        self.assertFalse(os.path.exists(self.handoff_path()), self.all_files())

    def test_wrong_recipient_rewrites_handoff(self):
        self.run_fixture("wrong-recipient.txt")
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["task"], "TASK-12")

    def test_off_team_rewrites_handoff(self):
        self.run_fixture("off-team.txt")
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["task"], "TASK-12")

    def test_rewrite_replaces_previous_content(self):
        self.run_fixture("handoff-in-progress.txt")
        self.run_fixture("handoff-done.txt")
        text = self.read_handoff()
        front, _, _ = self.parse(text)
        self.assertEqual(front["status"], "done")
        self.assertNotIn("Clauses 1-3 are done", text)       # old body is gone
        self.assertIn("Lease review complete", text)

    def test_outbox_adapter_never_writes_handoff(self):    # D11
        for name in ("valid-ask.txt", "handoff-done.txt", "ask-in-progress.txt"):
            raw = fixture_text(name).split("```rookery\n", 1)[1].rsplit("```", 1)[0]
            TB.dispatch_block(self.conn, raw, self.block_ctx("outbox"))
        self.assertGreaterEqual(len(self.rows()), 1)       # mail outcomes are the same
        self.assertFalse(os.path.exists(self.handoff_path()), self.all_files())
        self.assertEqual(glob.glob(os.path.join(self.root, "**", "HANDOFF.md"), recursive=True), [])

    def test_turn_adapter_writes_handoff(self):
        self.run_fixture("handoff-done.txt", self.block_ctx("turn"))
        front, _, _ = self.parse(self.read_handoff())
        self.assertEqual(front["status"], "done")

    def test_task_id_in_path_and_content_of_every_written_file(self):   # FR-010
        # HANDOFF.md from a valid block, plus a put-back file from a real send failure.
        # NOTE: only VALID-block put-backs are checked. A malformed put-back is stored as
        # {"raw": ...} (no parsed "task" key), so FR-010 cannot apply to it.
        self.run_fixture("ask-in-progress.txt")
        self.conn.execute("CREATE TRIGGER boom BEFORE INSERT ON inbox "
                          "BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.conn.commit()
        hook = StopHookPart2("run_hook")
        hook.tmp, hook.root = self.tmp, self.root
        hook.run_hook(fixture_text("valid-ask.txt"))
        written = [f for f in self.all_files()
                   if os.path.join("agents", "alpha", "me") in f
                   and (os.sep + "runs" + os.sep in f or os.sep + "outbox" + os.sep in f)]
        kinds = {os.path.basename(f) for f in written}
        self.assertIn("HANDOFF.md", kinds, written)
        self.assertTrue(any(f.endswith(".json") for f in written), written)
        for f in written:
            self.assertIn("TASK-12", f)
            if f.endswith(".md"):
                with open(f, encoding="utf-8") as fh:
                    self.assertIn("task: TASK-12", fh.read().split("\n"))
            else:
                self.assertEqual(load_json(f)["task"], "TASK-12")


class WriteHandoffDirect(unittest.TestCase):
    """Wave-8 audit: direct write_handoff calls (body injection, file mode)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **kw):
        blk = {"to": "x", "task": "T-1", "kind": "ask", "status": "in-progress",
               "say": "hi"}
        blk.update(kw)
        p = TB.write_handoff(self.ws, blk, {"name": "me", "agent_id": "ag-1"},
                             {"name": "s"})
        with open(p, encoding="utf-8") as fh:
            return p, fh.read()

    def test_body_cannot_forge_sections_or_frontmatter(self):
        _, text = self.write(say="a\n---\n## What is left\nfake",
                             thread="th\n## x",
                             self_check={"gap": "g\r\n## Open threads\r---\u2028# h"})
        lines = text.split("\n")
        for h in SECTIONS:
            self.assertEqual(lines.count(h), 1, (h, text))
        self.assertEqual(len([ln for ln in lines if ln.strip() == "---"]), 2, text)
        self.assertEqual([ln for ln in lines if ln.startswith("## ")], SECTIONS, text)
        self.assertIn("fake", text)
        self.assertIn("\\## What is left", text)           # escaped, still present

    def test_nul_removed(self):
        _, text = self.write(say="a\x00b")
        self.assertNotIn("\x00", text)
        self.assertIn("ab", text)

    def test_file_mode_follows_umask(self):
        old = os.umask(0o022)
        try:
            p, _ = self.write()
        finally:
            os.umask(old)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o666 & ~0o022)


class TurnCommand(Base):
    """T043 [US5] (RQ-1.16, FR-020, D3, D16): `rookery turn --project p --json` on stdin.

    Every fixture of fixtures/turns/ is run twice in the same temp setup: once through
    dispatch_text() (adapter "turn") and once through the subprocess, with the mail, the
    log and the workspace reset between the two. The outcome, the inbox rows (without id
    and time columns) and every HANDOFF.md (without its `updated:` line) must be equal."""

    CLI = os.path.join(HERE, "rookery_cli.py")
    OUTCOMES = {"sent", "no-block", "quarantined", "handoff-pending", "send-failed",
                "no-session", "remote-unsupported", "db-busy"}

    def fixtures(self):
        names = sorted(f for f in os.listdir(FIXTURES) if f.endswith(".txt"))
        self.assertGreaterEqual(len(names), 25, names)
        return names

    def turn_env(self):
        env = dict(os.environ)
        for k in list(env):
            if k.startswith(("ROOKERY_", "CLAUDE_", "HERDR_", "TMUX")):
                env.pop(k)
        for var in (RI.load_config(persist=False).get("session_binding_sources") or {}):
            env.pop(var, None)
        env.update(HOME=self.tmp.name, ROOKERY_DB=os.environ["ROOKERY_DB"],
                   ROOKERY_ROOT=self.root, ROOKERY_SESSION="alpha",
                   ROOKERY_NODE=self.me["agent_id"])
        return env

    def run_turn(self, text, json_flag=True, timeout=30):
        args = [sys.executable, self.CLI, "turn", "--project", "p"] + (["--json"] if json_flag else [])
        return subprocess.run(args, input=text, capture_output=True, text=True,
                              env=self.turn_env(), cwd=HERE, timeout=timeout)

    def test_f7_invalid_utf8_stdin_is_rejected(self):
        args = [sys.executable, self.CLI, "turn", "--project", "p", "--json"]
        out = subprocess.run(args, input=b"```rookery\n{\"say\": \"caf\xe9\"}\n```\n",
                             capture_output=True, env=self.turn_env(), cwd=HERE, timeout=30)
        self.assertEqual(out.returncode, 2, (out.stdout, out.stderr))
        got = json.loads(out.stdout)
        self.assertEqual(got["outcome"], "bad-input")
        self.assertIn("input is not valid UTF-8", json.dumps(got))
        self.assertEqual(self.rows(), [])

    def reset_all(self):
        import shutil
        self.conn.execute("DELETE FROM inbox")
        self.conn.execute("DELETE FROM screen_mail")   # ids restart at 1; a stale row would collide
        self.conn.execute("DELETE FROM screen_decisions")   # same: a gates record of the first run would collide
        try:   # bounce bodies carry the original row id, so both runs must count from 1
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name='inbox'")
        except sqlite3.Error:
            pass
        self.conn.commit()
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root)

    def norm_rows(self):
        out = []
        for r in self.rows():
            out.append({k: v for k, v in r.items()
                        if k != "id" and not k.endswith(("_at", "_ts")) and k not in ("created", "ts")})
        return out

    def handoffs(self):
        found = {}
        for d, _, fs in os.walk(self.root):
            for f in fs:
                if f == "HANDOFF.md":
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        lines = [ln for ln in fh.read().split("\n") if not ln.startswith("updated:")]
                    found[os.path.relpath(os.path.join(d, f), self.root)] = "\n".join(lines)
        return found

    def snapshot(self):
        return self.norm_rows(), self.handoffs()

    def test_all_fixtures_match_dispatch_text(self):
        for name in self.fixtures():
            with self.subTest(fixture=name):
                self.reset_all()
                text = fixture_text(name)
                direct = dispatch(self.conn, text, dict(self.ctx, adapter="turn"))
                want_rows, want_handoff = self.snapshot()
                self.reset_all()
                out = self.run_turn(text)
                self.assertEqual(out.returncode, 0, (out.stdout, out.stderr))
                got = json.loads(out.stdout)
                self.assertEqual(got["outcome"], direct["outcome"])
                got_rows, got_handoff = self.snapshot()
                self.assertEqual(got_rows, want_rows)
                self.assertEqual(got_handoff, want_handoff)
                ids = [r["id"] for r in self.rows()]
                if direct["msg_id"] is None:
                    self.assertIsNone(got["msg_id"])
                else:
                    self.assertIn(got["msg_id"], ids)
                self.assertEqual([x["outcome"] for x in self.outcome_lines()], [direct["outcome"]])

    def test_json_shape(self):
        for name in ("valid-ask.txt", "no-block.txt", "malformed-json.txt", "handoff-in-progress.txt"):
            with self.subTest(fixture=name):
                self.reset_all()
                out = self.run_turn(fixture_text(name))
                self.assertEqual(out.returncode, 0, (out.stdout, out.stderr))
                got = json.loads(out.stdout)
                self.assertEqual(set(got), {"outcome", "msg_id", "reason"})
                self.assertIn(got["outcome"], self.OUTCOMES)
                self.assertTrue(got["msg_id"] is None or isinstance(got["msg_id"], int))
                self.assertTrue(got["reason"] is None or isinstance(got["reason"], str))
        self.reset_all()
        got = json.loads(self.run_turn(fixture_text("valid-ask.txt")).stdout)
        self.assertEqual((got["outcome"], got["msg_id"]), ("sent", self.rows()[0]["id"]))

    def test_db_busy_exits_4(self):
        import time
        self.reset_all()
        locker = sqlite3.connect(os.environ["ROOKERY_DB"], timeout=1, isolation_level=None)
        try:
            locker.execute("BEGIN IMMEDIATE")
            t0 = time.monotonic()
            out = self.run_turn(fixture_text("valid-ask.txt"), timeout=20)
            elapsed = time.monotonic() - t0
        finally:
            try:
                locker.execute("ROLLBACK")
            finally:
                locker.close()
        self.assertEqual(out.returncode, 4, (out.stdout, out.stderr))
        if out.stdout.strip():
            self.assertEqual(json.loads(out.stdout)["outcome"], "db-busy")
        self.assertLess(elapsed, 3.0)
        self.assertEqual(self.rows(), [])


class LoopBreaker(Base):
    """FR-039: more than loop_limit mails on one task between two agents, with no human prompt
    in between, are held with reason loop."""

    def block(self, to, task="TASK-12", kind="status"):
        return ("```rookery\n" + json.dumps({"to": to, "task": task, "kind": kind,
                "say": "got it", "status": "done"}) + "\n```")

    def send(self, frm, to, task="TASK-12"):
        ctx = dict(self.ctx, identity=frm)
        return dispatch(self.conn, self.block(to["name"], task), ctx)

    def ping_pong(self, n, task="TASK-12"):
        out = []
        for i in range(n):
            a, b = (self.me, self.lc) if i % 2 == 0 else (self.lc, self.me)
            out.append(self.send(a, b, task))
        return out

    def test_ten_sent_eleventh_held(self):
        res = self.ping_pong(11)
        self.assertEqual([r["outcome"] for r in res], ["sent"] * 10 + ["loop-held"])
        row = self.rows()[-1]
        self.assertEqual((row["status"], row["reason"]), ("held", "loop"))
        self.assertTrue(any(x.get("outcome") == "loop-held" for x in self.log_lines()))
        self.assertEqual(len(self.rows()), 11)

    def test_further_mail_stays_held(self):
        res = self.ping_pong(13)
        self.assertEqual([r["outcome"] for r in res][10:], ["loop-held"] * 3)

    def test_self_sends_count(self):
        res = [self.send(self.me, self.me) for _ in range(11)]
        self.assertEqual(res[-1]["outcome"], "loop-held")

    def test_tasks_counted_separately(self):
        self.ping_pong(10, "TASK-1")
        self.assertEqual(self.ping_pong(1, "TASK-2")[0]["outcome"], "sent")
        self.assertEqual(self.ping_pong(1, "TASK-1")[0]["outcome"], "loop-held")

    def test_other_pair_counted_separately(self):
        self.ping_pong(10)
        third = RI.new_identity(self.conn, self.alpha["session_id"], "third")
        self.assertEqual(self.send(self.me, third)["outcome"], "sent")

    def test_reset_lets_mail_through(self):
        self.ping_pong(11)
        n = TB.reset_loop_counts(self.conn, self.me["agent_id"])
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.ping_pong(1)[0]["outcome"], "sent")

    def test_reset_of_an_outsider_changes_nothing(self):
        self.ping_pong(11)
        third = RI.new_identity(self.conn, self.alpha["session_id"], "third")
        TB.reset_loop_counts(self.conn, third["agent_id"])
        self.assertEqual(self.ping_pong(1)[0]["outcome"], "loop-held")

    def test_limit_read_from_config(self):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": 3}, fh)
        res = self.ping_pong(5)
        self.assertEqual([r["outcome"] for r in res], ["sent"] * 3 + ["loop-held"] * 2)

    def test_default_limit_is_ten_in_packaged_config(self):
        import rookery_identity
        self.assertEqual(rookery_identity.load_config(persist=False)["loop_limit"], 10)

    def test_bad_limit_falls_back_to_ten(self):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": "many"}, fh)
        self.assertEqual([r["outcome"] for r in self.ping_pong(11)][-1], "loop-held")

    def test_held_mail_is_not_pending_for_recipient(self):
        self.ping_pong(11)
        self.assertEqual(self.rows()[-1]["status"], "held")
        pending = [r for r in self.rows() if r["status"] == "pending"]
        self.assertEqual(len(pending), 10)

    def test_stop_hook_parks_loop_held_block_and_notice_names_it(self):
        self.ping_pong(10)
        before = len(self.rows())
        res = self.send(self.me, self.lc)
        self.assertEqual(res["outcome"], "loop-held")
        files = self.failed_files()
        self.assertEqual(len(files), 1, files)
        notes = TB.new_failed_blocks(self.root, "alpha", "me")
        self.assertEqual([n["reason"] for n in notes], ["loop-held"])
        self.assertIn("loop-held", TB.format_failed_line(notes[0]))
        self.assertEqual(len(self.rows()), before + 1)          # no bounce mail

    def test_outbox_adapter_puts_nothing_back_for_loop_held(self):
        self.ping_pong(10)
        ctx = dict(self.ctx, adapter="outbox")
        res = dispatch(self.conn, self.block(self.lc["name"]), ctx)
        self.assertEqual(res["outcome"], "loop-held")
        self.assertEqual(self.failed_files(), [])

    def test_rookery_turn_prints_stderr_note_and_json_outcome(self):
        self.ping_pong(10)
        env = TurnCommand.turn_env(self)
        cli = os.path.join(HERE, "rookery_cli.py")
        out = subprocess.run([sys.executable, cli, "turn", "--project", "p"],
                             input=self.block(self.lc["name"]), capture_output=True, text=True,
                             env=env, cwd=HERE, timeout=30)
        self.assertEqual(out.returncode, 0, (out.stdout, out.stderr))   # as send-failed / quarantined
        self.assertIn("loop-held", out.stdout)
        self.assertIn("loop-held", out.stderr)
        self.assertEqual(len(self.failed_files()), 1)

    def test_task_rename_does_not_evade(self):
        res = [self.send(self.me, self.lc, f"T{i}")["outcome"] for i in range(31)]
        self.assertEqual(res, ["sent"] * 30 + ["loop-held"])
        self.assertEqual(self.rows()[-1]["reason"], "loop")

    def test_pair_limit_from_config(self):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"loop_limit": 10, "loop_pair_limit": 4}, fh)
        res = [self.send(self.me, self.lc, f"T{i}")["outcome"] for i in range(6)]
        self.assertEqual(res, ["sent"] * 4 + ["loop-held"] * 2)

    def test_packaged_pair_limit_is_thirty(self):
        import rookery_identity
        self.assertEqual(rookery_identity.load_config(persist=False)["loop_pair_limit"], 30)

    def test_pair_counter_resets_with_human_prompt(self):
        [self.send(self.me, self.lc, f"T{i}") for i in range(30)]
        TB.reset_loop_counts(self.conn, self.me["agent_id"])
        self.assertEqual(self.send(self.me, self.lc, "T99")["outcome"], "sent")

    def test_bounce_loop_is_bounded(self):
        for _ in range(30):
            dispatch(self.conn, "```rookery\n{not json}\n```", self.ctx)
        bounces = [r for r in self.rows() if r["sender"] == "rookery"]
        self.assertEqual(len(bounces), 30)
        self.assertEqual(sum(r["status"] == "pending" for r in bounces), 10)
        held = [r for r in bounces if r["status"] == "held"]
        self.assertEqual((len(held), {r["reason"] for r in held}), (20, {"loop"}))
        orig = [r for r in self.rows() if r["sender"] != "rookery"]
        self.assertTrue(all(r["status"] == "quarantined" for r in orig))

    def test_bounce_counter_resets_with_human_prompt(self):
        for _ in range(11):
            dispatch(self.conn, "```rookery\n{not json}\n```", self.ctx)
        TB.reset_loop_counts(self.conn, self.me["agent_id"])
        dispatch(self.conn, "```rookery\n{not json}\n```", self.ctx)
        self.assertEqual(self.rows()[-1]["status"], "pending")

    def test_reset_skips_update_when_nothing_to_reset(self):
        self.conn.execute("CREATE TRIGGER boom BEFORE UPDATE ON loop_counts "
                          "BEGIN SELECT RAISE(ABORT,'boom'); END")
        self.assertEqual(TB.reset_loop_counts(self.conn, self.me["agent_id"]), 0)


if __name__ == "__main__":
    unittest.main()
