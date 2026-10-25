#!/usr/bin/env python3
"""004 T011: `rookery screen run` and `screen status` (D12, FR-004, FR-005, FR-013a, FR-014, FR-033, SC-001, SC-003, SC-004).

    python3 -m unittest test_screen_cli -v

Every run is a subprocess with a temporary HOME, ROOKERY_DB and ROOKERY_ROOT. The fake backend answers (no model call).
"""
import contextlib
import http.server
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from unittest import mock

import rookery
import rookery_identity as RI
from test_hooks import Base, CLI, HERE

FIXTURE = os.path.join(HERE, "fixtures", "screener", "fake_t1.json")


class Cli(Base):
    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake)
        self.sid = self.alpha["session_id"]

    def screen(self, *args, fake=True, env=None, check=True):
        e = self.env(session=False, node=False, **(env or {}))
        if fake:
            e["ROOKERY_SCREENER_FAKE"] = self.fake
        p = subprocess.run([sys.executable, CLI, "screen", *args], capture_output=True, text=True, env=e,
                           cwd=HERE, timeout=120)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def send(self, text, sender=None, to=None, sid=None, kind="ask"):
        body = json.dumps({"to": "researcher", "task": "T-7", "kind": kind, "say": text, "status": "in-progress"},
                          separators=(",", ":"), sort_keys=True)
        return rookery.send(self.conn, sender or self.swe["agent_id"], to or self.researcher["agent_id"], body,
                            topic="T-7", session_id=sid or self.sid, hold=False)

    def records(self, stage="t1"):
        return self.conn.execute("SELECT * FROM screen_decisions WHERE stage=? ORDER BY id", (stage,)).fetchall()

    def calls(self):
        p = self.fake + ".calls"
        if not os.path.exists(p):
            return 0
        with open(p) as fh:
            return len(fh.read().splitlines())

    def write_config(self, screener):
        d = os.path.join(self.tmp.name, ".rookery")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as fh:
            json.dump({"screener": screener}, fh)

    def beta(self):
        b = RI.new_session(self.conn, name="beta")
        return b, RI.new_identity(self.conn, b["session_id"], "swe"), RI.new_identity(self.conn, b["session_id"], "researcher")


class Run(Cli):
    def test_fifty_mails_one_t1_record_each_and_no_status_change(self):
        ids = []
        for i in range(50):
            kind = ("ask", "answer", "decline", "inform")[i % 4]
            ids.append(self.send("CASE-DELIVER n%d" % i, kind=kind))
        sys_id = rookery.send(self.conn, "rookery", self.researcher["agent_id"], "note", session_id=self.sid, hold=False)
        before = {r["id"]: r["status"] for r in self.conn.execute("SELECT id, status FROM inbox")}
        p = self.screen("run", "--once")
        self.assertTrue(p.stdout.startswith("screener backend=fake"), p.stdout)
        recs = self.records()
        self.assertEqual(sorted(r["mail_id"] for r in recs), sorted(ids))
        self.assertEqual({r["label"] for r in recs}, {"deliver"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_mail WHERE mail_id=?", (sys_id,)).fetchone()[0], 0)
        after = {r["id"]: r["status"] for r in self.conn.execute("SELECT id, status FROM inbox")}
        self.assertEqual(before, after)
        rows = self.conn.execute("SELECT state, owner FROM screen_mail").fetchall()
        self.assertEqual({r["state"] for r in rows}, {"decided"})
        self.screen("run", "--once")   # a second pass screens nothing
        self.assertEqual(len(self.records()), 50)

    def test_quarantined_at_store_gets_no_new_row(self):
        mid = self.send("CASE-DELIVER held")
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, final_label, final_by, decided_at) "
                          "VALUES(?,?,?,?,?,?)", (mid, self.sid, "decided", "injection", "t0:injection", 1))
        self.conn.commit()
        self.screen("run", "--once")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.calls(), 0)

    def test_restart_goes_on_from_first_undecided(self):
        ids = [self.send("CASE-DELIVER n%d" % i) for i in range(6)]
        for mid in ids[:3]:   # an earlier process decided three
            self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, final_label, final_by, decided_at) "
                              "VALUES(?,?,?,?,?,?)", (mid, self.sid, "decided", "deliver", "screener:tev1:4b", 1))
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, owner, claimed_at) VALUES(?,?,?,?,?)",
                          (ids[3], self.sid, "claimed", "dead:1:x", 1))   # a claim that expired long ago
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, owner, claimed_at) VALUES(?,?,?,?,?)",
                          (ids[4], self.sid, "claimed", "live:1:x", rookery.now()))   # a live claim stays
        self.conn.commit()
        self.screen("run", "--once")
        self.assertEqual(sorted(r["mail_id"] for r in self.records()), [ids[3], ids[5]])
        st = {r["mail_id"]: r["state"] for r in self.conn.execute("SELECT mail_id, state FROM screen_mail")}
        self.assertEqual(st[ids[4]], "claimed")
        self.assertEqual(st[ids[3]], "decided")

    def test_two_processes_one_record_per_mail(self):
        ids = [self.send("CASE-DELIVER n%d" % i) for i in range(30)]
        e = self.env(session=False, node=False)
        e["ROOKERY_SCREENER_FAKE"] = self.fake
        ps = [subprocess.Popen([sys.executable, CLI, "screen", "run", "--once"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=e, cwd=HERE) for _ in range(2)]
        for p in ps:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        recs = self.records()
        self.assertEqual(sorted(r["mail_id"] for r in recs), sorted(ids))
        self.assertEqual(self.calls(), 30)

    def test_failure_in_one_session_does_not_stop_another(self):
        b, bswe, bres = self.beta()
        a = self.send("CASE-TIMEOUT in alpha")
        bm = self.send("CASE-DELIVER in beta", sender=bswe["agent_id"], to=bres["agent_id"], sid=b["session_id"])
        self.screen("run", "--once")
        by_mail = {r["mail_id"]: r for r in self.records()}
        self.assertEqual(by_mail[a]["failure"], "timeout")   # a shadow failure is a record, and final
        self.assertEqual(by_mail[bm]["label"], "deliver")
        st = {r["mail_id"]: r["state"] for r in self.conn.execute("SELECT mail_id, state FROM screen_mail")}
        self.assertEqual(st, {a: "failed", bm: "decided"})

    def test_an_error_goes_to_the_screener_log(self):
        import screener
        screener._log("alpha", 7, "ValueError: boom")
        with open(os.path.join(self.tmp.name, ".rookery", "logs", "screener.jsonl")) as fh:
            rec = json.loads(fh.read().splitlines()[-1])
        self.assertEqual((rec["session"], rec["msg_id"], rec["reason"]), ("alpha", 7, "ValueError: boom"))

    def test_session_flag_screens_only_that_session(self):
        b, bswe, bres = self.beta()
        self.send("CASE-DELIVER alpha")
        bm = self.send("CASE-DELIVER beta", sender=bswe["agent_id"], to=bres["agent_id"], sid=b["session_id"])
        self.screen("run", "--once", "--session", "beta")
        self.assertEqual([r["mail_id"] for r in self.records()], [bm])
        p = self.screen("run", "--once", "--session", "nope", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("nope", p.stderr)

    def test_t1_off_writes_no_record_and_no_claim(self):
        self.write_config({"checks": {"t1": "off"}})
        self.send("CASE-DELIVER x")
        self.screen("run", "--once")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_mail").fetchone()[0], 0)
        self.assertEqual(self.calls(), 0)

    def test_bad_configuration_exits_2(self):
        self.write_config({"checks": {"t1": "maybe"}})
        p = self.screen("run", "--once", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("screener.checks.t1", p.stderr)

    def test_owner_only_finishes_its_claim(self):
        import screener
        mid = self.send("CASE-DELIVER x")
        self.assertTrue(screener._claim(self.conn, mid, self.sid, "a:1:x", 0))
        self.assertFalse(screener._claim(self.conn, mid, self.sid, "b:2:y", 0))   # a live claim is not taken
        screener._finish(self.conn, mid, "b:2:y", {"model": "m"}, "deliver", None)
        self.assertEqual(self.conn.execute("SELECT state FROM screen_mail").fetchone()[0], "claimed")
        self.assertTrue(screener._claim(self.conn, mid, self.sid, "b:2:y", rookery.now() + 5))   # an expired one is
        screener._finish(self.conn, mid, "a:1:x", {"model": "m"}, "deliver", None)
        self.assertEqual(self.conn.execute("SELECT state FROM screen_mail").fetchone()[0], "claimed")
        screener._finish(self.conn, mid, "b:2:y", {"model": "m"}, "deliver", None)
        self.assertEqual(self.conn.execute("SELECT state FROM screen_mail").fetchone()[0], "decided")


class Hardening(Cli):
    """Audit fixes: a poison mail, exit codes, fairness, the lease and the age limit."""

    def setUp(self):
        super().setUp()
        import screener
        self.screener = screener
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(mock.patch.stopall)
        os.environ["ROOKERY_SCREENER_FAKE"] = self.fake
        self.addCleanup(os.environ.pop, "ROOKERY_SCREENER_FAKE", None)

    def run_once(self, **kw):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.screener.run(once=True, **kw)
        return code, err.getvalue()

    def states(self):
        return {r["mail_id"]: r["state"] for r in self.conn.execute("SELECT mail_id, state FROM screen_mail")}

    def test_poison_mail_ends_failed_and_the_rest_are_screened(self):
        ids = [self.send("CASE-DELIVER n%d" % i) for i in range(3)]
        real = self.screener.screen_one

        def poison(conn, mail_id, cfg, backend, **kw):
            if mail_id == ids[1]:
                raise RuntimeError("boom")
            return real(conn, mail_id, cfg, backend, **kw)
        with mock.patch.object(self.screener, "screen_one", poison):
            code, _ = self.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(self.states(), {ids[0]: "decided", ids[1]: "failed", ids[2]: "decided"})
        with open(os.path.join(self.tmp.name, ".rookery", "logs", "screener.jsonl")) as fh:
            recs = [json.loads(line) for line in fh]
        hit = [r for r in recs if r.get("msg_id") == ids[1]]
        self.assertTrue(hit and hit[-1]["session"] == "alpha" and "boom" in hit[-1]["reason"], recs)

    def test_expired_claim_with_three_tries_is_failed_not_retaken(self):
        a, b = self.send("CASE-DELIVER a"), self.send("CASE-DELIVER b")
        for mid, tries in ((a, 3), (b, 2)):
            self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, owner, claimed_at, attempts) "
                              "VALUES(?,?,?,?,?,?)", (mid, self.sid, "claimed", "dead:1:x", 1, tries))
        self.conn.commit()
        self.assertFalse(self.screener._claim(self.conn, a, self.sid, "me:1:x", 100))
        self.assertTrue(self.screener._claim(self.conn, b, self.sid, "me:1:x", 100))
        self.assertEqual(self.states(), {a: "failed", b: "claimed"})

    def test_once_returns_2_for_bad_config_and_the_loop_retries(self):
        self.write_config({"checks": {"t1": "maybe"}})
        code, err = self.run_once()
        self.assertEqual(code, 2)
        self.assertIn("screener.checks.t1", err)
        calls = []

        def load():
            calls.append(1)
            raise ValueError("screener.timeout_s: bad")
        sleeps = []

        def sleep(_):
            sleeps.append(1)
            if len(sleeps) >= 3:
                raise KeyboardInterrupt
        with mock.patch.object(self.screener.screener_config, "load", load), \
                mock.patch.object(self.screener.time, "sleep", sleep), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                self.screener.run(once=False, poll=0.1)
        self.assertGreaterEqual(len(calls), 3)

    def test_ctrl_c_exits_130_without_traceback(self):
        import argparse
        import cli_screen
        args = argparse.Namespace(session=None, once=False, poll=2.0)
        with mock.patch.object(self.screener, "run", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli_screen.cmd_run(args)
        self.assertEqual(ctx.exception.code, 130)

    def test_vanished_session_or_backend_error_is_logged_and_once_exits_2(self):
        self.send("CASE-DELIVER x")
        with mock.patch.object(self.screener, "open_sessions", side_effect=ValueError("session 'gone' does not exist")):
            code, _ = self.run_once()
        self.assertEqual(code, 2)
        with mock.patch.object(self.screener.screener_backend, "system_one", side_effect=RuntimeError("no backend")):
            code, _ = self.run_once()
        self.assertEqual(code, 2)
        self.assertEqual(self.states(), {})

    def test_bad_session_stops_before_the_startup_line(self):
        p = self.screen("run", "--once", "--session", "nope", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("screener backend", p.stdout)

    def test_each_pass_takes_five_mails_per_session(self):
        b, bswe, bres = self.beta()
        for i in range(7):
            self.send("CASE-DELIVER a%d" % i)
        self.send("CASE-DELIVER b", sender=bswe["agent_id"], to=bres["agent_id"], sid=b["session_id"])
        order = []
        real = self.screener.screen_one

        def spy(conn, mail_id, cfg, backend, **kw):
            order.append(conn.execute("SELECT session_id FROM inbox WHERE id=?", (mail_id,)).fetchone()[0])
            return real(conn, mail_id, cfg, backend, **kw)
        with mock.patch.object(self.screener, "screen_one", spy):
            self.assertEqual(self.run_once()[0], 0)
        a, bs = self.sid, b["session_id"]
        self.assertEqual(order, [a] * 5 + [bs] + [a] * 2)
        self.assertEqual(set(self.states().values()), {"decided"})

    def test_claim_expires_after_lease_s_alone(self):
        mid = self.send("CASE-DELIVER x")
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, owner, claimed_at) VALUES(?,?,?,?,?)",
                          (mid, self.sid, "claimed", "dead:1:x", rookery.now() - 650))   # past lease_s 600, inside 900
        self.conn.commit()
        self.assertEqual(self.run_once()[0], 0)
        self.assertEqual(self.states(), {mid: "decided"})

    def test_mail_older_than_max_age_is_not_screened(self):
        old, fresh = self.send("CASE-DELIVER old"), self.send("CASE-DELIVER fresh")
        self.conn.execute("UPDATE inbox SET created_at=? WHERE id=?", (rookery.now() - 86401, old))
        self.conn.commit()
        self.assertEqual(self.run_once()[0], 0)
        self.assertEqual(self.states(), {fresh: "decided"})
        self.assertEqual([r["mail_id"] for r in self.records()], [fresh])


class _Lane(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/llm/v1/models"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def log_message(self, *a):
        pass


class Status(Cli):
    def lane(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Lane)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/llm" % srv.server_port

    def test_status_json_keys_and_counts(self):
        self.write_config({"base_url": self.lane()})
        a, b = self.send("CASE-DELIVER 1"), self.send("CASE-TIMEOUT 2")
        self.send("CASE-DELIVER 3")
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, final_label, decided_at) VALUES(?,?,?,?,?)",
                          (a, self.sid, "decided", "deliver", 1))
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, decided_at) VALUES(?,?,?,?)",
                          (b, self.sid, "failed", 1))
        self.conn.commit()
        out = json.loads(self.screen("status", "--json").stdout)
        self.assertEqual(list(out), ["backend", "python", "model", "host", "checks", "sessions"])
        self.assertEqual(out["backend"], "fake")
        self.assertEqual(out["python"], sys.executable)
        self.assertEqual(list(out["host"]), ["url", "answers", "checked_at"])
        self.assertTrue(out["host"]["answers"])   # the local fake lane answers /v1/models
        self.assertEqual(set(out["checks"]), {"injection", "duplicate", "budget", "thread", "gates", "t1", "hunch", "t2"})
        row = [s for s in out["sessions"] if s["session"] == "alpha"][0]
        self.assertEqual({k: row[k] for k in ("backlog", "escalated", "failed", "decided")},
                         {"backlog": 1, "escalated": 0, "failed": 1, "decided": 1})
        self.assertIsInstance(row["oldest_age_s"], int)

    def test_host_that_does_not_answer(self):
        self.write_config({"base_url": "http://127.0.0.1:1/llm"})
        out = json.loads(self.screen("status", "--json").stdout)
        self.assertFalse(out["host"]["answers"])

    def test_text_form_and_unknown_session(self):
        self.write_config({"base_url": "http://127.0.0.1:1/llm"})
        p = self.screen("status")
        self.assertIn("session alpha: backlog=0", p.stdout)
        q = self.screen("status", "--session", "nope", check=False)
        self.assertEqual(q.returncode, 2)
        self.assertIn("nope", q.stderr)


class Imports(Cli):
    def test_hook_and_send_do_not_load_the_screener(self):
        probe = ("import sys, runpy\nsys.argv = ['rookery_cli.py'] + %r\n"
                 "try:\n    runpy.run_path('rookery_cli.py', run_name='__main__')\nexcept SystemExit:\n    pass\n"
                 "print(sorted(m for m in ('screener', 'screener_backend', 'promptchain') if m in sys.modules))")
        for argv in (["hook", "stop", "--project", "p"], ["send", "researcher", "hi"]):
            p = subprocess.run([sys.executable, "-c", probe % argv], input="{}", capture_output=True, text=True,
                               env=self.env(), cwd=HERE, timeout=60)
            self.assertTrue(p.stdout.rstrip().endswith("[]"), (argv, p.stdout, p.stderr))


if __name__ == "__main__":
    import unittest
    unittest.main()
