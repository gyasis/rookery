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
import statistics
import tempfile
import time
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
            "SELECT i.agent_id, i.name FROM bindings b JOIN identities i ON i.agent_id=b.owner_id "
            "WHERE b.owner_type='identity' AND b.kind='herdr-pane' AND b.value='pane-77'").fetchone()
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

    def test_idle_write_failure_still_dispatches_one_outcome(self):
        # A failed idle write never blocks the stop and adds no second outcome line.
        os.makedirs(self.ws(), exist_ok=True)
        with open(os.path.join(self.ws(), "state"), "w") as fh:
            fh.write("not a directory")
        before = len(self.log_lines())
        out = self.hook("stop", {"last_assistant_message": "Done, no block here."})
        self.assertEqual(out, "")
        new = self.log_lines()[before:]
        self.assertEqual([x.get("outcome") for x in new if "outcome" in x], ["no-block"], new)
        self.assertTrue(any(x.get("event") == "idle-write-failed" for x in new), new)

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
        # resolve audit events (e.g. `bound`) are separate; exactly one hook-error line
        self.assertEqual(len([x for x in self.log_lines()[before:]
                              if x.get("outcome") == "hook-error"]), 1)


class InboxTests(Base):
    """T034 (FR-031): `rookery inbox` / `rookery peek` as subprocesses."""

    def setUp(self):
        super().setUp()
        self.other = RI.new_identity(self.conn, self.alpha["session_id"], "other")
        self.beta = RI.new_session(self.conn, name="beta")
        self.bme = RI.new_identity(self.conn, self.beta["session_id"], "bme")
        self.m_me = self.mail(self.researcher, self.me, task="TASK-1")
        self.m_me2 = self.mail(self.swe, self.me, task="TASK-2")
        self.m_other = self.mail(self.researcher, self.other, task="TASK-3")
        self.m_beta = R.send(self.conn, self.researcher["agent_id"], self.bme["agent_id"],
                             json.dumps({"say": "BETA-BODY"}), topic="TASK-4", hold=False,
                             session_id=self.beta["session_id"])

    def cli(self, *argv, env=None):
        return subprocess.run([sys.executable, CLI, *argv], capture_output=True, text=True,
                              env=env if env is not None else self.env(), cwd=HERE, timeout=30)

    def statuses(self):
        c = sqlite3.connect(self.db)
        try:
            return {r[0]: (r[1], r[2]) for r in
                    c.execute("SELECT id, status, delivered FROM inbox")}
        finally:
            c.close()

    def test_inbox_lists_only_my_mail_and_marks_it_read(self):
        out = self.cli("inbox", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        obj = json.loads(out.stdout)
        self.assertEqual(obj["agent_id"], self.me["agent_id"])
        self.assertEqual(sorted(m["id"] for m in obj["messages"]),
                         sorted([self.m_me, self.m_me2]))
        st = self.statuses()
        for mid in (self.m_me, self.m_me2):
            self.assertEqual(st[mid], ("done", 1))
        self.assertEqual(st[self.m_other][0], "pending")
        self.assertEqual(st[self.m_beta][0], "pending")
        again = json.loads(self.cli("inbox", "--json").stdout)
        self.assertEqual(again["count"], 0)

    def test_peek_lists_same_mail_and_changes_nothing(self):
        before = self.snapshot()
        out = self.cli("peek", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        obj = json.loads(out.stdout)
        self.assertEqual(sorted(m["id"] for m in obj["messages"]),
                         sorted([self.m_me, self.m_me2]))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(self.cli("peek", "--json").stdout)["count"], 2)

    def test_peek_matches_inbox_listing(self):
        peek = json.loads(self.cli("peek", "--json").stdout)
        inbox = json.loads(self.cli("inbox", "--json").stdout)
        self.assertEqual(peek, inbox)

    def test_neither_accepts_as_or_all(self):
        before = self.snapshot()
        for cmd in ("inbox", "peek"):
            for flag in (("--as", "other"), ("--all",)):
                out = self.cli(cmd, *flag)
                self.assertEqual(out.returncode, 2, (cmd, flag, out.stderr))
                self.assertIn("unrecognized arguments", out.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_each_call_logs_inbox_read_with_agent_and_bindings(self):
        for cmd, peek in (("peek", True), ("inbox", False)):
            n = len(self.log_lines())
            self.assertEqual(self.cli(cmd, "--json").returncode, 0)
            new = [x for x in self.log_lines()[n:] if x.get("event") == "inbox-read"]
            self.assertEqual(len(new), 1, cmd)
            self.assertEqual(new[0]["agent_id"], self.me["agent_id"])
            self.assertIn("bindings", new[0])
            self.assertIs(new[0]["peek"], peek)

    def test_without_session_both_exit_3_with_contract_message(self):
        before = self.snapshot()
        for cmd in ("inbox", "peek"):
            out = self.cli(cmd, env=self.env(session=False, node=False))
            self.assertEqual(out.returncode, 3, out.stderr)
            self.assertIn("No current Rookery session. Set ROOKERY_SESSION or start in a pane "
                          "with a session binding.", out.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()


class SessionStartHardening(Base):
    """Wave-15 audit fixes for the SessionStart hook and the postmaster's SIGTERM exit."""

    def start(self, source="startup", env=None, **extra):
        return self.hook("session-start", dict({"source": source, "session_id": "c1"}, **extra),
                         env=env)

    def env_lines(self, ef):
        with open(ef) as fh:
            return fh.read().splitlines()

    # 1. CLAUDE_ENV_FILE: newline-safe and idempotent
    def test_env_file_without_trailing_newline_keeps_previous_line(self):
        ef = os.path.join(self.tmp.name, "env.sh")
        with open(ef, "w") as fh:
            fh.write("export FOO=1")
        self.start(env=self.env(CLAUDE_ENV_FILE=ef))
        lines = self.env_lines(ef)
        self.assertEqual(lines[0], "export FOO=1")
        self.assertEqual(lines[1:], [f"export ROOKERY_SESSION={self.alpha['session_id']}",
                                     f"export ROOKERY_NODE={self.me['agent_id']}"])

    def test_env_file_not_regrown_by_repeated_starts(self):
        ef = os.path.join(self.tmp.name, "env.sh")
        e = self.env(CLAUDE_ENV_FILE=ef)
        self.start("startup", env=e)
        self.start("compact", env=e)
        self.start("resume", env=e)
        self.assertEqual(len(self.env_lines(ef)), 2, self.env_lines(ef))

    def test_env_file_new_identity_appends_new_pair(self):
        ef = os.path.join(self.tmp.name, "env.sh")
        self.start(env=self.env(CLAUDE_ENV_FILE=ef))
        self.start(env=self.env(CLAUDE_ENV_FILE=ef, ROOKERY_NODE=self.swe["agent_id"]))
        lines = self.env_lines(ef)
        self.assertEqual(len(lines), 4, lines)
        self.assertEqual(lines[-1], f"export ROOKERY_NODE={self.swe['agent_id']}")
        self.assertEqual(lines[-2], f"export ROOKERY_SESSION={self.alpha['session_id']}")

    # 2. audit trail survives a later failure
    def test_failure_after_resolve_still_logs_created_event(self):
        ed = os.path.join(self.tmp.name, "envdir")
        os.makedirs(ed)
        out = self.start(env=self.env(CLAUDE_ENV_FILE=ed, ROOKERY_NODE="newguy"))
        self.assertEqual(out, "")
        row = self.conn.execute("SELECT agent_id FROM identities WHERE name='newguy'").fetchone()
        self.assertIsNotNone(row)
        lines = self.log_lines()
        created = [x for x in lines if x.get("event") == "created" and x.get("name") == "newguy"]
        self.assertEqual(len(created), 1, lines)
        errs = [x for x in lines if x.get("outcome") == "hook-error"]
        self.assertEqual(len(errs), 1, lines)
        self.assertEqual(errs[0].get("agent_id"), row["agent_id"])

    # 3. ended session
    def test_ended_session_creates_nothing(self):
        beta = RI.new_session(self.conn, name="beta")
        RI.end_session(self.conn, "beta")
        ef = os.path.join(self.tmp.name, "env.sh")
        before = self.snapshot()
        out = self.start(env=self.env(CLAUDE_ENV_FILE=ef, ROOKERY_SESSION="beta",
                                      ROOKERY_NODE="ghost"))
        self.assertEqual(out, "")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM identities WHERE session_id=?",
            (beta["session_id"],)).fetchone()[0], 0)
        self.assertFalse(os.path.exists(ef))
        self.assertFalse(os.path.exists(os.path.join(self.root, "agents", "beta")))
        ended = [x for x in self.log_lines() if x.get("outcome") == "session-ended"]
        self.assertEqual(len(ended), 1, self.log_lines())
        self.assertEqual(ended[0].get("session"), beta["session_id"])
        self.assertEqual([x for x in self.log_lines() if x.get("event") == "created"
                          and x.get("session") == beta["session_id"]], [])

    # 4. HANDOFF sanitising
    def test_handoff_control_chars_stripped(self):
        self.handoff("ok-line\x1b[2J\x1b]0;pwn\x07‮evil\r[rookery] x\r\nnext\tcol")
        out = self.start()
        for bad in ("\x1b", "\x07", "‮", "\r"):
            self.assertNotIn(bad, out)
        self.assertIn("next\tcol", out)
        self.assertIn("\n[rookery] x\nnext", out)

    def test_handoff_multibyte_cap_no_replacement_char(self):
        self.handoff("é" * 20000)          # 40 KB
        out = self.start()
        self.assertNotIn("�", out)
        self.assertIn("truncated", out)
        self.assertLessEqual(len(out.encode("utf-8")), 16 * 1024 + 1024)

    # 5. SIGTERM clean exit
    def test_postmaster_sigterm_exits_cleanly(self):
        import signal
        import time
        env = self.env(PATH=os.path.join(HERE, "fixtures", "bin") + os.pathsep + "/usr/bin:/bin")
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "postmaster.py"),
                              "--poll", "0.2"], env=env, cwd=HERE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.7)
            self.assertIsNone(p.poll(), "postmaster died before SIGTERM")
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=3)
            self.assertEqual(p.returncode, 0, (out, err))
            self.assertIn("offline", out + err)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


class HookTiming(Base):
    """T050: with 10,000 inbox rows each hook finishes in under 1 s (plan performance goal).

    Each hook runs 3 times end-to-end (subprocess wall clock). The MEDIAN must be < 1.0 s:
    it guards against one-off scheduler noise while still enforcing the goal.
    """
    GOAL = 1.0
    RUNS = 3

    def seed(self):
        t = R.now()
        sid = self.alpha["session_id"]
        me, others = self.me["agent_id"], [self.researcher["agent_id"], self.swe["agent_id"]]
        rows = []
        for i in range(9000):       # done, mostly to other recipients, some to me
            rows.append((others[i % 2], me if i % 10 == 0 else others[(i + 1) % 2],
                         json.dumps({"kind": "ask", "task": f"TASK-{i % 40}", "say": "x"}),
                         f"TASK-{i % 40}", "done", 1, t - i, sid))
        for i in range(500):        # pending for me, several senders and tasks
            rows.append((others[i % 2], me, json.dumps({"kind": "ask", "task": f"TASK-{i % 7}", "say": "y"}),
                         f"TASK-{i % 7}", "pending", 0, t - i, sid))
        for i in range(300):        # held
            rows.append((others[i % 2], me, "{}", "TASK-H", "held", 0, t - i, sid))
        for i in range(200):        # quarantined
            rows.append((others[i % 2], me, "{}", "TASK-Q", "quarantined", 0, t - i, sid))
        self.conn.executemany(
            "INSERT INTO inbox(sender, recipient, body, topic, status, delivered, created_at, session_id) "
            "VALUES(?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        n = self.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
        self.assertEqual(n, 10000)

    def median_time(self, name, payload):
        times = []
        for _ in range(self.RUNS):
            t0 = time.monotonic()
            self.hook(name, payload)
            times.append(time.monotonic() - t0)
        return statistics.median(times), times

    def test_each_hook_under_one_second_with_10000_rows(self):
        self.seed()
        with open(os.path.join(HERE, "fixtures", "turns", "valid-ask.txt")) as fh:
            block = fh.read()
        cases = (("session-start", {"source": "startup", "session_id": "c1"}),
                 ("prompt", {"session_id": "c1", "prompt": "hello"}),
                 ("stop", {"session_id": "c1", "last_assistant_message": block}))
        results = {}
        for name, payload in cases:
            results[name] = self.median_time(name, payload)
        report = "; ".join(f"{n}: median {m:.3f}s runs {[round(x, 3) for x in ts]}"
                           for n, (m, ts) in results.items())
        print("\nHookTiming (10,000 rows): " + report)
        for name, (m, _ts) in results.items():
            self.assertLess(m, self.GOAL, f"{name} too slow. {report}")
