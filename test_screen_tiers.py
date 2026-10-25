#!/usr/bin/env python3
"""004 T018: the second opinion of hunch (D11, D14, FR-009, FR-029).

    python3 -m unittest test_screen_tiers -v

T1 answers in a band. The band `review` asks hunch the same route question. Agreement acts, disagreement escalates.
The fake backend answers both kinds from one file (key `t1`, key `hunch`); no model call.
"""
import copy
import json
import os
import shutil
import sqlite3
import unittest
import unittest.mock as mock

import mail_gate
import rookery
import screener
import screener_backend as SB
import screener_config
from test_hooks import Base
from test_screen_run import block

HERE = os.path.dirname(os.path.abspath(__file__))
BANDS = {"accept": 0.8, "review": 0.5}


class Tiers(Base):
    def setUp(self):
        super().setUp()
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.cfg["checks"].update(t1="live", hunch="live")
        self.cfg["bands"]["t1"] = BANDS
        self.fake_path = os.path.join(self.tmp.name, "fake.json")
        self.sid = self.alpha["session_id"]
        self.conn.row_factory = sqlite3.Row
        self.t1 = SB.fake(self.fake_path, "t1")
        self.hunch = SB.fake(self.fake_path, "hunch")
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)
        self.conn.execute("INSERT INTO up_agents VALUES(?,?,?,?,?,?,?,?)",
                          (self.me["agent_id"], self.sid, "claude", "boss", None, None, "none", "now"))
        self.conn.commit()
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)   # the gate reads the file: hold as `screening`
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": BANDS}, "checks": {"t1": "live", "hunch": "live"}}}, fh)

    def fake(self, t1, hunch=()):
        rules = lambda rs: [{"match": m, **({"fail": x} if x in ("timeout", "bad-answer") else {"label": x, "confidence": c})}
                            for m, x, c in rs]
        with open(self.fake_path, "w") as fh:
            json.dump({"t1": rules(t1), "hunch": rules(hunch)}, fh)

    def calls(self, kind):
        try:
            with open(self.fake_path + ".calls") as fh:
                return len([ln for ln in fh if json.loads(ln)["kind"] == kind])
        except OSError:
            return 0

    def submit(self, say):
        r = mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                             body=block(say=say), topic="T-7")
        self.assertEqual(r["outcome"], "sent", r)
        return r["msg_id"]

    def passes(self, hunch="default"):
        return screener._screen_session(self.conn, self.sid, "alpha", self.cfg, self.t1, "me:1:x",
                                        rookery.now() - self.cfg["lease_s"], [],
                                        self.hunch if hunch == "default" else hunch)

    def row(self, mid):
        return self.conn.execute("SELECT * FROM inbox WHERE id=?", (mid,)).fetchone()

    def recs(self, mid, stage=None):
        return [r for r in self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id=? ORDER BY id", (mid,))
                if stage in (None, r["stage"])]

    def notices(self, mid):
        return [r for r in self.conn.execute("SELECT * FROM inbox WHERE sender='rookery' AND body NOT LIKE '{%'")
                if f"Mail {mid} " in r["body"]]

    def test_accept_acts_with_no_hunch_call(self):
        self.fake([("CASE-A", "deliver", 0.9)])
        mid = self.submit("CASE-A")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.calls("hunch")), ("pending", 0))
        self.assertEqual(self.recs(mid, "hunch"), [])

    def test_review_asks_hunch_once_and_agreement_releases(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.row(mid)["verdict"]), ("pending", "release"))
        self.assertEqual((self.calls("t1"), self.calls("hunch")), (1, 1))
        (h,) = self.recs(mid, "hunch")
        self.assertEqual((h["check_name"], h["decided_by"], h["label"], h["confidence"], h["mode"], h["failure"]),
                         ("hunch", "hunch:vote6:qwen2.5:3b", "deliver", 0.7, "live", None))
        self.assertEqual(self.recs(mid, "t1")[0]["action"], "release")
        self.assertEqual(self.notices(mid), [])

    def test_agreement_on_a_reject_label_quarantines(self):
        self.fake([("CASE-R", "unneeded", 0.6)], [("CASE-R", "unneeded", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual(self.row(mid)["status"], "quarantined")

    def test_disagreement_escalates_with_a_notice(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "unneeded", 0.9)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "t1 and hunch disagree"))
        self.assertEqual(self.recs(mid, "t1")[0]["action"], "escalate")
        self.assertEqual(len(self.notices(mid)), 1)

    def test_a_hunch_failure_escalates_and_is_recorded(self):
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "timeout", 0)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "hunch timeout"))
        (h,) = self.recs(mid, "hunch")
        self.assertEqual((h["label"], h["failure"], h["decided_by"]), (None, "timeout", "hunch:vote6:qwen2.5:3b"))
        self.assertEqual(len(self.notices(mid)), 1)

    def test_unset_bands_and_the_escalate_band_never_call_hunch(self):
        self.fake([("CASE-U", "deliver", 0.9), ("CASE-E", "deliver", 0.3)], [("CASE-U", "deliver", 1), ("CASE-E", "deliver", 1)])
        for case, bands in (("CASE-U", None), ("CASE-E", BANDS)):
            self.cfg["bands"]["t1"] = bands
            mid = self.submit(case)
            self.passes()
            self.assertEqual((self.row(mid)["status"], self.row(mid)["reason"]), ("escalated", "low-confidence deliver"))
        self.assertEqual(self.calls("hunch"), 0)

    def test_hunch_in_shadow_writes_the_record_and_the_mail_escalates(self):
        self.cfg["checks"]["hunch"] = "shadow"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        r = self.row(mid)
        self.assertEqual((r["status"], r["reason"]), ("escalated", "low-confidence deliver"))
        self.assertEqual([(h["mode"], h["label"]) for h in self.recs(mid, "hunch")], [("shadow", "deliver")])
        self.assertEqual(len(self.notices(mid)), 1)

    def test_hunch_off_makes_no_call(self):
        self.cfg["checks"]["hunch"] = "off"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")
        self.passes()
        self.assertEqual((self.row(mid)["status"], self.calls("hunch"), self.recs(mid, "hunch")), ("escalated", 0, []))

    def test_no_hunch_backend_escalates_at_review(self):
        self.fake([("CASE-R", "deliver", 0.6)])
        mid = self.submit("CASE-R")
        self.passes(hunch=None)
        self.assertEqual((self.row(mid)["reason"], self.recs(mid, "hunch")), ("low-confidence deliver", []))

    def test_t1_shadow_writes_the_same_records_and_acts_on_nothing(self):
        self.cfg["checks"]["t1"] = "shadow"
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        mid = self.submit("CASE-R")   # t1 was live at submit time: the gate holds it
        self.conn.execute("UPDATE inbox SET status='pending', reason=NULL WHERE id=?", (mid,))
        self.conn.commit()
        self.passes()
        self.assertEqual(self.row(mid)["status"], "pending")
        self.assertEqual([(r["stage"], r["mode"], r["action"]) for r in self.recs(mid)],
                         [("t1", "shadow", "none"), ("hunch", "live", "none")])

    def test_hunch_runs_with_six_votes_on_the_stdlib_path(self):   # HUNCH_VOTE=6, through the real backend class
        log = os.path.join(self.tmp.name, "hunch.log")
        self.fake([("CASE-R", "deliver", 0.6)], [("CASE-R", "deliver", 0.7)])
        env = {"PATH": os.path.join(HERE, "fixtures", "bin") + os.pathsep + os.environ["PATH"],
               "ROOKERY_FAKE_HUNCH": self.fake_path, "ROOKERY_FIXTURE_LOG": log, "ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}
        mid = self.submit("CASE-R")
        with mock.patch.dict(os.environ, env):
            os.environ.pop("ROOKERY_SCREENER_FAKE", None)
            self.passes(hunch=SB.hunch_vote(self.cfg))
        with open(log) as fh:
            (call,) = [json.loads(ln) for ln in fh]
        self.assertEqual((call["HUNCH_VOTE"], call["HUNCH_MODEL"]), ("6", "qwen2.5:3b"))
        self.assertEqual(self.row(mid)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
