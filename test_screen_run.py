#!/usr/bin/env python3
"""004 T010: screener.build_envelope() and screen_one() (D8, D11, FR-007, FR-008, FR-010, FR-012, FR-013, FR-031, SC-007).

    python3 -m unittest test_screen_run -v

The fake backend (fixtures/screener/fake_t1.json, copied to a temp folder) answers; its .calls file counts calls.
"""
import copy
import json
import os
import shutil
import sqlite3
import sys

import rookery
import rookery_identity as RI
import screener
import screener_backend as SB
import screener_config
import screener_t0
from test_hooks import Base

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "screener", "fake_t1.json")


def block(**kw):
    b = {"to": "researcher", "task": "T-7", "kind": "ask", "say": "CASE-DELIVER read the file", "status": "in-progress"}
    b.update(kw)
    return json.dumps(b, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


class Screen(Base):
    def setUp(self):
        super().setUp()
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.fake_path = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake_path)
        self.backend = SB.fake(self.fake_path)
        self.sid = self.alpha["session_id"]
        self.conn.row_factory = sqlite3.Row

    def calls(self):
        p = self.fake_path + ".calls"
        if not os.path.exists(p):
            return 0
        with open(p) as fh:
            return len(fh.read().splitlines())

    def mail(self, body, sender=None, to=None, sid=None, topic="T-7", thread=None):
        sid = sid or self.sid
        m = rookery.send(self.conn, sender or self.swe["agent_id"], to or self.researcher["agent_id"], body,
                         topic=topic, session_id=sid, thread=thread)
        return m

    def row(self, mid):
        return self.conn.execute("SELECT * FROM inbox WHERE id=?", (mid,)).fetchone()

    def recs(self, mid=None):
        q = "SELECT * FROM screen_decisions" + (" WHERE mail_id=%d" % mid if mid else "") + " ORDER BY id"
        return self.conn.execute(q).fetchall()

    def scope(self, ident, does, does_not):
        self.conn.execute("INSERT OR REPLACE INTO agent_scope VALUES(?,?,?,?)",
                          (ident["agent_id"], ident["session_id"], json.dumps(does), json.dumps(does_not)))
        self.conn.commit()

    def context(self, sid, acceptance, task="the task"):
        self.conn.execute("INSERT OR REPLACE INTO session_context VALUES(?,?,?,?,?)",
                          (sid, task, json.dumps(acceptance), None, "2026-10-25T00:00:00Z"))
        self.conn.commit()

    def test_each_label_is_recorded(self):
        for case, label in (("CASE-DELIVER", "deliver"), ("CASE-DONE", "task-already-done"),
                            ("CASE-UNNEEDED", "unneeded")):
            mid = self.mail(block(say=case + " please"))
            self.assertEqual(screener.screen_one(self.conn, mid, self.cfg, self.backend), label)
            r = self.recs(mid)[0]
            self.assertEqual((r["stage"], r["label"], r["failure"], r["band"], r["mode"], r["action"]),
                             ("t1", label, None, "unset", "shadow", "none"))
            self.assertEqual((r["decided_by"], r["backend_impl"]), ("screener:tev1:4b", "fake"))
            self.assertTrue(screener_t0.check_decided_by(r["decided_by"]))
        self.assertEqual(self.calls(), 3)

    def test_wrong_recipient_asks_suggest_without_sender_and_recipient(self):
        beta = RI.new_identity(self.conn, self.sid, "counsel")
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": "roster:", "label": "counsel", "confidence": 0.8},
                              {"match": "CASE-WRONG", "label": "wrong-recipient", "confidence": 0.91}]}, fh)
        seen = []
        real = self.backend.decide
        self.backend.decide = lambda state, labels, instr, question="route": (
            seen.append((question, list(labels))), real(state, labels, instr, question=question))[1]
        mid = self.mail(block(say="CASE-WRONG who reviews"))
        self.assertEqual(screener.screen_one(self.conn, mid, self.cfg, self.backend), "wrong-recipient")
        q, labels = seen[1]
        self.assertEqual(q, "suggest")
        self.assertEqual(sorted(labels), ["counsel", "me", "none"])   # no swe (sender), no researcher (to)
        self.assertEqual(seen[0][1], screener.LABELS)
        t1, sg = self.recs(mid)
        self.assertEqual((sg["stage"], sg["check_name"], sg["label"], sg["decided_by"]),
                         ("suggest", "suggest", "counsel", "screener:tev1:4b"))
        self.assertIsNone(sg["band"])
        self.assertTrue(all(screener_t0.check_decided_by(r["decided_by"]) for r in (t1, sg)))

    def test_decline_naming_a_peer_keeps_it_without_a_call(self):
        RI.new_identity(self.conn, self.sid, "counsel")
        mid = self.mail(block(kind="decline", say="CASE-WRONG ask counsel instead"))
        screener.screen_one(self.conn, mid, self.cfg, self.backend)
        sg, t1 = self.recs(mid)
        self.assertEqual((sg["stage"], sg["label"], sg["decided_by"]), ("suggest", "counsel", "t0:gates"))
        self.assertEqual(t1["label"], "wrong-recipient")
        self.assertEqual(self.calls(), 1)   # the route call only: no suggest call

    def test_too_large_state_gives_failure_and_no_call(self):
        say = "CASE-DELIVER " + "x" * 1180
        self.scope(self.swe, ["a" * 600], ["b" * 600])
        self.scope(self.researcher, ["c" * 600], ["d" * 600])
        mid = self.mail(block(say=say))
        self.assertGreater(len(screener.build_envelope(self.conn, self.row(mid), self.cfg)), 3000)
        self.assertIsNone(screener.screen_one(self.conn, mid, self.cfg, self.backend))
        (r,) = self.recs(mid)
        self.assertEqual((r["failure"], r["label"], r["action"]), ("envelope-too-large", None, "none"))
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.row(mid)["status"], "held" if rookery.hold_enabled() else "pending")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_decisions WHERE label='deliver'").fetchone()[0], 0)

    def test_evidence_never_in_envelope(self):
        mid = self.mail(block(say="CASE-DELIVER ok", self_check={"task_complete": False, "gap": "none",
                                                                  "gap_in_my_scope": True,
                                                                  "evidence": "SECRET-PASTED-WORK"}))
        env = screener.build_envelope(self.conn, self.row(mid), self.cfg)
        self.assertNotIn("SECRET-PASTED-WORK", env)
        self.assertNotIn("evidence", env)
        d = json.loads(env)
        self.assertEqual(d["self_check"], {"gap": "none", "gap_in_my_scope": True, "task_complete": False})
        self.assertEqual(list(d)[:6], ["mail", "session", "kind", "from", "to", "task"])
        self.assertEqual((d["session"], d["from"], d["to"], d["ask"]), ("alpha", "swe", "researcher", "CASE-DELIVER ok"))
        self.assertNotIn(" ", env.split('"ask"')[0])   # compact separators

    def test_plain_mail_uses_body_as_ask(self):
        mid = self.mail("CASE-DELIVER hello there")
        d = json.loads(screener.build_envelope(self.conn, self.row(mid), self.cfg))
        self.assertEqual((d["kind"], d["ask"], d["task"]), ("plain", "CASE-DELIVER hello there", "T-7"))
        self.assertNotIn("self_check", d)

    def test_two_sessions_two_envelopes(self):
        other = RI.new_session(self.conn, name="beta")
        bs = RI.new_identity(self.conn, other["session_id"], "swe")
        br = RI.new_identity(self.conn, other["session_id"], "researcher")
        RI.new_identity(self.conn, other["session_id"], "zed")
        self.scope(self.swe, ["alpha-swe-does"], [])
        self.scope(bs, ["beta-swe-does"], ["beta-swe-not"])
        self.context(self.sid, ["alpha-criterion"])
        self.context(other["session_id"], ["beta-criterion"])
        a = self.mail(block(say="CASE-WRONG alpha"))
        b = self.mail(block(say="CASE-WRONG beta"), sender=bs["agent_id"], to=br["agent_id"], sid=other["session_id"])
        ea = screener.build_envelope(self.conn, self.row(a), self.cfg)
        eb = screener.build_envelope(self.conn, self.row(b), self.cfg)
        self.assertIn("alpha-swe-does", ea)
        self.assertIn("alpha-criterion", ea)
        self.assertNotIn("beta", ea.replace('"session":"alpha"', ""))
        self.assertIn("beta-swe-does", eb)
        self.assertIn("beta-criterion", eb)
        self.assertNotIn("alpha", eb)
        # the suggest roster of B is B's own: zed, none (swe sent it, researcher got it)
        seen = []
        real = self.backend.decide
        self.backend.decide = lambda s, l, i, question="route": (seen.append((question, list(l))), real(s, l, i, question=question))[1]
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": "roster:", "label": "zed", "confidence": 0.7},
                              {"match": "CASE-WRONG", "label": "wrong-recipient", "confidence": 0.9}]}, fh)
        screener.screen_one(self.conn, b, self.cfg, self.backend)
        self.assertEqual(sorted(seen[1][1]), ["none", "zed"])

    def test_timeout_and_bad_answer_give_failures_with_no_label(self):
        for case, cause in (("CASE-TIMEOUT", "timeout"), ("CASE-BADANSWER", "bad-answer")):
            mid = self.mail(block(say=case))
            self.assertIsNone(screener.screen_one(self.conn, mid, self.cfg, self.backend))
            (r,) = self.recs(mid)
            self.assertEqual((r["failure"], r["label"], r["confidence"], r["band"]), (cause, None, None, None))
            self.assertTrue(screener_t0.check_decided_by(r["decided_by"]))
        # no rule at all: the answer is outside the labels
        mid = self.mail(block(say="no case word"))
        self.assertIsNone(screener.screen_one(self.conn, mid, self.cfg, self.backend))
        self.assertEqual(self.recs(mid)[0]["failure"], "bad-answer")

    def test_bands(self):
        self.cfg["bands"]["t1"] = {"accept": 0.94, "review": 0.9}
        for case, band in (("CASE-DELIVER", "accept"), ("CASE-DONE", "review"), ("CASE-UNNEEDED", "review")):
            mid = self.mail(block(say=case))
            screener.screen_one(self.conn, mid, self.cfg, self.backend)
            self.assertEqual(self.recs(mid)[0]["band"], band)
        self.cfg["bands"]["t1"] = {"accept": 0.99, "review": 0.95}
        mid = self.mail(block(say="CASE-WRONG"))
        screener.screen_one(self.conn, mid, self.cfg, self.backend)
        self.assertEqual(self.recs(mid)[0]["band"], "escalate")

    def test_second_screen_is_the_next_attempt(self):
        mid = self.mail(block(say="CASE-TIMEOUT"))
        screener.screen_one(self.conn, mid, self.cfg, self.backend)
        screener.screen_one(self.conn, mid, self.cfg, self.backend)
        self.assertEqual([r["attempt"] for r in self.recs(mid)], [1, 2])

    def test_off_writes_nothing_and_inbox_is_untouched(self):
        mid = self.mail(block(say="CASE-DELIVER"))
        before = tuple(self.row(mid))
        self.cfg["checks"]["t1"] = "off"
        self.assertIsNone(screener.screen_one(self.conn, mid, self.cfg, self.backend))
        self.assertEqual((self.recs(), self.calls()), ([], 0))
        self.cfg["checks"]["t1"] = "shadow"
        screener.screen_one(self.conn, mid, self.cfg, self.backend)
        self.assertEqual(tuple(self.row(mid)), before)

    def test_suggest_state_over_the_limit_is_a_failure_and_no_suggest_call(self):   # F1
        for i in range(6):
            RI.new_identity(self.conn, self.sid, "peer" + "x" * 50 + str(i))
        base = len(screener.build_envelope(self.conn, self.row(self.mail(block(say="CASE-WRONG "))), self.cfg))
        self.cfg["envelope_max_chars"] = base + 20   # the route state fits, the roster suffix does not
        mid = self.mail(block(say="CASE-WRONG "))
        self.assertLessEqual(len(screener.build_envelope(self.conn, self.row(mid), self.cfg)), base + 20)
        self.assertEqual(screener.screen_one(self.conn, mid, self.cfg, self.backend), "wrong-recipient")
        t1, sg = self.recs(mid)
        self.assertEqual((sg["stage"], sg["failure"], sg["label"]), ("suggest", "envelope-too-large", None))
        self.assertEqual(self.calls(), 1)

    def test_block_fields_are_a_whitelist(self):   # F2, F3
        body = json.dumps({"kind": "ask", "say": "CASE-DELIVER", "task": {"a": 1}, "thread": 5, "read": 5,
                           "extra": "LEAK0", "self_check": {"Evidence": "LEAK1", "gap": {"evidence": "LEAK2"},
                                                            "task_complete": True, "other": "LEAK3"}})
        env = screener.build_envelope(self.conn, self.row(self.mail(body)), self.cfg)
        for leak in ("LEAK0", "LEAK1", "LEAK2", "LEAK3"):
            self.assertNotIn(leak, env)
        d = json.loads(env)
        self.assertEqual(d["self_check"], {"task_complete": True})
        self.assertEqual((d.get("task"), d.get("read")), (None, None))   # wrong-typed fields are dropped
        mid = self.mail(json.dumps({"kind": "ask", "say": "CASE-DELIVER", "self_check": "x", "read": [1]}))
        self.assertEqual(screener.screen_one(self.conn, mid, self.cfg, self.backend), "deliver")

    def test_envelope_that_cannot_be_built_is_a_failure_record(self):   # F3
        mid = self.mail(block(say="CASE-DELIVER"))
        real = screener.build_envelope
        screener.build_envelope = lambda *a: 1 / 0
        try:
            self.assertIsNone(screener.screen_one(self.conn, mid, self.cfg, self.backend))
        finally:
            screener.build_envelope = real
        self.assertEqual(self.recs(mid)[0]["failure"], "bad-envelope")
        self.assertEqual(self.calls(), 0)

    def test_attempt_collision_retries_with_a_fresh_number(self):   # F4
        mid = self.mail(block(say="CASE-TIMEOUT"))
        real = screener_t0.record
        hits = []

        def racing(conn, **kw):
            if not hits:   # a second writer takes the number first
                hits.append(1)
                conn.execute("INSERT INTO screen_decisions(mail_id, session_id, stage, check_name, attempt, mode,"
                             " decided_by, failure, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                             (mid, self.sid, "t1", "t1", kw["attempt"], "shadow", "t0:x", "timeout", "now"))
            return real(conn, **kw)
        screener_t0.record = racing
        try:
            screener.screen_one(self.conn, mid, self.cfg, self.backend)
        finally:
            screener_t0.record = real
        self.assertEqual([r["attempt"] for r in self.recs(mid)], [1, 2])

    def test_bad_model_name_raises_before_any_call(self):   # F5
        self.cfg["model"] = "my model+x"
        mid = self.mail(block(say="CASE-DELIVER"))
        with self.assertRaises(ValueError):
            screener.screen_one(self.conn, mid, self.cfg, self.backend)
        self.assertEqual((self.recs(), self.calls()), ([], 0))

    def test_decline_peer_with_trailing_punctuation(self):   # F6
        peers = ["counsel", "me"]
        for say, want in (("ask counsel.", "counsel"), ("ask counsel, please", "counsel"), ("ask counsel!", "counsel"),
                          ("ask counsel?", "counsel"), ("see counsel: now", "counsel"), ("ask counsel; ok", "counsel"),
                          ("use counsel-2", None), ("see counsel.md", None), ("ask me.", None)):
            b = screener._block(block(kind="decline", say=say))
            self.assertEqual(screener._named_peer(b, peers), want, say)

    def test_suggest_roster_is_deduped_and_has_no_peer_named_none(self):   # F7
        RI.new_identity(self.conn, self.sid, "none")
        RI.new_identity(self.conn, self.sid, "counsel")
        seen = []
        real = self.backend.decide
        self.backend.decide = lambda s, l, i, question="route": (seen.append(list(l)), real(s, l, i, question=question))[1]
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": [{"match": "roster:", "label": "none", "confidence": 0.5},
                              {"match": "CASE-WRONG", "label": "wrong-recipient", "confidence": 0.9}]}, fh)
        screener.screen_one(self.conn, self.mail(block(say="CASE-WRONG")), self.cfg, self.backend)
        self.assertEqual(sorted(seen[1]), ["counsel", "me", "none"])
        self.assertEqual(len(seen[1]), len(set(seen[1])))

    def test_no_forbidden_keys_and_no_promptchain(self):
        with open(os.path.join(HERE, "screener.py")) as fh:
            src = fh.read()
        for key in ("max_tokens", "max_completion_tokens", "num_predict", "num_ctx", "think"):
            self.assertNotIn(key, src)
        self.assertNotIn("import promptchain", src)
        self.assertNotIn("promptchain", sys.modules)


if __name__ == "__main__":
    import unittest
    unittest.main()
