#!/usr/bin/env python3
"""004 T012: the user calls, `screen review|mark|report|export|release|quarantine|escalate` (D14, D20, FR-015, FR-016, FR-018).

    python3 -m unittest test_screen_calls -v

Each command is a subprocess with a temporary HOME, ROOKERY_DB and ROOKERY_ROOT. No model call.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import unittest.mock

import rookery
import screener_t0 as S
from test_hooks import Base, CLI, HERE

BODY = "SECRET-BODY-TEXT"


class Calls(Base):
    def setUp(self):
        super().setUp()
        self.sid = self.alpha["session_id"]

    def mail(self, hold=True):
        return rookery.send(self.conn, self.swe["agent_id"], self.researcher["agent_id"], BODY, topic="T-7",
                            session_id=self.sid, hold=hold)

    def rec(self, mail_id, stage, label, check=None, confidence=None, decided_by=None):
        check = check or {"t0": "injection", "t1": "t1", "hunch": "hunch", "t2": "t2", "suggest": "suggest"}[stage]
        by = decided_by or ("t0:injection" if stage == "t0" else "screener:tev1:4b")
        rid = S.record(self.conn, mail_id=mail_id, session_id=self.sid, stage=stage, check_name=check, mode="shadow",
                       decided_by=by, label=label, confidence=confidence, attempt=1 if stage != "t0" else 1)
        self.conn.commit()
        return rid

    def calls(self):
        return self.conn.execute("SELECT * FROM screen_calls ORDER BY id").fetchall()

    def screen(self, *args, check=True):
        p = subprocess.run([sys.executable, CLI, "screen", *args], capture_output=True, text=True,
                           env=self.env(session=False, node=False), cwd=HERE, timeout=120)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def status(self, mid):
        return rookery.get_message(self.conn, mid)["status"]


class Mapping(Calls):
    """Each row of the mapping table (FR-015)."""

    def setUp(self):
        super().setUp()
        self.mid = self.mail()
        self.d_t0 = self.rec(self.mid, "t0", "would-deny")
        self.d_t1 = self.rec(self.mid, "t1", "unneeded", confidence=0.9)
        self.d_h = self.rec(self.mid, "hunch", "deliver")
        self.d_sug = self.rec(self.mid, "suggest", "researcher")

    def by_decision(self):
        return {c["decision_id"]: (c["call_label"], c["verdict"], c["source"], c["by"]) for c in self.calls()}

    def test_release(self):
        self.assertEqual(S.calls_for_action(self.conn, self.mid, "release", None, "human:ann"), 3)
        got = self.by_decision()
        self.assertEqual(got[self.d_t0], ("deliver", "wrong", "release", "human:ann"))
        self.assertEqual(got[self.d_t1][:2], ("deliver", "wrong"))
        self.assertEqual(got[self.d_h][:2], ("deliver", "right"))
        self.assertNotIn(self.d_sug, got)          # suggest: only from mark

    def test_unquarantine_same_as_release(self):
        rookery.quarantine(self.conn, self.mid, "unneeded", "human:ann")
        S.calls_for_action(self.conn, self.mid, "unquarantine", None, "human:ann")
        got = self.by_decision()
        self.assertEqual(got[self.d_t0][:3], ("deliver", "wrong", "unquarantine"))
        self.assertEqual(got[self.d_h][:2], ("deliver", "right"))

    def test_quarantine_with_t1_label(self):
        S.calls_for_action(self.conn, self.mid, "quarantine", "unneeded", "human:ann")
        got = self.by_decision()
        self.assertEqual(got[self.d_t0][:2], ("would-deny", "right"))
        self.assertEqual(got[self.d_t1][:2], ("unneeded", "right"))
        self.assertEqual(got[self.d_h][:2], ("unneeded", "wrong"))

    def test_quarantine_with_other_reason_is_a_disagreement(self):
        S.calls_for_action(self.conn, self.mid, "quarantine", "off-team", "human:ann")
        got = self.by_decision()
        self.assertEqual(got[self.d_t1][:2], ("off-team", "wrong"))
        self.assertEqual(got[self.d_t0][:2], ("would-deny", "right"))

    def test_t0_record_that_blocks_nothing_gets_no_call(self):
        mid = self.mail()
        self.rec(mid, "t0", "skipped", check="thread")
        self.assertEqual(S.calls_for_action(self.conn, mid, "release", None, "human:ann"), 0)

    def test_every_blocking_t0_label(self):
        for label in S.T0_BLOCKING:
            mid = self.mail()
            self.rec(mid, "t0", label, check="gates")
            self.assertEqual(S.calls_for_action(self.conn, mid, "release", None, "human:ann"), 1, label)

    def test_bad_action_refuses(self):
        with self.assertRaises(ValueError):
            S.calls_for_action(self.conn, self.mid, "escalate", None, "human:ann")
        with self.assertRaises(ValueError):
            S.calls_for_action(self.conn, self.mid, "quarantine", None, "human:ann")


class Mark(Calls):
    def setUp(self):
        super().setUp()
        self.mid = self.mail()
        self.t0 = self.rec(self.mid, "t0", "duplicate", check="duplicate")
        self.t1 = self.rec(self.mid, "t1", "deliver", confidence=0.8)

    def test_wrong_on_a_model_decision_refuses_and_asks_for_the_label(self):
        p = self.screen("mark", str(self.t1), "wrong", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("label", p.stderr)
        self.assertEqual(self.calls(), [])

    def test_label_on_t0_is_refused(self):
        p = self.screen("mark", str(self.t0), "deliver", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_right_wrong_and_label(self):
        self.screen("mark", str(self.t1), "right", "--by", "ann")
        self.screen("mark", str(self.t0), "wrong")
        c = self.calls()
        self.assertEqual((c[0]["call_label"], c[0]["verdict"], c[0]["by"], c[0]["source"]),
                         ("deliver", "right", "human:ann", "mark"))
        self.assertEqual((c[1]["verdict"], c[1]["by"].startswith("human:")), ("wrong", True))
        self.screen("mark", str(self.t1), "unneeded")
        self.assertEqual(self.calls()[-1]["verdict"], "wrong")

    def test_unknown_decision(self):
        p = self.screen("mark", "999", "right", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("999", p.stderr)


class Newest(Calls):
    def test_newest_call_counts(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver", confidence=0.95)
        self.screen("mark", str(d), "unneeded")
        r = json.loads(self.screen("report", "--json").stdout)["checks"]["t1"]
        self.assertEqual((r["compared"], r["agree"]), (1, 0))
        self.screen("mark", str(d), "right")
        r = json.loads(self.screen("report", "--json").stdout)["checks"]["t1"]
        self.assertEqual((r["compared"], r["agree"]), (1, 1))
        lines = self.screen("export").stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["call_label"], "deliver")


class Report(Calls):
    def fill(self):
        # 150 compared t1 decisions, 140 right: 7 deliver->unneeded, 3 unneeded->deliver.
        mid = self.mail()
        for i in range(150):
            if i < 100:
                label, call, conf = "deliver", "deliver", 0.95
            elif i < 107:
                label, call, conf = "deliver", "unneeded", 0.92
            elif i < 140:
                label, call, conf = "unneeded", "unneeded", 0.85
            else:
                label, call, conf = "unneeded", ("deliver" if i < 143 else "unneeded"), 0.81
            d = S.record(self.conn, mail_id=mid, session_id=self.sid, stage="t1", check_name="t1", mode="shadow",
                         decided_by="screener:tev1:4b", label=label, confidence=conf, attempt=i + 1)
            S.add_call(self.conn, d, mid, call, "right" if call == label else "wrong", "mark", "human:ann")
        self.conn.commit()

    def test_150_decisions_140_right(self):
        self.fill()
        r = json.loads(self.screen("report", "--json").stdout)["checks"]["t1"]
        self.assertEqual((r["compared"], r["agree"], r["rate"]), (150, 140, 0.933))
        self.assertEqual(r["disagreements"], {"deliver->unneeded": 7, "unneeded->deliver": 3})
        self.assertEqual(r["by_confidence"]["0.9"], {"compared": 107, "rate": 0.935})
        self.assertEqual(r["by_confidence"]["0.8"], {"compared": 43, "rate": 0.93})
        self.assertEqual(r["missing"], 0)
        self.assertFalse(r["live_ready"])           # no bands yet
        d = os.path.join(self.tmp.name, ".rookery")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": {"accept": 0.9, "review": 0.5}}}}, fh)
        r = json.loads(self.screen("report", "--json").stdout)["checks"]["t1"]
        self.assertTrue(r["live_ready"])

    def test_text_form(self):
        self.fill()
        out = self.screen("report").stdout
        for part in ("compared=150", "agree=140", "rate=0.933", "deliver->unneeded: 7", "confidence 0.9"):
            self.assertIn(part, out)

    def test_missing_and_hunch_needs_no_bands(self):
        mid = self.mail()
        for i in range(149):
            d = S.record(self.conn, mail_id=mid, session_id=self.sid, stage="hunch", check_name="hunch",
                         mode="shadow", decided_by="hunch:vote6:qwen2.5:3b", label="deliver", attempt=i + 1)
            S.add_call(self.conn, d, mid, "deliver", "right", "mark", "human:ann")
        self.conn.commit()
        r = json.loads(self.screen("report", "--json").stdout)["checks"]["hunch"]
        self.assertEqual((r["compared"], r["missing"], r["live_ready"]), (149, 1, False))

    def test_t0_check_is_ready_with_no_count(self):
        mid = self.mail()
        d = self.rec(mid, "t0", "duplicate", check="duplicate")
        S.add_call(self.conn, d, mid, "deliver", "wrong", "mark", "human:ann")
        self.conn.commit()
        r = json.loads(self.screen("report", "--check", "duplicate", "--json").stdout)["checks"]["duplicate"]
        self.assertEqual((r["live_ready"], r["missing"]), (True, 0))


class ExportReview(Calls):
    def test_export_keys_and_no_body(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver", confidence=0.97)
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, envelope) VALUES(?,?,?,?)",
                          (mid, self.sid, "decided", json.dumps({"mail": mid, "kind": "ask"})))
        self.conn.commit()
        self.screen("mark", str(d), "right", "--by", "ann")
        out = os.path.join(self.tmp.name, "out.jsonl")
        self.screen("export", "--out", out)
        with open(out) as fh:
            text = fh.read()
        line = json.loads(text.splitlines()[0])
        self.assertEqual(sorted(line), sorted(["mail_id", "session", "check", "envelope", "label", "confidence",
                                               "decided_by", "call_label", "call_by", "called_at"]))
        self.assertEqual((line["session"], line["call_by"], line["envelope"]["kind"]), ("alpha", "human:ann", "ask"))
        self.assertNotIn(BODY, text)
        self.assertNotIn(BODY, self.screen("export").stdout)

    def test_review_lists_only_decisions_with_no_call_and_no_body(self):
        mid = self.mail()
        a = self.rec(mid, "t1", "deliver", confidence=0.9)
        b = self.rec(mid, "hunch", "deliver")
        self.screen("mark", str(a), "right")
        out = self.screen("review", "--json").stdout
        rows = json.loads(out)
        self.assertEqual([r["decision"] for r in rows], [b])
        self.assertEqual(rows[0]["task"], "T-7")
        self.assertEqual(sorted(rows[0]), ["check", "confidence", "decision", "from", "label", "mail_id", "task",
                                           "thread", "to"])
        self.assertNotIn(BODY, out)
        self.assertNotIn(BODY, self.screen("review").stdout)
        self.assertEqual(json.loads(self.screen("review", "--check", "t1", "--json").stdout), [])


class Actions(Calls):
    def test_release_writes_calls_with_human_name(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "unneeded")
        self.screen("release", str(mid), "--by", "ann")
        self.assertEqual(self.status(mid), "pending")
        row = rookery.get_message(self.conn, mid)
        self.assertEqual(row["decided_by"], "human:ann")
        c = self.calls()
        self.assertEqual([(x["decision_id"], x["call_label"], x["verdict"], x["source"], x["by"]) for x in c],
                         [(d, "deliver", "wrong", "release", "human:ann")])

    def test_default_by_is_the_user_name(self):
        import getpass
        mid = self.mail()
        self.screen("release", str(mid))
        self.assertEqual(rookery.get_message(self.conn, mid)["decided_by"], "human:" + getpass.getuser())

    def test_quarantine_from_escalated(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "unneeded")
        rookery.escalate(self.conn, mid, "screener:tev1:4b", "failed")
        self.screen("quarantine", str(mid), "unneeded", "--note", "no")
        self.assertEqual(self.status(mid), "quarantined")
        self.assertEqual([(x["call_label"], x["verdict"], x["source"]) for x in self.calls()],
                         [("unneeded", "right", "quarantine")])

    def test_wrong_status_refuses_and_leaves_no_call(self):
        mid = self.mail(hold=False)
        self.rec(mid, "t1", "deliver")
        p = self.screen("release", str(mid), check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("pending", p.stderr)
        self.assertEqual(self.calls(), [])

    def test_escalate(self):
        mid = self.mail()
        self.screen("escalate", str(mid), "--reason", "needs a person", "--by", "ann")
        row = rookery.get_message(self.conn, mid)
        self.assertEqual((row["status"], row["reason"], row["decided_by"]), ("escalated", "needs a person", "human:ann"))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.screen("escalate", str(mid), check=False).returncode, 2)

    def test_escalate_sends_the_notice_to_the_boss(self):
        self.conn.execute("INSERT INTO up_agents(agent_id, session_id, harness, role, repo, task_id, binding_kind, "
                          "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (self.researcher["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()
        mid = self.mail()
        self.screen("escalate", str(mid), "--reason", "needs a person", "--by", "ann")
        notes = self.conn.execute("SELECT body FROM inbox WHERE sender='rookery' AND recipient=?",
                                  (self.researcher["agent_id"],)).fetchall()
        self.assertEqual(len(notes), 1)
        self.assertIn(f"Mail {mid} ", notes[0][0])
        self.assertNotIn(BODY, notes[0][0])     # FR-029: no body


class MailScreen(Calls):
    def run_ms(self, *args):
        p = subprocess.run([sys.executable, os.path.join(HERE, "mail_screen.py"), *args], capture_output=True,
                           text=True, env=self.env(session=False, node=False), cwd=HERE, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_release_quarantine_unquarantine_write_calls(self):
        import getpass
        user = "human:" + getpass.getuser()
        m1, m2 = self.mail(), self.mail()
        self.rec(m1, "t1", "deliver")
        self.rec(m2, "t1", "unneeded")
        self.run_ms("--id", str(m1), "--release")
        self.run_ms("--id", str(m2), "--quarantine", "unneeded")
        self.run_ms("--id", str(m2), "--unquarantine")
        c = [(x["mail_id"], x["call_label"], x["verdict"], x["source"], x["by"]) for x in self.calls()]
        self.assertEqual(c, [(m1, "deliver", "right", "release", user), (m2, "unneeded", "right", "quarantine", user),
                             (m2, "deliver", "wrong", "unquarantine", user)])
        self.assertEqual(rookery.get_message(self.conn, m1)["decided_by"], user)

    def test_a_classifier_id_writes_no_call(self):
        mid = self.mail()
        self.rec(mid, "t1", "deliver")
        self.run_ms("--id", str(mid), "--release", "--by", "tev1")
        self.assertEqual(self.calls(), [])


class Audit(Calls):
    """Findings F1 to F7 of the wave 7 audit."""

    def test_t1_labels_match_the_screener(self):
        import cli_screen, screener   # cli_screen keeps its own copy (F20)
        self.assertEqual(tuple(cli_screen.T1_LABELS), tuple(screener.LABELS))

    def run_cli(self, *args):
        return self.screen(*args, check=False)

    def test_f1_skipped_is_not_marked_not_compared_not_reviewed(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "skipped")
        p = self.run_cli("mark", str(d), "deliver")
        self.assertEqual(p.returncode, 2)
        self.assertIn("skipped", p.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(json.loads(self.screen("review", "--json").stdout), [])
        # a call row that exists anyway must not count
        self.conn.execute("INSERT INTO screen_calls(decision_id, mail_id, call_label, verdict, source, \"by\","
                          " created_at) VALUES(?,?,?,?,?,?,?)", (d, mid, "deliver", "wrong", "mark", "human:a", 1))
        self.conn.commit()
        rep = json.loads(self.screen("report", "--json").stdout)
        self.assertEqual(rep["checks"], {})
        self.assertEqual(self.screen("export").stdout, "")

    def test_f2_label_must_be_known(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver")
        p = self.run_cli("mark", str(d), "banana")
        self.assertEqual((p.returncode, "banana" in p.stderr), (2, True))
        self.assertEqual(self.run_cli("mark", str(d), "off-team").returncode, 0)
        self.assertEqual(self.run_cli("mark", str(d), "wrong-recipient").returncode, 0)
        self.assertEqual(len(self.calls()), 2)

    def test_f2_suggest_takes_a_roster_name_or_none(self):
        mid = self.mail()
        d = self.rec(mid, "suggest", "researcher")
        p = self.run_cli("mark", str(d), "nobody")
        self.assertEqual((p.returncode, "nobody" in p.stderr), (2, True))
        self.assertEqual(self.run_cli("mark", str(d), "swe").returncode, 0)
        self.assertEqual(self.run_cli("mark", str(d), "none").returncode, 0)
        self.assertEqual([c["call_label"] for c in self.calls()], ["swe", "none"])

    def test_f3_empty_label(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver")
        p = self.run_cli("mark", str(d), "")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn(str(d), p.stderr)

    def test_f3_decision_without_mail(self):
        rid = S.record(self.conn, mail_id=None, session_id=self.sid, stage="t1", check_name="t1", mode="shadow",
                       decided_by="screener:tev1:4b", label="deliver")
        self.conn.commit()
        p = self.run_cli("mark", str(rid), "right")
        self.assertEqual(p.returncode, 2)
        self.assertIn("This decision has no mail.", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_f3_export_out_missing_folder_and_atomic(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver")
        self.screen("mark", str(d), "right")
        bad = os.path.join(self.tmp.name, "nope", "x.jsonl")
        p = self.run_cli("export", "--out", bad)
        self.assertEqual(p.returncode, 2)
        self.assertIn("nope", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        good = os.path.join(self.tmp.name, "x.jsonl")
        with open(good, "w") as fh:
            fh.write("OLD\n")
        self.screen("export", "--out", good)
        with open(good) as fh:
            self.assertEqual(len(fh.read().splitlines()), 1)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if f.startswith(".export-")], [])

    def test_f3_corrupt_envelope_is_null(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver")
        self.screen("mark", str(d), "right")
        self.conn.execute("INSERT INTO screen_mail(mail_id, session_id, state, envelope) VALUES(?,?,?,?)",
                          (mid, self.sid, "decided", "{not json"))
        self.conn.commit()
        row = json.loads(self.screen("export").stdout.splitlines()[0])
        self.assertIsNone(row["envelope"])

    def test_f4_a_refused_action_leaves_no_call(self):
        import cli_screen
        import types
        mid = self.mail()
        self.rec(mid, "t1", "unneeded")
        real = rookery.get_message
        racer = rookery.connect(self.db)
        racer.execute("PRAGMA busy_timeout=0")
        state = {"done": False}

        def racing_get(conn, msg_id):
            row = real(conn, msg_id)
            if not state["done"]:
                state["done"] = True
                try:   # another process delivers the mail between the check and the status change
                    racer.execute("UPDATE inbox SET status='pending', decided_by='racer' WHERE id=?", (msg_id,))
                    racer.commit()
                except Exception:
                    racer.rollback()
            return row
        args = types.SimpleNamespace(mail_id=mid, by="ann")
        try:
            with unittest.mock.patch.object(rookery, "get_message", racing_get):
                try:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        cli_screen.cmd_release(args)
                except SystemExit:
                    pass
        finally:
            racer.close()
        row = real(self.conn, mid)
        if row["decided_by"] != "human:ann":     # the action was refused: no call may stay
            self.assertEqual(self.calls(), [])
        else:
            self.assertEqual(len(self.calls()), 1)

    def test_f5_by_is_validated(self):
        mid = self.mail()
        d = self.rec(mid, "t1", "deliver")
        for bad in ("screener:x", "t0:injection", "hunch:vote6:m", "t2:z", "a b", "a\nb"):
            p = self.run_cli("mark", str(d), "right", "--by", bad)
            self.assertEqual(p.returncode, 2, bad)
            self.assertNotIn("Traceback", p.stderr)
            p = self.run_cli("release", str(mid), "--by", bad)
            self.assertEqual(p.returncode, 2, bad)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.status(mid), "held")

    def test_f5_mail_screen_human_by_is_validated(self):
        mid = self.mail()
        self.rec(mid, "t1", "deliver")
        p = subprocess.run([sys.executable, os.path.join(HERE, "mail_screen.py"), "--id", str(mid), "--release",
                            "--by", "human:a b"], capture_output=True, text=True,
                           env=self.env(session=False, node=False), cwd=HERE, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.status(mid), "held")

    def test_f7_report_check_with_no_rows_and_unknown_session(self):
        p = self.screen("report", "--check", "t1")
        self.assertIn("No decision has a call yet.", p.stdout)
        q = self.run_cli("review", "--session", "ghost")
        self.assertEqual(q.returncode, 2)
        self.assertIn("ghost", q.stderr)


class LiveSwitch(Calls):
    def fill(self, check, n):
        mid = self.mail()
        for i in range(n):
            d = S.record(self.conn, mail_id=mid, session_id=self.sid, stage=check, check_name=check, mode="shadow",
                         decided_by="screener:tev1:4b", label="deliver", attempt=i + 1)
            S.add_call(self.conn, d, mid, "deliver", "right", "mark", "human:ann")
        self.conn.commit()

    def bands(self):
        d = os.path.join(self.tmp.name, ".rookery")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": {"accept": 0.9, "review": 0.5}}}}, fh)

    def mode(self, check):
        with open(os.path.join(self.tmp.name, ".rookery", "config.json")) as fh:
            return json.load(fh)["screener"].get("checks", {}).get(check)

    def test_149_refuses_and_says_1(self):
        self.fill("t1", 149)
        self.bands()
        p = self.screen("mode", "t1", "live", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("It has 149 compared decisions and needs 150. Compare 1 more decision with "
                      "`rookery screen mark`.", p.stderr)
        self.assertIsNone(self.mode("t1"))

    def test_150_with_bands_accepts_and_writes(self):
        self.fill("t1", 150)
        self.bands()
        self.screen("mode", "t1", "live")
        self.assertEqual(self.mode("t1"), "live")
        with open(os.path.join(self.tmp.name, ".rookery", "config.json")) as fh:
            self.assertIn("bands", json.load(fh)["screener"])    # other keys stay

    def test_150_without_bands_names_bands(self):
        self.fill("t1", 150)
        p = self.screen("mode", "t1", "live", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("screener.bands.t1", p.stderr)

    def test_t0_check_goes_live_with_no_count(self):
        self.screen("mode", "duplicate", "live")
        self.assertEqual(self.mode("duplicate"), "live")

    def test_hunch_and_t2_need_150_and_no_bands(self):
        for check in ("hunch", "t2"):
            self.fill(check, 149)
            p = self.screen("mode", check, "live", check=False)
            self.assertEqual(p.returncode, 2)
            self.assertIn("needs 150", p.stderr)
            self.fill(check, 1)
            self.screen("mode", check, "live")
            self.assertEqual(self.mode(check), "live")

    def test_shadow_and_off_always_allowed(self):
        self.screen("mode", "t1", "off")
        self.assertEqual(self.mode("t1"), "off")
        self.screen("mode", "t1", "shadow")
        self.assertEqual(self.mode("t1"), "shadow")

    def test_unknown_check_or_mode_names_it(self):
        p = self.screen("mode", "ghost", "live", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("ghost", p.stderr)
        p = self.screen("mode", "t1", "fast", check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("fast", p.stderr)


class Text(unittest.TestCase):
    TEXTS = ["No decision waits for a call.", "No decision has a call yet.", "Released mail 5.",
             "Quarantined mail 5 (unneeded).", "Escalated mail 5.", "Wrote 3 lines to out.jsonl.",
             "Show only this check.", "Rows to show (default 50).", "List the decisions that have no call.",
             "Say if a decision was right.", "Show how often the calls agree with the decisions.",
             "Write one JSON line for each compared decision.", "Deliver a held or escalated mail.",
             "Reject a held or escalated mail.", "Hold a mail for a person.", "Why the mail waits.",
             "Your name (default: your user name).", "The agent that the sender should use.",
             "This decision has no mail. Rookery cannot record a call for it.",
             "Set t1 to live mode.", "Set a check to shadow, live or off.",
             "Rookery cannot switch t1 to live mode. It has 149 compared decisions and needs 150. "
             "Compare 1 more decision with `rookery screen mark`.",
             "Rookery cannot switch t1 to live mode. It has no bands. Set screener.bands.t1 in the configuration first.",
             "'ghost' is not a check. Use one of injection, duplicate.", "'fast' is not a mode. Use one of shadow, live, off.",
             "Rookery delivered mail 5 from swe to researcher. The screener says unneeded. "
             "Use rookery screen review to check it.",
             S.BUDGET_BOUNCE.format(task="T-7", limit=20),
             S.DUPLICATE_BOUNCE.format(recipient="researcher", time="2026-10-25 10:30 UTC"),
             "Close a thread.", "Close a thread. It stays closed.", "Close it as the boss of the session.",
             "Closed thread th-1.", "Thread th-1 is already closed.",
             "'swe' is not the boss of session 'alpha'.", "Thread 'x' does not exist in session 'alpha'."]

    def test_ste_lint(self):
        exe = shutil.which("ste-lint")
        if not exe:
            self.skipTest("ste-lint is not installed")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "texts.md")
            with open(path, "w") as fh:
                fh.write("\n\n".join(self.TEXTS) + "\n")
            p = subprocess.run([exe, "--max-score", "2.0", "--glossary", os.path.join(HERE, "ste-glossary.txt"), path],
                               capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
