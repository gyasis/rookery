#!/usr/bin/env python3
"""004 T007: screener_t0.record() and check_decided_by() (FR-010, FR-012, FR-014, FR-033).

    python3 -m unittest test_screen_t0 -v
"""
import json
import os
import subprocess
import sys
import unittest

import screener_t0 as S
from test_hooks import Base

HERE = os.path.dirname(os.path.abspath(__file__))


class Record(Base):
    def rec(self, **kw):
        args = dict(mail_id=1, session_id="s1", stage="t1", check_name="t1", mode="shadow",
                    decided_by="screener:tev1:4b", label="deliver")
        args.update(kw)
        return S.record(self.conn, **args)

    def rows(self):
        self.conn.row_factory = __import__("sqlite3").Row
        return self.conn.execute("SELECT * FROM screen_decisions ORDER BY id").fetchall()

    def log(self):
        p = os.path.join(self.tmp.name, ".rookery", "logs", "gate.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f]

    def test_label_xor_failure(self):
        for kw in ({"label": None}, {"failure": "timeout"}):
            with self.assertRaises(ValueError):
                self.rec(**kw)
        self.assertEqual((self.rows(), self.log()), ([], []))
        self.rec(label=None, failure="timeout")
        self.assertEqual(self.rows()[0]["failure"], "timeout")

    def test_shadow_refuses_action(self):
        with self.assertRaises(ValueError):
            self.rec(action="quarantine")
        self.rec(mode="live", action="quarantine")
        self.assertEqual(self.rows()[0]["action"], "quarantine")

    def test_decided_by_forms(self):
        good = ["t0:injection", "screener:tev1:4b", "hunch:vote6:qwen2.5:3b", "t2:review-cmd", "human:gyasis"]
        bad = ["", "t0:", "screener", "hunch:qwen", "model:x", "human:", None, 5]
        for g in good:
            self.assertTrue(S.check_decided_by(g), g)
        for b in bad:
            self.assertFalse(S.check_decided_by(b), b)
        with self.assertRaises(ValueError):
            self.rec(decided_by="nobody")

    def test_decided_by_names_are_strict(self):
        for b in ["t2: ", "t2:-", "screener:a\x00b", "screener:m\x1b[31m", "human:x y", "human:\u200b",
                  "hunch:vote6:", "hunch:vote6:-x", "screener:a b"]:
            self.assertFalse(S.check_decided_by(b), repr(b))
        for g in ["human:me@host/x", "screener:tev1:4b", "t2:review cmd"]:
            self.assertTrue(S.check_decided_by(g), g)

    def test_bad_values_are_value_errors(self):
        for kw in ({"attempt": 0}, {"attempt": True}, {"attempt": "x"}, {"attempt": 1.5}, {"confidence": 7.5},
                   {"confidence": -0.1}, {"confidence": float("nan")}, {"confidence": True},
                   {"confidence": "x"}, {"label": ""}, {"label": 5}, {"detail": "x" * 501}, {"detail": 5}):
            with self.assertRaises(ValueError, msg=kw):
                self.rec(**kw)
        self.assertEqual((self.rows(), self.log()), ([], []))
        self.rec(confidence=0, detail="x" * 500)
        self.rec(mail_id=2, confidence=1, attempt=2)

    def test_enum_values(self):
        for kw in ({"stage": "t9"}, {"check_name": "x"}, {"mode": "off"}, {"band": "wide"}, {"action": "zap"}):
            with self.assertRaises(ValueError, msg=kw):
                self.rec(**kw)
        self.assertEqual(self.rows(), [])

    def test_usage(self):
        for bad in ({"prompt_tokens": "9"}, {"reasoning_tokens": 1.5}, {"prompt_tokens": True}):
            with self.assertRaises(ValueError):
                self.rec(usage=bad)
        self.rec(usage={"prompt_tokens": 120, "reasoning_tokens": 0}, impl="fake", confidence=0.9, band="accept")
        r = self.rows()[0]
        self.assertEqual((r["prompt_tokens"], r["reasoning_tokens"], r["backend_impl"]), (120, 0, "fake"))
        self.rec(mail_id=2, usage=None)
        self.assertIsNone(self.rows()[1]["prompt_tokens"])

    def test_one_gate_line_per_row(self):
        rid = self.rec(confidence=0.97, impl="fake")
        self.rec(mail_id=None, stage="t0", check_name="thread", decided_by="t0:thread", label="denied",
                 detail="closed")
        self.assertEqual(len(self.rows()), 2)
        lines = self.log()
        self.assertEqual(len(lines), 2)
        first = lines[0]
        self.assertEqual((first["path"], first["session"], first["msg_id"], first["stage"], first["check"]),
                         ("screener", "s1", 1, "t1", "t1"))
        self.assertEqual((first["label"], first["confidence"], first["mode"], first["action"],
                          first["decided_by"], first["failure"]),
                         ("deliver", 0.97, "shadow", "none", "screener:tev1:4b", None))
        self.assertIn("ts", first)
        self.assertEqual(self.conn.execute("SELECT id FROM screen_decisions ORDER BY id").fetchone()[0], rid)

    def test_duplicate_key_writes_no_log_line(self):
        self.rec()
        with self.assertRaises(Exception):
            self.rec()
        self.assertEqual(len(self.log()), 1)

    def test_detail_is_stored_as_given_only(self):
        self.rec(detail="pattern:ignore-previous")
        self.assertEqual(self.rows()[0]["detail"], "pattern:ignore-previous")


class Imports(unittest.TestCase):
    def test_no_heavy_imports(self):
        code = ("import sys, screener_t0; "
                "bad=[m for m in ('screener','screener_backend','promptchain') if m in sys.modules]; "
                "sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
