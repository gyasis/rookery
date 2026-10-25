#!/usr/bin/env python3
"""004 T021: B2, the ask budget and duplicates (D17, FR-025, FR-026, R18, SC-010).

    python3 -m unittest test_screen_b2 -v

Only block mail of kind `ask` counts. Each ask gets a `queued` screen_mail row with its hash, and T1 still screens it.
Shadow mode records and delivers. Live mode holds the mail (reason `budget` or `duplicate`) and bounces the sender.
Precedence: loop > budget > duplicate > screening. Fake backend only.
"""
import copy
import json
import os
import shutil
import sqlite3
import unittest

import mail_gate
import rookery
import screener
import screener_backend as SB
import screener_config
import screener_t0
import turn_block as TB
from test_hooks import Base
from test_screen_run import FIXTURE, block

BANDS = {"accept": 0.8, "review": 0.5}


class B2(Base):
    def setUp(self):
        super().setUp()
        self.conn.row_factory = sqlite3.Row
        self.sid = self.alpha["session_id"]
        self.fake_path = os.path.join(self.tmp.name, "fake_t1.json")
        shutil.copy(FIXTURE, self.fake_path)
        self.backend = SB.fake(self.fake_path)
        self.addCleanup(os.environ.pop, "ROOKERY_HOLD", None)
        self.config()

    def config(self, budget="shadow", duplicate="shadow", t1="shadow", **extra):
        os.makedirs(os.path.join(self.tmp.name, ".rookery"), exist_ok=True)
        checks = {"budget": budget, "duplicate": duplicate, "t1": t1}
        with open(os.path.join(self.tmp.name, ".rookery", "config.json"), "w") as fh:
            json.dump({"screener": {"bands": {"t1": BANDS}, "checks": checks, **extra}}, fh)
        self.cfg = copy.deepcopy(screener_config.DEFAULTS)
        self.cfg["checks"].update(checks)
        self.cfg["bands"]["t1"] = BANDS
        self.cfg.update(extra)

    def ask(self, say="CASE-DELIVER read the file", to="researcher", task="T-7", principal=None, **kw):
        return mail_gate.submit(self.conn, path="t", principal=principal or (self.alpha, self.swe), raw_sender="x",
                                to=to, body=block(say=say, to=to, task=task, **kw), topic=task, thread=kw.get("thread"),
                                block={"kind": kw.get("kind", "ask"), "say": say, "task": task})

    def row(self, r):
        return rookery.get_message(self.conn, r["msg_id"])

    def recs(self, mid, check):
        return self.conn.execute("SELECT * FROM screen_decisions WHERE mail_id=? AND check_name=? ORDER BY id",
                                 (mid, check)).fetchall()

    def passes(self):
        return screener._screen_session(self.conn, self.sid, "alpha", self.cfg, self.backend, "me:1:x",
                                        rookery.now() - self.cfg["lease_s"], [])

    def set_budget(self, n):
        self.conn.execute("INSERT INTO session_context(session_id, task, acceptance, ask_budget, updated_at) "
                          "VALUES(?,?,?,?,?)", (self.sid, "t", "a", n, "now"))
        self.conn.commit()

    # --- budget
    def test_team_value_wins_over_config_and_fourth_ask_is_over(self):
        self.config(ask_budget=20)
        self.set_budget(3)
        rs = [self.ask(say=f"CASE-DELIVER q{i}") for i in range(4)]
        for r in rs[:3]:
            self.assertEqual(self.recs(r["msg_id"], "budget"), [])
        (rec,) = self.recs(rs[3]["msg_id"], "budget")
        self.assertEqual((rec["label"], rec["detail"], rec["mode"], rec["action"], rec["decided_by"], rec["stage"]),
                         ("over-budget", "4 of 3", "shadow", "none", "t0:budget", "t0"))
        self.assertEqual(self.row(rs[3])["status"], "pending")   # shadow: delivered

    def test_config_limit_without_a_team_value(self):
        self.config(ask_budget=2)
        rs = [self.ask(say=f"CASE-DELIVER q{i}") for i in range(3)]
        self.assertEqual([len(self.recs(r["msg_id"], "budget")) for r in rs], [0, 0, 1])

    def test_budget_counts_one_task_and_only_asks(self):
        self.config(ask_budget=1)
        self.ask(say="CASE-DELIVER a", task="T-1")
        r = self.ask(say="CASE-DELIVER b", task="T-2")
        self.assertEqual(self.recs(r["msg_id"], "budget"), [])
        mail_gate.submit(self.conn, path="t", principal=(self.alpha, self.swe), raw_sender="x", to="researcher",
                         body="plain mail", topic="T-2")
        self.ask(say="CASE-DELIVER c", kind="inform")   # not an ask
        r = self.ask(say="CASE-DELIVER d", task="T-2")
        self.assertEqual(len(self.recs(r["msg_id"], "budget")), 1)

    def test_live_budget_holds_and_bounces(self):
        self.config(budget="live", ask_budget=1)
        self.ask(say="CASE-DELIVER a")
        r = self.ask(say="CASE-DELIVER b")
        row = self.row(r)
        self.assertEqual((r["outcome"], r["reason"], row["status"], row["reason"]), ("sent", "budget", "held", "budget"))
        (rec,) = self.recs(r["msg_id"], "budget")
        self.assertEqual((rec["mode"], rec["action"]), ("live", "hold"))
        bounce = rookery.get_message(self.conn, r["bounce_id"])
        self.assertEqual((bounce["sender"], bounce["recipient"], bounce["body"]),
                         ("rookery", self.swe["agent_id"], screener_t0.BUDGET_BOUNCE.format(task="T-7", limit=1)))

    # --- duplicate
    def test_duplicate_inside_the_window_not_after_and_not_to_another_recipient(self):
        first = self.ask(say="CASE-DELIVER Read  the FILE?")
        r = self.ask(say="  case-deliver read the file ")
        (rec,) = self.recs(r["msg_id"], "duplicate")
        self.assertEqual((rec["label"], rec["mode"], rec["action"], rec["decided_by"]),
                         ("duplicate", "shadow", "none", "t0:duplicate"))
        self.assertEqual(rec["detail"], screener_t0.ask_hash("CASE-DELIVER read the file")[:12])
        self.assertEqual(self.row(r)["status"], "pending")
        h = self.conn.execute("SELECT ask_hash FROM screen_mail WHERE mail_id=?", (first["msg_id"],)).fetchone()[0]
        self.assertEqual(h, screener_t0.ask_hash("case deliver read the file"))
        self.assertEqual(len(h), 64)
        self.assertEqual(self.recs(self.ask(say="CASE-DELIVER read the file", to="me")["msg_id"], "duplicate"), [])
        self.conn.execute("UPDATE inbox SET created_at=created_at-7200")   # older than duplicate_window_s (3600)
        self.conn.commit()
        self.assertEqual(self.recs(self.ask(say="CASE-DELIVER read the file")["msg_id"], "duplicate"), [])

    def test_other_sender_is_not_a_duplicate(self):
        self.ask()
        r = self.ask(principal=(self.alpha, self.me))
        self.assertEqual(self.recs(r["msg_id"], "duplicate"), [])

    def test_live_duplicate_holds_and_bounces(self):
        self.config(duplicate="live")
        self.ask()
        r = self.ask()
        row = self.row(r)
        self.assertEqual((r["reason"], row["status"], row["reason"]), ("duplicate", "held", "duplicate"))
        (rec,) = self.recs(r["msg_id"], "duplicate")
        self.assertEqual((rec["mode"], rec["action"]), ("live", "hold"))
        body = rookery.get_message(self.conn, r["bounce_id"])["body"]
        self.assertTrue(body.startswith("Rookery held this ask. You sent the same question to researcher at 20"), body)
        self.assertTrue(body.endswith(" UTC. Wait for that answer."), body)

    def test_budget_beats_duplicate(self):
        self.config(budget="live", duplicate="live", ask_budget=1)
        self.ask()
        r = self.ask()
        self.assertEqual((self.row(r)["reason"], len(self.recs(r["msg_id"], "budget")),
                          len(self.recs(r["msg_id"], "duplicate"))), ("budget", 1, 1))
        self.assertIn("budget of 1 asks", rookery.get_message(self.conn, r["bounce_id"])["body"])

    def test_checks_off_write_nothing_but_the_hash(self):
        self.config(budget="off", duplicate="off", ask_budget=1)
        r = [self.ask() for _ in range(3)]
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_decisions").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM screen_mail WHERE ask_hash IS NOT NULL").fetchone()[0], 3)
        self.assertTrue(all(self.row(x)["status"] == "pending" for x in r))

    # --- the loop breaker comes first (R18, SC-010)
    def test_ping_pong_stops_at_the_loop_limit(self):
        self.config(budget="live", duplicate="live")
        limit, _ = TB.loop_limits()
        self.assertLess(limit, self.cfg["ask_budget"])
        sides = [(self.alpha, self.swe, "researcher"), (self.alpha, self.researcher, "swe")]
        last = None
        for i in range(limit + 1):
            p = sides[i % 2]
            last = self.ask(say=f"CASE-DELIVER ping {i}", to=p[2], principal=(p[0], p[1]))
            if i < limit:
                self.assertEqual(last["outcome"], "sent", i)
        self.assertEqual((last["outcome"], last["reason"], last["bounce_id"]), ("loop-held", "loop", None))
        row = self.row(last)
        self.assertEqual((row["status"], row["reason"]), ("held", "loop"))
        self.assertEqual(self.recs(last["msg_id"], "budget") + self.recs(last["msg_id"], "duplicate"), [])

    def test_loop_hold_beats_a_duplicate(self):
        self.config(duplicate="live")
        limit, _ = TB.loop_limits()
        for i in range(limit):
            self.ask(say=f"CASE-DELIVER x {i}")
        r = self.ask(say="CASE-DELIVER x 0")
        self.assertEqual((r["outcome"], self.row(r)["reason"], r["bounce_id"]), ("loop-held", "loop", None))

    # --- the screener
    def test_ask_with_a_stored_hash_is_still_screened(self):
        r = self.ask()
        st = self.conn.execute("SELECT state, ask_hash FROM screen_mail WHERE mail_id=?", (r["msg_id"],)).fetchone()
        self.assertEqual(st["state"], "queued")
        self.assertEqual(self.passes(), 1)
        st = self.conn.execute("SELECT state, ask_hash FROM screen_mail WHERE mail_id=?", (r["msg_id"],)).fetchone()
        self.assertEqual(st["state"], "decided")
        self.assertTrue(st["ask_hash"])
        self.assertTrue(self.recs(r["msg_id"], "t1"))
        self.assertEqual(self.passes(), 0)

    def test_held_for_budget_or_duplicate_is_not_acted_on_by_the_live_screener(self):
        self.config(budget="live", duplicate="live", t1="live", ask_budget=2)
        os.environ.pop("ROOKERY_HOLD", None)
        a = self.ask(say="CASE-DELIVER a")   # held as screening (t1 live, review mode pre)
        self.ask(say="CASE-DELIVER b")
        over = self.ask(say="CASE-DELIVER c")
        dup = self.ask(say="CASE-DELIVER a")
        self.assertEqual((self.row(over)["reason"], self.row(dup)["reason"], self.row(a)["reason"]),
                         ("budget", "budget", "screening"))   # budget beats duplicate
        self.config(budget="live", duplicate="live", t1="live", ask_budget=10)
        self.conn.execute("UPDATE inbox SET reason='duplicate' WHERE id=?", (dup["msg_id"],))
        self.conn.commit()
        self.passes()
        for r in (over, dup):
            row = self.row(r)
            self.assertEqual(row["status"], "held")
            self.assertIn(row["reason"], ("budget", "duplicate"))
            self.assertTrue(all(x["mode"] == "shadow" and x["action"] == "none" for x in self.recs(r["msg_id"], "t1")))

    def test_duplicate_ask_on_an_approved_thread_gets_a_full_t1(self):
        self.config(t1="live")
        first = self.ask(say="CASE-DELIVER go", thread="th-1")["msg_id"]
        self.assertEqual(self.passes(), 1)
        self.assertEqual(self.conn.execute("SELECT status FROM threads").fetchone()[0], "approved")
        again = self.ask(say="case-deliver GO", thread="th-1")["msg_id"]
        self.assertEqual(len(self.recs(again, "duplicate")), 1)
        self.assertEqual(self.passes(), 1)
        self.assertNotIn("skipped", [r["label"] for r in self.recs(again, "t1")])
        self.assertTrue(self.recs(again, "t1"))
        self.assertNotEqual(first, again)

    # --- audit fixes (wave 13/14)
    def test_say_with_no_letters_is_never_a_duplicate(self):
        self.config(duplicate="live")
        for say in ("???", "!!!", "\U0001F600", "???"):
            r = self.ask(say=say)
            self.assertEqual((self.row(r)["status"], self.recs(r["msg_id"], "duplicate")), ("pending", []), say)

    def test_stored_budget_of_zero_is_honoured(self):
        self.config(budget="live", ask_budget=5)
        self.set_budget(0)
        self.assertEqual(self.ask()["reason"], "budget")

    def test_stored_budget_of_one_beats_config_of_two(self):
        self.config(ask_budget=2)
        self.set_budget(1)
        rs = [self.ask(say=f"CASE-DELIVER q{i}") for i in range(2)]
        self.assertEqual([len(self.recs(r["msg_id"], "budget")) for r in rs], [0, 1])

    def test_held_retries_do_not_eat_budget_or_refresh_the_window(self):
        self.config(budget="live", duplicate="live", ask_budget=3)
        rs = [self.ask(say="CASE-DELIVER same") for _ in range(5)]
        self.assertEqual([self.row(r)["reason"] for r in rs], [None] + ["duplicate"] * 4)
        self.assertEqual([len(self.recs(r["msg_id"], "budget")) for r in rs], [0] * 5)
        # age the first ask: held retries (newer) must not keep the window open
        self.conn.execute("UPDATE inbox SET created_at=created_at-3000 WHERE id=?", (rs[0]["msg_id"],))
        self.conn.execute("UPDATE inbox SET created_at=created_at-3000 WHERE id>?", (rs[0]["msg_id"],))
        self.conn.execute("UPDATE inbox SET created_at=created_at-1000 WHERE id=?", (rs[0]["msg_id"],))
        self.conn.commit()
        self.assertEqual(self.ask(say="CASE-DELIVER same")["reason"], None)

    def test_duplicate_bounce_names_the_original_time(self):
        self.config(duplicate="live")
        first = self.ask(say="CASE-DELIVER again")
        self.conn.execute("UPDATE inbox SET created_at=created_at-600 WHERE id=?", (first["msg_id"],))
        self.conn.commit()
        t0 = rookery.get_message(self.conn, first["msg_id"])["created_at"]
        second = self.ask(say="CASE-DELIVER again")   # held as a duplicate, then a person releases it
        self.conn.execute("UPDATE inbox SET status='pending', reason=NULL WHERE id=?", (second["msg_id"],))
        self.conn.commit()
        r = self.ask(say="CASE-DELIVER again")
        body = rookery.get_message(self.conn, r["bounce_id"])["body"]
        self.assertIn(screener_t0.duplicate_time(t0), body)
        self.assertNotEqual(t0, rookery.get_message(self.conn, second["msg_id"])["created_at"])


if __name__ == "__main__":
    unittest.main()
