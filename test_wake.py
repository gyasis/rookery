#!/usr/bin/env python3
"""Rookery postmaster wake step, US4 (T035): RQ-1.15, RQ-1.23, FR-018, FR-028.

    python3 -m unittest test_wake -v

ASSUMED API (research D10, tasks.md T041):
    postmaster.wake_identities(conn, root, config, run=subprocess.run)
      one loop of the wake step. Pushes to an identity in an open session that has pending mail newer
      than <root>/postmaster/pushed/<agent_id>.json, only when <workspace>/state/turn.json reads idle.
      Logs (stream "postmaster") reason "no-wake-binding" or "no-idle-state" when it skips.
    postmaster.wake(...) is the old headless wake; the wake step must never call it for an identity.

The fixture fixtures/bin/herdr is put on PATH. It appends json.dumps(sys.argv[1:]) as one line to the
file named by env HERDR_RECORD. Tests use a temp ROOKERY_DB, HOME and ROOKERY_ROOT, a minimal PATH,
and never run a real herdr or tmux.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unicodedata
import unittest

import rookery as R
import rookery_identity as RI
import turn_block as TB
import postmaster as PM

HERE = os.path.dirname(os.path.abspath(__file__))
FIXBIN = os.path.join(HERE, "fixtures", "bin")
ENV_KEYS = ("ROOKERY_DB", "ROOKERY_ROOT", "HOME", "PATH", "HERDR_RECORD")
BIND = ("herdr-pane", "w1:p2")


def tree(path):
    """{relative path: sha1 of content} for every file under path."""
    out = {}
    for d, _, files in os.walk(path):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, path)] = hashlib.sha1(fh.read()).hexdigest()
    return out


class WakeBase(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        for k in list(os.environ):
            if k.startswith(("ROOKERY_", "HERDR_", "TMUX")):
                os.environ.pop(k)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        self.record = os.path.join(self.tmp.name, "herdr.record")
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = os.path.join(self.tmp.name, "t.db")
        os.environ["ROOKERY_ROOT"] = self.root
        os.environ["HERDR_RECORD"] = self.record
        os.environ["PATH"] = FIXBIN + os.pathsep + "/usr/bin:/bin"
        self.conn = TB.open_db()
        self.config = RI.load_config(persist=False)
        self.sess = RI.new_session(self.conn, name="alpha")
        self.sid = self.sess["session_id"]
        self.me = RI.new_identity(self.conn, self.sid, "me")
        self.lc = RI.new_identity(self.conn, self.sid, "law-counsel")
        RI.bind(self.conn, self.sid, self.lc["agent_id"], *BIND)
        self.set_state("law-counsel", "idle")
        self.set_state("me", "idle")

    def tearDown(self):
        self.conn.close()
        os.environ.clear()
        os.environ.update(self._saved)
        self.tmp.cleanup()

    # helpers
    def set_state(self, name, state, session="alpha"):
        ws = TB.workspace(self.root, session, name)
        os.makedirs(os.path.join(ws, "state"), exist_ok=True)
        TB.write_json_atomic(os.path.join(ws, "state", "turn.json"), {"state": state, "since": R.now()})

    def mail(self, to=None, sender=None, topic="TASK-7"):
        return R.send(self.conn, (sender or self.me)["agent_id"], (to or self.lc)["agent_id"],
                      "please review", topic=topic, session_id=self.sid)

    def loop(self):
        PM.wake_identities(self.conn, self.root, self.config)

    def calls(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def log_lines(self):
        p = os.path.join(self.root, "postmaster", "postmaster.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def has_log(self, word):
        return any(word in map(str, x.values()) for x in self.log_lines())

    def pushed(self, agent):
        return os.path.join(self.root, "postmaster", "pushed", agent["agent_id"] + ".json")


class TestWake(WakeBase):
    def test_idle_with_binding_pushes_each_command(self):
        self.mail()
        self.loop()
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][:4], ["agent", "prompt", "w1:p2", calls[0][3]])
        self.assertTrue(os.path.exists(self.pushed(self.lc)))

    def test_text_names_sender_task_and_inbox(self):
        self.mail()
        self.loop()
        text = self.calls()[0][3]
        self.assertIn("me", text)
        self.assertIn("TASK-7", text)
        self.assertIn("rookery inbox", text)
        self.assertNotIn("{", text)
        self.assertNotIn("please review", text)   # a pointer, never the mail body

    def test_second_loop_without_new_mail_runs_nothing(self):
        self.mail()
        self.loop()
        n = len(self.calls())
        self.loop()
        self.assertEqual(len(self.calls()), n)

    def test_new_mail_after_push_pushes_again(self):
        self.mail()
        self.loop()
        self.mail(topic="TASK-8")
        self.loop()
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("TASK-8", self.calls()[1][3])

    def test_busy_gets_no_push_then_idle_pushes_once(self):
        self.set_state("law-counsel", "busy")
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.set_state("law-counsel", "idle")
        self.loop()
        self.assertEqual(len(self.calls()), 1)
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_no_state_file_no_push_logs_no_idle_state(self):
        os.remove(os.path.join(TB.workspace(self.root, "alpha", "law-counsel"), "state", "turn.json"))
        self.mail()
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertTrue(self.has_log("no-idle-state"), self.log_lines())

    def test_no_wake_binding_no_push_and_wake_not_called(self):
        other = RI.new_identity(self.conn, self.sid, "unbound")
        self.set_state("unbound", "idle")
        self.mail(to=other)
        called = []
        saved = PM.wake
        PM.wake = lambda *a, **k: called.append((a, k))
        try:
            self.loop()
        finally:
            PM.wake = saved
        self.assertEqual(self.calls(), [])
        self.assertEqual(called, [])
        self.assertTrue(self.has_log("no-wake-binding"), self.log_lines())

    def test_ended_session_gets_no_push(self):
        self.mail()
        RI.end_session(self.conn, "alpha")
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_writes_nothing_under_agents(self):
        self.mail()
        other = RI.new_identity(self.conn, self.sid, "unbound")
        self.set_state("unbound", "busy")
        self.mail(to=other)
        before = tree(os.path.join(self.root, "agents"))
        self.loop()
        self.loop()
        self.assertEqual(tree(os.path.join(self.root, "agents")), before)


def _dirty(text):
    return [c for c in text if ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) in ("Cc", "Cf")]


class TestPushGate(WakeBase):
    """FR-038: only mail that needs action wakes the recipient."""

    def mailk(self, kind, to=None, sender=None, topic="TASK-7", body=None):
        to, sender = to or self.lc, sender or self.me
        body = body if body is not None else json.dumps(
            {"to": to["name"], "task": topic, "kind": kind, "say": "x", "status": "done"})
        return R.send(self.conn, sender["agent_id"], to["agent_id"], body, topic=topic,
                      session_id=self.sid)

    def test_status_and_answer_are_not_pushed(self):
        for kind in ("status", "answer"):
            self.mailk(kind)
        self.loop()
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(R.fetch_undelivered(self.conn, self.lc["agent_id"])), 2)  # still delivered

    def test_action_kinds_are_pushed(self):
        for kind in ("ask", "handoff", "recruit", "decline"):
            self.tearDown(); self.setUp()
            self.mailk(kind)
            self.loop()
            self.assertEqual(len(self.calls()), 1, kind)

    def test_bounce_is_pushed(self):
        self.mailk("bounce", sender={"agent_id": "rookery"})
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_self_send_is_not_pushed(self):
        self.mailk("ask", to=self.lc, sender=self.lc)
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_legacy_plain_body_is_pushed(self):
        self.mailk(None, body="plain text, no JSON")
        self.mailk(None, body='{"no_kind": 1}', topic="TASK-8")
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_status_then_ask_pushes_the_ask_once(self):
        self.mailk("status")
        self.loop()
        self.assertEqual(self.calls(), [])
        self.mailk("ask", topic="TASK-9")
        self.loop()
        self.loop()
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("TASK-9", calls[0][3])
        self.assertNotIn("TASK-7", calls[0][3])      # the status mail is not named

    def test_skipped_mail_does_not_log_or_back_off(self):
        self.mailk("status")
        self.loop()
        self.assertFalse(any(x.get("outcome") in ("pushed", "wake-failed") for x in self.log_lines()))

    def test_replay_of_live_loop_sequence_never_pushes(self):
        """T056 2026-10-20: ids 4-15, status/answer chain between two agents, with self-sends."""
        a, b = self.me, self.lc
        chain = [("status", a, b), ("answer", b, a), ("status", a, a), ("answer", b, b),
                 ("status", a, b), ("status", b, a), ("answer", a, b), ("answer", b, a),
                 ("status", a, b), ("answer", b, a), ("status", b, b), ("answer", a, b)]
        for kind, s, r in chain:
            self.mailk(kind, to=r, sender=s)
            self.set_state("me", "idle")
            self.set_state("law-counsel", "idle")
            self.loop()
        RI.bind(self.conn, self.sid, a["agent_id"], "herdr-pane", "w1:p9")
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_answer_to_my_ask_is_pushed_once(self):
        self.mailk("ask", to=self.me, sender=self.lc)         # law-counsel asked me
        self.mailk("answer")                                  # I answer
        self.loop()
        self.assertEqual(len(self.calls()), 1)
        self.mailk("answer")                                  # second answer to the same ask
        self.loop()
        self.assertEqual(len(self.calls()), 1)

    def test_second_answer_in_same_tick_is_not_pushed_twice(self):
        self.mailk("ask", to=self.me, sender=self.lc)
        self.mailk("answer")
        self.mailk("answer")
        self.loop()
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("1 ", str(self.log_lines()[-1].get("count", "1 ")) + " ")

    def test_answer_without_prior_ask_is_not_pushed(self):
        self.mailk("answer")
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_answer_on_another_task_is_not_pushed(self):
        self.mailk("ask", to=self.me, sender=self.lc, topic="TASK-1")
        self.mailk("answer", topic="TASK-2")
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_ask_after_the_answer_does_not_count(self):
        self.mailk("answer")
        self.mailk("ask", to=self.me, sender=self.lc)
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_status_never_pushes_even_after_an_ask(self):
        self.mailk("ask", to=self.me, sender=self.lc)
        self.mailk("status")
        self.loop()
        self.assertEqual(self.calls(), [])

    def test_no_push_ids_are_not_reparsed_every_tick(self):
        self.mailk("status")
        cache = {}
        PM.wake_identities(self.conn, self.root, self.config, nopush=cache)
        self.assertEqual(len(cache.get(self.lc["agent_id"], ())), 1)
        orig = PM._needs_push
        PM._needs_push = lambda *a, **k: self.fail("parsed again")
        try:
            PM.wake_identities(self.conn, self.root, self.config, nopush=cache)
        finally:
            PM._needs_push = orig
        self.conn.execute("UPDATE inbox SET status='done'")
        self.conn.commit()
        PM.wake_identities(self.conn, self.root, self.config, nopush=cache)
        self.assertEqual(len(cache.get(self.lc["agent_id"], ())), 0)   # bounded


class TestWaveFourteen(WakeBase):
    def test_control_chars_never_reach_the_pane(self):
        self.mail(topic="X\x04\x03\x1b[2J\x15rm -rf ~\x1b\x1bOA")
        self.mail(topic="a\nb")
        self.mail(sender={"agent_id": "evil\nsudo\x03 reboot"}, topic="T")
        self.loop()
        text = self.calls()[0][3]
        self.assertEqual(_dirty(text), [], repr(text))
        self.assertIn("rookery inbox", text)

    def test_shell_syntax_never_reaches_the_pane(self):
        # The text is typed into a pane that may be a shell: no $, `, ;, |, &, <, >, (, ), quotes.
        self.mail(topic="$(rm -rf ~)`id`;x|y&z<a>b'c\"d")
        self.mail(sender={"agent_id": "$(reboot)"}, topic="T")
        self.loop()
        text = self.calls()[0][3]
        self.assertNotRegex(text, r"[$`;|&<>()'\"\\]", repr(text))
        self.assertTrue(text.endswith("rookery inbox"))

    def test_huge_backlog_still_pushes_short_text(self):
        for i in range(3000):
            R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "b",
                   topic="TOPIC-%d" % i, session_id=self.sid, commit=False)
        self.conn.commit()
        self.loop()
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(len(calls[0][3]), 400)
        self.assertIn("more", calls[0][3])
        self.assertTrue(calls[0][3].endswith("rookery inbox"))

    def _failing_run(self, log):
        def run(argv, **kw):
            log.append(argv)
            return subprocess.CompletedProcess(argv, 1 if argv[2] == "prompt" else 0)
        return run

    def test_partial_failure_backs_off_and_logs_once(self):
        self.mail()
        sent, backoff, t = [], {}, [1000.0]
        run = self._failing_run(sent)
        for _ in range(3):
            PM.wake_identities(self.conn, self.root, self.config, run=run, backoff=backoff,
                               now=lambda: t[0])
        self.assertEqual([a for a in sent if a[2] == "prompt"].__len__(), 1, sent)
        self.assertEqual(sum(1 for x in self.log_lines() if x.get("outcome") == "wake-failed"), 1)
        t[0] += 31
        PM.wake_identities(self.conn, self.root, self.config, run=run, backoff=backoff,
                           now=lambda: t[0])
        self.assertEqual([a for a in sent if a[2] == "prompt"].__len__(), 2)
        self.assertEqual(sum(1 for x in self.log_lines() if x.get("outcome") == "wake-failed"), 1)
        t[0] += 40          # second wait is 60 s: still backing off
        PM.wake_identities(self.conn, self.root, self.config, run=run, backoff=backoff,
                           now=lambda: t[0])
        self.assertEqual([a for a in sent if a[2] == "prompt"].__len__(), 2)

    def test_success_clears_backoff(self):
        self.mail()
        sent, backoff, t = [], {}, [1000.0]
        PM.wake_identities(self.conn, self.root, self.config, run=self._failing_run(sent),
                           backoff=backoff, now=lambda: t[0])
        self.assertTrue(backoff)
        t[0] += 31
        PM.wake_identities(self.conn, self.root, self.config, backoff=backoff, now=lambda: t[0])
        self.assertFalse(backoff)
        self.assertTrue(os.path.exists(self.pushed(self.lc)))

    def test_released_older_mail_is_pushed(self):
        old = R.send(self.conn, self.me["agent_id"], self.lc["agent_id"], "b", topic="HELD-1",
                     hold=True, session_id=self.sid)
        self.mail(topic="TASK-3")
        self.loop()
        self.assertEqual(len(self.calls()), 1)
        self.assertNotIn("HELD-1", self.calls()[0][3])
        self.conn.execute("UPDATE inbox SET status='pending' WHERE id=?", (old,))
        self.conn.commit()
        self.loop()
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("HELD-1", self.calls()[1][3])
        self.loop()
        self.assertEqual(len(self.calls()), 2)

    def test_old_format_pushed_file_is_honoured(self):
        first = self.mail()
        os.makedirs(os.path.dirname(self.pushed(self.lc)))
        with open(self.pushed(self.lc), "w") as fh:
            json.dump({"last_id": first}, fh)
        self.loop()
        self.assertEqual(self.calls(), [])
        self.mail(topic="TASK-9")
        self.loop()
        self.assertEqual(len(self.calls()), 1)


class TestNoRosterProcess(WakeBase):
    """T058: `postmaster.py --poll 0.2` with no --nodes runs the wake step (D10, FR-018)."""

    def _env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ROOKERY_", "HERDR_", "TMUX"))}
        env.update(PATH=FIXBIN + os.pathsep + "/usr/bin:/bin", HOME=self.tmp.name,
                   ROOKERY_DB=os.environ["ROOKERY_DB"], ROOKERY_ROOT=self.root,
                   HERDR_RECORD=self.record)
        return env

    def _start(self, env, *args):
        return subprocess.Popen(["python3", os.path.join(HERE, "postmaster.py"), *args],
                                cwd=self.tmp.name, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)

    def _stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stderr:
            proc.stderr.close()

    def test_no_nodes_pushes_once_to_idle_identity(self):
        self.mail()
        proc = self._start(self._env(), "--poll", "0.2")
        try:
            import time
            end = time.time() + 5
            while time.time() < end and len(self.calls()) < 1:
                time.sleep(0.05)
            self.assertEqual(len(self.calls()), 1, self.calls())
            time.sleep(0.6)
            self.assertEqual(len(self.calls()), 1, "second push without new mail")
            self.assertIsNone(proc.poll(), "postmaster exited")
        finally:
            self._stop(proc)

    def test_no_nodes_no_root_exits_2(self):
        env = self._env()
        del env["ROOKERY_ROOT"]
        bad = os.path.join(self.tmp.name, "bad name")      # unsafe folder name, no --root/--project
        os.makedirs(bad)
        proc = subprocess.Popen(["python3", os.path.join(HERE, "postmaster.py"), "--poll", "0.2"],
                                cwd=bad, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        try:
            err = proc.communicate(timeout=5)[1]
            self.assertEqual(proc.returncode, 2, err)
            self.assertIn("no --nodes", err)
        finally:
            self._stop(proc)


class TestOutboxRoots(unittest.TestCase):
    def test_roots_deduplicated(self):
        saved = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as home:
                os.environ.clear()
                os.environ.update(saved)
                for k in [k for k in os.environ if k.startswith("ROOKERY_")]:
                    os.environ.pop(k)
                os.environ["HOME"] = home
                base = os.path.join(home, ".rookery", "projects")
                has = os.path.join(base, "has")
                os.makedirs(os.path.join(has, "agents"))
                os.makedirs(os.path.join(base, "without"))
                os.symlink(has, os.path.join(base, "link"))
                extra = os.path.join(home, "extra")
                os.makedirs(extra)
                env_root = os.path.join(home, "envroot")
                os.makedirs(env_root)
                os.environ["ROOKERY_ROOT"] = env_root
                self.assertEqual(PM.outbox_roots(extra), [extra, env_root, has])
                os.environ["ROOKERY_ROOT"] = has + "/"      # same project via env: not repeated
                self.assertEqual(PM.outbox_roots(has), [has])
                self.assertEqual(PM.outbox_roots(None), [has + "/"])
        finally:
            os.environ.clear()
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
