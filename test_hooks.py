#!/usr/bin/env python3
"""Rookery hooks, US4 (T033): session-start and prompt hooks, plus the Stop idle write.

    python3 -m unittest test_hooks -v

Each hook runs as a subprocess: `python3 rookery_cli.py hook <name> --project p`, JSON on stdin.
Contract: specs/001-rookery-init-turn-block/contracts/cli-and-hooks.md; research D7, D8, D9, D16, D20, D23.
Every ROOKERY_* and CLAUDE_* variable is scrubbed from the child env; each case sets only what it needs.
Tests use a temp ROOKERY_DB, HOME and ROOKERY_ROOT and never touch the real database.
Every hook call must exit 0 with empty stderr (D16).
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import rookery as R
import rookery_identity as RI
import turn_block as TB

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("ROOKERY_DB", "ROOKERY_ROOT", "HOME")}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        self.db = os.path.join(self.tmp.name, "t.db")
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = self.db
        os.environ["ROOKERY_ROOT"] = self.root
        self.conn = TB.open_db()
        self.alpha = RI.new_session(self.conn, name="alpha")
        self.me = RI.new_identity(self.conn, self.alpha["session_id"], "me")
        self.researcher = RI.new_identity(self.conn, self.alpha["session_id"], "researcher")
        self.swe = RI.new_identity(self.conn, self.alpha["session_id"], "swe")

    def tearDown(self):
        self.conn.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # helpers
    def env(self, session=True, node=True, **extra):
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("ROOKERY_") or k.startswith("CLAUDE_"))}
        env.update(HOME=self.tmp.name, ROOKERY_DB=self.db, ROOKERY_ROOT=self.root)
        env.pop("HERDR_PANE_ID", None)
        env.pop("HERDR_WORKSPACE_ID", None)
        env.pop("TMUX_PANE", None)
        if session:
            env["ROOKERY_SESSION"] = "alpha"
        if node:
            env["ROOKERY_NODE"] = self.me["agent_id"]
        env.update(extra)
        return env

    def hook(self, name, payload=None, env=None):
        out = subprocess.run(
            [sys.executable, CLI, "hook", name, "--project", "p"],
            input=json.dumps(payload if payload is not None else {}),
            capture_output=True, text=True, env=env if env is not None else self.env(),
            cwd=HERE, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "")
        return out.stdout

    def ws(self, name="me"):
        return os.path.join(self.root, "agents", "alpha", name)

    def turn_file(self, name="me"):
        return os.path.join(self.ws(name), "state", "turn.json")

    def turn_state(self, name="me"):
        with open(self.turn_file(name)) as fh:
            return json.load(fh)["state"]

    def snapshot(self):
        c = sqlite3.connect(self.db)
        try:
            return {t: c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                    for t in ("nodes", "identities", "inbox")}
        finally:
            c.close()

    def mail(self, sender, recipient=None, task="TASK-12", status=None):
        recipient = recipient or self.me
        body = json.dumps({"to": recipient["name"], "kind": "ask", "task": task,
                           "say": "SECRET-BODY-TEXT"})
        mid = R.send(self.conn, sender["agent_id"], recipient["agent_id"], body,
                     topic=task, hold=False, session_id=self.alpha["session_id"])
        if status:
            self.conn.execute("UPDATE inbox SET status=? WHERE id=?", (status, mid))
            self.conn.commit()
        return mid

    def handoff(self, text="HANDOFF-MARKER: pick up the schema work."):
        d = os.path.join(self.ws(), "runs", "TASK-9")
        os.makedirs(d)
        with open(os.path.join(d, "HANDOFF.md"), "w") as fh:
            fh.write(text + "\n")

    def failed_block(self, task="TASK-12", fname=None):
        d = os.path.join(self.root, "postmaster", "outbox", "alpha", "me", "failed")
        os.makedirs(d, exist_ok=True)
        fname = fname or f"{task}-block.json"
        with open(os.path.join(d, fname + ".result.json"), "w") as fh:
            json.dump({"task": task, "outcome": "send-failed", "msg_id": None,
                       "reason": "db down"}, fh)
        return os.path.join(d, fname)

    def log_lines(self):
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]


class NoSessionTests(Base):
    def test_no_session_prints_nothing_and_changes_no_table(self):
        before = self.snapshot()
        env = self.env(session=False, node=False)
        for name in ("session-start", "prompt", "stop"):
            out = self.hook(name, {"source": "startup", "session_id": "c1",
                                   "last_assistant_message": "x"}, env=env)
            self.assertEqual(out, "", name)
        self.assertEqual(self.snapshot(), before)

    def test_no_session_writes_no_turn_json(self):
        env = self.env(session=False, node=False)
        for name in ("session-start", "prompt", "stop"):
            self.hook(name, {"source": "startup", "session_id": "c1",
                             "last_assistant_message": "x"}, env=env)
        found = [os.path.join(d, f) for d, _, fs in os.walk(self.root)
                 for f in fs if f == "turn.json"]
        self.assertEqual(found, [])


class SessionStartTests(Base):
    def start(self, source="startup", env=None, **extra):
        return self.hook("session-start", dict({"source": source, "session_id": "c1"}, **extra),
                         env=env)

    def test_identity_handoff_unread_in_order(self):
        self.handoff()
        self.mail(self.researcher)
        self.mail(self.swe)
        out = self.start()
        ident = (f"[rookery] You are me ({self.me['agent_id']}) in Rookery session "
                 f"alpha.")
        self.assertTrue(out.startswith(ident + "\n"), out)
        i_h = out.index("HANDOFF-MARKER")
        i_u = out.index("unread")
        self.assertTrue(len(ident) < i_h < i_u, out)
        self.assertEqual(out.splitlines()[-1].count("unread"), 1, out)
        self.assertNotIn("SECRET-BODY-TEXT", out)

    def test_registers_node_as_persistent_pane(self):
        self.start()
        c = sqlite3.connect(self.db)
        try:
            row = c.execute("SELECT kind, lifecycle FROM nodes WHERE node_id=?",
                            (self.me["agent_id"],)).fetchone()
        finally:
            c.close()
        self.assertEqual(row, ("pane", "persistent"))

    def test_appends_two_export_lines_to_claude_env_file(self):
        ef = os.path.join(self.tmp.name, "claude-env.sh")
        with open(ef, "w") as fh:
            fh.write("export PRE=1\n")
        self.start(env=self.env(CLAUDE_ENV_FILE=ef))
        with open(ef) as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "export PRE=1")
        self.assertEqual(lines[1:], [f"export ROOKERY_SESSION={self.alpha['session_id']}",
                                     f"export ROOKERY_NODE={self.me['agent_id']}"])

    def test_no_env_file_is_fine(self):
        out = self.start()
        self.assertIn("[rookery] You are me", out)

    def test_compact_prints_same_text(self):
        self.handoff()
        self.mail(self.researcher)
        self.assertEqual(self.start("startup"), self.start("compact"))

    def test_writes_idle(self):
        self.start()
        self.assertEqual(self.turn_state(), "idle")

    def test_creates_identity_from_pane_binding(self):
        """D20: session via ROOKERY_SESSION, no ROOKERY_NODE; the pane binding makes an identity."""
        env = self.env(node=False, HERDR_PANE_ID="pane-77")
        out = self.start(env=env)
        self.assertTrue(out.startswith("[rookery] You are "), out)
        row = self.conn.execute(
            "SELECT i.agent_id, i.name FROM bindings b JOIN identities i ON i.agent_id=b.agent_id "
            "WHERE b.kind='herdr-pane' AND b.value='pane-77'").fetchone()
        self.assertIsNotNone(row)
        self.assertIn(f"({row['agent_id']})", out)
        # the same pane gets the same identity on the next start (compact)
        self.assertEqual(self.start("compact", env=env), out)

    def test_failed_block_line_shown_once(self):
        self.failed_block()
        out = self.start()
        self.assertIn("TASK-12", out)
        self.assertIn("send-failed", out)
        self.assertEqual(self.start().count("send-failed"), 0)


class PromptTests(Base):
    def prompt(self, env=None):
        return self.hook("prompt", {"prompt": "hello", "session_id": "c1"}, env=env)

    def test_zero_unread_prints_nothing(self):
        self.assertEqual(self.prompt(), "")

    def test_two_unread_one_line_with_count_sender_task(self):
        self.mail(self.researcher)
        self.mail(self.researcher)
        before = self.snapshot()["inbox"]
        out = self.prompt()
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        for needle in ("[rookery]", "2", "researcher", "TASK-12", "rookery inbox"):
            self.assertIn(needle, out)
        self.assertNotIn("SECRET-BODY-TEXT", out)
        self.assertEqual(self.snapshot()["inbox"], before)      # no status changes
        self.assertEqual(self.prompt(), out)                    # still unread, shown again

    def test_held_and_quarantined_are_not_unread(self):
        self.mail(self.researcher, status="held")
        self.mail(self.researcher, status="quarantined")
        self.assertEqual(self.prompt(), "")

    def test_failed_block_notice_once(self):
        path = self.failed_block()
        out = self.prompt()
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1, out)
        for needle in ("[rookery]", "TASK-12", "send-failed"):
            self.assertIn(needle, lines[0])
        self.assertIn(os.path.dirname(path), lines[0])
        self.assertEqual(self.prompt(), "")                     # FR-037, D23
        seen = os.path.join(self.ws(), "state", "failed-seen.json")
        self.assertTrue(os.path.exists(seen))

    def test_second_failed_block_is_new(self):
        self.failed_block("TASK-12")
        self.prompt()
        self.failed_block("TASK-13")
        out = self.prompt()
        self.assertIn("TASK-13", out)
        self.assertNotIn("TASK-12", out)

    def test_writes_busy(self):
        self.prompt()
        self.assertEqual(self.turn_state(), "busy")


class StopTests(Base):
    def test_stop_with_unread_prints_nothing_and_writes_idle(self):
        self.mail(self.researcher)
        before = self.snapshot()["inbox"]
        out = self.hook("stop", {"last_assistant_message": "Done, no block here."})
        self.assertEqual(out, "")                               # FR-017: never blocks
        self.assertEqual(self.turn_state(), "idle")
        self.assertEqual(self.snapshot()["inbox"], before)

    def test_prompt_then_stop_flips_state(self):
        self.hook("prompt", {"prompt": "go"})
        self.assertEqual(self.turn_state(), "busy")
        self.hook("stop", {"last_assistant_message": "ok"})
        self.assertEqual(self.turn_state(), "idle")


class HookErrorTests(Base):
    def test_exception_in_hook_exits_zero_with_one_log_line(self):
        """Method: put a regular FILE where the identity's state/ directory belongs, so the
        turn.json write fails with NotADirectoryError/FileExistsError inside the hook."""
        os.makedirs(self.ws(), exist_ok=True)
        with open(os.path.join(self.ws(), "state"), "w") as fh:
            fh.write("not a directory")
        before = len(self.log_lines())
        out = self.hook("prompt", {"prompt": "hello", "session_id": "c1"})
        self.assertEqual(out, "")
        new = self.log_lines()[before:]
        self.assertEqual(len(new), 1, new)
        self.assertIn("error", json.dumps(new[0]).lower())

    def test_session_start_exception_exits_zero(self):
        os.makedirs(self.ws(), exist_ok=True)
        with open(os.path.join(self.ws(), "state"), "w") as fh:
            fh.write("not a directory")
        before = len(self.log_lines())
        self.hook("session-start", {"source": "startup", "session_id": "c1"})
        self.assertEqual(len(self.log_lines()[before:]), 1)


if __name__ == "__main__":
    unittest.main()
