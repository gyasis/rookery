#!/usr/bin/env python3
"""Three-word session names (research D22, FR-037, SC-015).

    python3 -m unittest test_session_names -v
"""
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
import rookery_names as N

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")
THREE = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")


class _SeqRng:
    """A fake rng: choice() walks a fixed list of words."""

    def __init__(self, words):
        self.words = list(words)

    def choice(self, seq):
        w = self.words.pop(0)
        assert w in seq, w
        return w


class WordLists(unittest.TestCase):
    def test_each_list_has_64_unique_lowercase_ascii_words(self):
        for lst in (N.MOODS, N.COLORS, N.ANIMALS):
            self.assertEqual(len(lst), 64)
            self.assertEqual(len(set(lst)), 64)
            for w in lst:
                self.assertRegex(w, r"^[a-z]+$")

    def test_make_name_has_three_words(self):
        for _ in range(200):
            self.assertRegex(N.make_name(), THREE)


class NewSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = R.connect(os.path.join(self.tmp.name, "t.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_1000_unnamed_sessions_are_distinct_three_word_names(self):
        names = set()
        for _ in range(1000):
            s = RI.new_session(self.conn)
            self.assertRegex(s["name"], THREE)
            self.assertIsNone(RI._ID_LOOKALIKE.fullmatch(s["name"]))
            self.assertRegex(s["session_id"], r"^rs-[0-9a-f]{8}$")
            names.add(s["name"])
        self.assertEqual(len(names), 1000)

    def test_given_name_works_as_before(self):
        s = RI.new_session(self.conn, name="my-session")
        self.assertEqual(s["name"], "my-session")
        with self.assertRaises(ValueError):
            RI.new_session(self.conn, name="my-session")

    def test_clash_makes_it_choose_again(self):
        first = [N.MOODS[0], N.COLORS[0], N.ANIMALS[0]]
        second = [N.MOODS[1], N.COLORS[1], N.ANIMALS[1]]
        a = RI.new_session(self.conn, name="-".join(first))
        rng = _SeqRng(first + second)
        orig = N.secrets
        N.secrets = rng
        try:
            b = RI.new_session(self.conn)
        finally:
            N.secrets = orig
        self.assertNotEqual(b["name"], a["name"])
        self.assertEqual(b["name"], "-".join(second))
        self.assertEqual(rng.words, [])


class Cli(unittest.TestCase):
    def test_session_new_without_name_prints_three_word_name(self):
        with tempfile.TemporaryDirectory() as t:
            home = os.path.join(t, "home")
            os.makedirs(home)
            env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
            env.update(HOME=home, ROOKERY_DB=os.path.join(t, "r.db"),
                       ROOKERY_ROOT=os.path.join(t, "root"))
            r = subprocess.run([sys.executable, CLI, "session", "new", "--json"], env=env,
                               cwd=t, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertRegex(json.loads(r.stdout)["name"], THREE)


if __name__ == "__main__":
    unittest.main()
