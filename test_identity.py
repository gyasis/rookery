#!/usr/bin/env python3
"""Rookery sessions, agent identities, bindings and resolve() (research D17 to D20).

Part 1 (T009): new_session, get_session, end_session, new_identity, list_identities.
Part 2 (T010): resolve(conn, env, hook_input, root, create=True)
               -> None | (session_row, identity_row, bindings).

    python3 -m unittest test_identity -v
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

import rookery as R
import rookery_identity as RI


class IdentityBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        # load_config() reads ~/.rookery/config.json: point HOME at the temp dir.
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        self.conn = R.connect(os.path.join(self.tmp.name, "t.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    # helpers
    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def snapshot(self):
        return {t: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {t} ORDER BY 1,2,3")]
                for t in ("rookery_sessions", "identities", "bindings")}

    def log_text(self):
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        return open(p).read() if os.path.exists(p) else ""

    def resolve(self, env, hook_input=None, create=True):
        return RI.resolve(self.conn, env, hook_input or {}, self.root, create=create)

    def holder(self, kind, value, session_id):
        r = self.conn.execute(
            "SELECT owner_id FROM bindings WHERE session_id=? AND owner_type='identity' "
            "AND kind=? AND value=?", (session_id, kind, value)).fetchone()
        return r["owner_id"] if r else None


class Ids(IdentityBase):
    def test_session_id_format(self):
        s = RI.new_session(self.conn, name="alpha")
        self.assertRegex(s["session_id"], r"^rs-[0-9a-f]{8}$")

    def test_agent_id_format(self):
        s = RI.new_session(self.conn, name="alpha")
        i = RI.new_identity(self.conn, s["session_id"], "researcher")
        self.assertRegex(i["agent_id"], r"^ag-[0-9a-f]{10}$")

    def test_no_id_repeats_in_1000_creations(self):
        sids, aids = set(), set()
        s = RI.new_session(self.conn, name="alpha")
        for n in range(1000):
            sids.add(RI.new_session(self.conn)["session_id"])
            aids.add(RI.new_identity(self.conn, s["session_id"], f"a{n}")["agent_id"])
        self.assertEqual((len(sids), len(aids)), (1000, 1000))


class Names(IdentityBase):
    def test_name_repeats_across_sessions(self):
        a = RI.new_session(self.conn, name="alpha")
        b = RI.new_session(self.conn, name="beta")
        i1 = RI.new_identity(self.conn, a["session_id"], "researcher")
        i2 = RI.new_identity(self.conn, b["session_id"], "researcher")
        self.assertNotEqual(i1["agent_id"], i2["agent_id"])

    def test_name_unique_inside_session(self):
        a = RI.new_session(self.conn, name="alpha")
        RI.new_identity(self.conn, a["session_id"], "researcher")
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            RI.new_identity(self.conn, a["session_id"], "researcher")
        self.assertEqual(self.count("identities"), 1)

    def test_system_sender_names_refused(self):
        a = RI.new_session(self.conn, name="alpha")
        for name in sorted(R.SYSTEM_SENDERS) + ["Rookery", "POSTMASTER"]:
            with self.assertRaises(ValueError, msg=name) as cm:
                RI.new_identity(self.conn, a["session_id"], name)
            self.assertIn("system sender", str(cm.exception))
        self.assertEqual(self.count("identities"), 0)

    def test_unsafe_names_rejected(self):
        a = RI.new_session(self.conn, name="alpha")
        for bad in ("..", "a/b", "x" * 65):
            with self.assertRaises(ValueError, msg=bad):
                RI.new_identity(self.conn, a["session_id"], bad)
            with self.assertRaises(ValueError, msg=bad):
                RI.new_session(self.conn, name=bad)
        self.assertEqual(self.count("identities"), 0)

    def test_id_lookalike_names_rejected(self):          # audit 6, fix 5a
        a = RI.new_session(self.conn, name="alpha")
        i = RI.new_identity(self.conn, a["session_id"], "researcher")
        for bad in (i["agent_id"], "ag-0123456789"):
            with self.assertRaises(ValueError, msg=bad):
                RI.new_identity(self.conn, a["session_id"], bad)
        for bad in (a["session_id"], "rs-01234567"):
            with self.assertRaises(ValueError, msg=bad):
                RI.new_session(self.conn, name=bad)
        self.assertEqual(self.count("identities"), 1)
        RI.new_identity(self.conn, a["session_id"], "ag-short")   # not ID-shaped: fine

    def test_safe_name_boundary_64_accepted(self):
        a = RI.new_session(self.conn, name="alpha")
        RI.new_identity(self.conn, a["session_id"], "x" * 64)

    def test_session_without_name_gets_its_id(self):
        s = RI.new_session(self.conn)
        self.assertEqual(s["name"], s["session_id"])
        self.assertEqual(RI.get_session(self.conn, s["name"])["session_id"], s["session_id"])

    def test_ended_session_name_not_reusable(self):
        s = RI.new_session(self.conn, name="alpha")
        RI.end_session(self.conn, "alpha")
        self.assertEqual(RI.get_session(self.conn, s["session_id"])["state"], "ended")
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            RI.new_session(self.conn, name="alpha")


class ResolveSession(IdentityBase):
    def test_no_session_returns_none_and_changes_nothing(self):
        RI.new_session(self.conn, name="alpha")
        before = self.snapshot()
        self.assertIsNone(self.resolve({"HERDR_PANE_ID": "p1"}))
        self.assertIsNone(self.resolve({"HERDR_PANE_ID": "p1"}, {"session_id": "c1"}))
        self.assertEqual(self.snapshot(), before)

    def test_rookery_session_by_name_and_by_id(self):
        s = RI.new_session(self.conn, name="alpha")
        by_name = self.resolve({"ROOKERY_SESSION": "alpha", "HERDR_PANE_ID": "p1"})
        by_id = self.resolve({"ROOKERY_SESSION": s["session_id"], "HERDR_PANE_ID": "p1"})
        self.assertEqual(by_name[0]["session_id"], s["session_id"])
        self.assertEqual(by_id[0]["session_id"], s["session_id"])
        self.assertEqual(by_name[1]["agent_id"], by_id[1]["agent_id"])

    def test_session_binding_selects_session(self):
        s = RI.new_session(self.conn, name="alpha", binds=("herdr-workspace:w1",))
        RI.new_session(self.conn, name="beta", binds=("herdr-workspace:w2",))
        r = self.resolve({"HERDR_WORKSPACE_ID": "w1", "HERDR_PANE_ID": "p1"})
        self.assertEqual(r[0]["session_id"], s["session_id"])
        self.assertEqual(r[1]["session_id"], s["session_id"])


class ResolveIdentity(IdentityBase):
    def setUp(self):
        super().setUp()
        self.s = RI.new_session(self.conn, name="alpha")
        self.sid = self.s["session_id"]
        self.env = {"ROOKERY_SESSION": "alpha"}

    def test_two_panes_get_two_identities(self):
        r1 = self.resolve({**self.env, "HERDR_PANE_ID": "p1"}, {"session_id": "c1"})
        r2 = self.resolve({**self.env, "HERDR_PANE_ID": "p2"}, {"session_id": "c2"})
        self.assertNotEqual(r1[1]["agent_id"], r2[1]["agent_id"])
        self.assertEqual(self.count("identities"), 2)

    def test_same_pane_rejoins_same_identity(self):
        r1 = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})
        r2 = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})
        self.assertEqual(r1[1]["agent_id"], r2[1]["agent_id"])
        self.assertEqual(self.count("identities"), 1)

    def test_same_pane_new_session_new_identity_and_no_old_mail(self):
        r1 = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})
        old = r1[1]["agent_id"]
        R.send(self.conn, "someone", old, "old mail", "TASK-1")
        RI.new_session(self.conn, name="beta")
        r2 = RI.resolve(self.conn, {"ROOKERY_SESSION": "beta", "HERDR_PANE_ID": "p1"},
                        {}, self.root, create=True)
        new = r2[1]["agent_id"]
        self.assertNotEqual(old, new)
        self.assertEqual(R.fetch_undelivered(self.conn, new), [])
        self.assertEqual(len(R.fetch_undelivered(self.conn, old)), 1)

    def test_rookery_node_new_name_creates_identity(self):
        r = self.resolve({**self.env, "ROOKERY_NODE": "researcher", "HERDR_PANE_ID": "p1"})
        self.assertEqual(r[1]["name"], "researcher")
        self.assertEqual(r[1]["session_id"], self.sid)
        self.assertEqual(self.holder("herdr-pane", "p1", self.sid), r[1]["agent_id"])

    def test_rookery_node_existing_name_and_id(self):
        i = RI.new_identity(self.conn, self.sid, "researcher")
        by_name = self.resolve({**self.env, "ROOKERY_NODE": "researcher"})
        by_id = self.resolve({**self.env, "ROOKERY_NODE": i["agent_id"]})
        self.assertEqual(by_name[1]["agent_id"], i["agent_id"])
        self.assertEqual(by_id[1]["agent_id"], i["agent_id"])
        self.assertEqual(self.count("identities"), 1)

    def test_unknown_node_id_logs_and_continues_with_bindings(self):
        first = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})
        r = self.resolve({**self.env, "ROOKERY_NODE": "ag-0000000000", "HERDR_PANE_ID": "p1"})
        self.assertEqual(r[1]["agent_id"], first[1]["agent_id"])
        self.assertIn("ag-0000000000", self.log_text())
        self.assertEqual(self.count("identities"), 1)

    def test_binding_moves_to_node_identity_and_is_logged(self):
        a = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})[1]
        b = RI.new_identity(self.conn, self.sid, "reviewer")
        r = self.resolve({**self.env, "ROOKERY_NODE": "reviewer", "HERDR_PANE_ID": "p1"})
        self.assertEqual(r[1]["agent_id"], b["agent_id"])
        self.assertEqual(self.holder("herdr-pane", "p1", self.sid), b["agent_id"])
        self.assertNotEqual(a["agent_id"], b["agent_id"])
        self.assertIn("binding-moved", self.log_text())

    def test_without_node_agent_joins_identity_holding_binding(self):
        b = RI.new_identity(self.conn, self.sid, "reviewer")
        self.resolve({**self.env, "ROOKERY_NODE": "reviewer", "HERDR_PANE_ID": "p1"})
        r = self.resolve({**self.env, "HERDR_PANE_ID": "p1"})
        self.assertEqual(r[1]["agent_id"], b["agent_id"])
        self.assertEqual(self.count("identities"), 1)

    def test_new_identity_default_name_and_all_bindings(self):
        r = self.resolve({**self.env, "HERDR_PANE_ID": "p1", "TMUX_PANE": "%3"},
                         {"session_id": "claude-abc"})
        self.assertRegex(r[1]["name"], r"^[A-Za-z0-9._-]+-[0-9a-f]{4}$")
        agent = r[1]["agent_id"]
        self.assertEqual(self.holder("herdr-pane", "p1", self.sid), agent)
        self.assertEqual(self.holder("tmux-pane", "%3", self.sid), agent)
        self.assertEqual(self.holder("claude-session", "claude-abc", self.sid), agent)


class ReadOnly(IdentityBase):
    def home_tree(self):
        return sorted(os.path.join(d, f) for d, _, fs in os.walk(self.tmp.name) for f in fs
                      if not f.startswith("t.db"))

    def test_no_session_and_create_false_do_not_write_home(self):
        cfg = os.path.join(self.tmp.name, ".rookery", "config.json")
        RI.new_session(self.conn, name="alpha")
        self.assertIsNone(self.resolve({"HERDR_PANE_ID": "p1"}))
        self.assertFalse(os.path.exists(cfg))
        r = self.resolve({"ROOKERY_SESSION": "alpha", "ROOKERY_NODE": "bob"}, create=False)
        self.assertIsNone(r[1])
        self.assertFalse(os.path.exists(cfg))
        self.assertIn("no-session", self.log_text())

    def test_unknown_session_logs_one_line_only(self):
        self.assertIsNone(self.resolve({"ROOKERY_SESSION": "nope"}))
        lines = [json.loads(x) for x in self.log_text().splitlines()]
        self.assertEqual([x["event"] for x in lines], ["unknown-session"])

    def test_load_config_persist_false_returns_default_without_writing(self):
        cfg = RI.load_config(persist=False)
        self.assertIn("identity_binding_sources", cfg)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, ".rookery")))
        RI.load_config()
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".rookery", "config.json")))

    def test_create_false_creates_nothing_and_moves_nothing(self):
        s = RI.new_session(self.conn, name="alpha")
        tree = self.home_tree()
        env = {"ROOKERY_SESSION": "alpha", "HERDR_PANE_ID": "p1"}
        # no identity yet: nothing is created
        before = self.snapshot()
        r = RI.resolve(self.conn, env, {"session_id": "c1"}, self.root, create=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(r[0]["session_id"], s["session_id"])
        self.assertIsNone(r[1])
        # an identity holds the binding; ROOKERY_NODE names another: no move
        a = RI.resolve(self.conn, env, {}, self.root, create=True)[1]
        RI.new_identity(self.conn, s["session_id"], "reviewer")
        before = self.snapshot()
        RI.resolve(self.conn, {**env, "ROOKERY_NODE": "reviewer"}, {}, self.root, create=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.holder("herdr-pane", "p1", s["session_id"]), a["agent_id"])
        self.assertNotIn("binding-moved", self.log_text())
        self.assertEqual([f for f in self.home_tree() if "turn-block.jsonl" not in f
                          and not f.endswith("-wal") and not f.endswith("-shm")],
                         [f for f in tree if "turn-block.jsonl" not in f])


class ConcurrentResolve(unittest.TestCase):
    """Fix 3: concurrent resolve(create=True) converges instead of raising."""

    WORKER = (
        "import sys, time, json, os\n"
        "import turn_block, rookery_identity as ri\n"
        "time.sleep(max(0, float(sys.argv[1]) - time.time()))\n"
        "try:\n"
        "    c = turn_block.open_db()\n"
        "    ri.resolve(c, {'ROOKERY_SESSION': 's', 'ROOKERY_NODE': 'bob',\n"
        "                   'HERDR_PANE_ID': 'p9'}, {'session_id': 'cs'},\n"
        "               os.environ['ROOKERY_ROOT'])\n"
        "except Exception as e:\n"
        "    print(type(e).__name__ + ': ' + str(e)); sys.exit(1)\n")

    def test_same_node_and_pane_converge(self):
        import subprocess
        import time
        import turn_block
        here = os.path.dirname(os.path.abspath(__file__))
        for rnd in range(6):
            with tempfile.TemporaryDirectory() as d:
                env = dict(os.environ, HOME=d, ROOKERY_DB=os.path.join(d, "t.db"),
                           ROOKERY_ROOT=os.path.join(d, "root"))
                old = os.environ.get("ROOKERY_DB")
                os.environ["ROOKERY_DB"] = env["ROOKERY_DB"]
                try:
                    c = turn_block.open_db()
                finally:
                    if old is None:
                        os.environ.pop("ROOKERY_DB")
                    else:
                        os.environ["ROOKERY_DB"] = old
                sid = RI.new_session(c, name="s")["session_id"]
                go = str(time.time() + 1.0)
                procs = [subprocess.Popen([sys.executable, "-c", self.WORKER, go], cwd=here,
                                          env=env, stdout=subprocess.PIPE, text=True)
                         for _ in range(4)]
                outs = [p.communicate()[0] for p in procs]
                self.assertEqual([o for o in outs if o], [], f"round {rnd}")
                ids = c.execute("SELECT agent_id FROM identities WHERE name='bob'").fetchall()
                self.assertEqual(len(ids), 1)
                holder = c.execute(
                    "SELECT owner_id FROM bindings WHERE session_id=? AND owner_type='identity' "
                    "AND kind='herdr-pane' AND value='p9'", (sid,)).fetchall()
                self.assertEqual([r[0] for r in holder], [ids[0][0]])
                c.close()


class LogHandlerRecreated(unittest.TestCase):
    """Fix 7: deleting the log file must not lose later events."""

    def test_log_after_external_delete(self):
        import turn_block
        with tempfile.TemporaryDirectory() as d:
            turn_block.log_event(d, event="one")
            path = os.path.join(d, "logs", "turn-block.jsonl")
            self.assertTrue(os.path.exists(path))
            os.remove(path)
            turn_block.log_event(d, event="two")
            self.assertTrue(os.path.exists(path))
            self.assertIn('"two"', open(path).read())


class OpenDbReadOnly(unittest.TestCase):
    """Fix 2 (research D16): open_db on a current schema must not need the write lock."""

    def test_open_db_while_writer_holds_lock(self):
        import time
        import turn_block
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            old = os.environ.get("ROOKERY_DB")
            os.environ["ROOKERY_DB"] = path
            try:
                turn_block.open_db().close()          # migrate
                w = sqlite3.connect(path, isolation_level=None)
                w.execute("BEGIN IMMEDIATE")
                try:
                    t = time.time()
                    conn = turn_block.open_db()
                    self.assertLess(time.time() - t, 1.0)
                    self.assertEqual(conn.execute(
                        "SELECT COUNT(*) FROM rookery_sessions").fetchone()[0], 1)
                    conn.close()
                finally:
                    w.execute("ROLLBACK")
                    w.close()
            finally:
                if old is None:
                    os.environ.pop("ROOKERY_DB", None)
                else:
                    os.environ["ROOKERY_DB"] = old


class CliSessionIdentity(unittest.TestCase):
    """T012: `rookery_cli.py session|identity ...` as subprocesses (temp DB/HOME/ROOT only)."""

    CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rookery_cli.py")

    def setUp(self):
        import subprocess  # noqa: F401
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "cli.db")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("ROOKERY_")}
        self.env.update(ROOKERY_DB=self.db, HOME=self.tmp.name,
                        ROOKERY_ROOT=os.path.join(self.tmp.name, "root"))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, env=None, code=0):
        import subprocess
        r = subprocess.run([sys.executable, self.CLI, *args], env=env or self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return json.loads(r.stdout) if code == 0 else r

    def count(self, table, sid):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (sid,)).fetchone()[0]
        finally:
            c.close()

    def test_session_new_list_show_end(self):
        n = self.cli("session", "new", "--name", "alpha", "--json")
        self.assertEqual(n["name"], "alpha")
        self.assertTrue(n["session_id"])
        lst = self.cli("session", "list", "--json")
        self.assertIn("alpha", [s["name"] for s in lst["sessions"]])
        shown = self.cli("session", "show", "alpha", "--json")
        self.assertEqual(shown["session"]["session_id"], n["session_id"])
        self.assertEqual(shown["session"]["state"], "open")
        ended = self.cli("session", "end", "alpha", "--json")
        self.assertEqual(ended["state"], "ended")

    def test_identity_new_system_name_exits_2(self):
        n = self.cli("session", "new", "--name", "alpha", "--json")
        for name in sorted(R.SYSTEM_SENDERS):
            r = self.cli("identity", "new", name, "--session", "alpha", code=2)
            self.assertIn("system sender", r.stderr)
            self.assertEqual(r.stdout, "")
        self.assertEqual(self.count("identities", n["session_id"]), 0)

    def test_session_end_deletes_nothing(self):
        sid = self.cli("session", "new", "--name", "alpha", "--json")["session_id"]
        ident = self.cli("identity", "new", "bob", "--session", "alpha", "--json")
        env = dict(self.env, ROOKERY_SESSION="alpha", ROOKERY_NODE="bob")
        self.cli("identity", "bind", "herdr-pane:p1", "--json", env=env)
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO inbox(sender, recipient, body, status, created_at, session_id) "
                  "VALUES ('x', ?, 'hi', 'pending', 1, ?)", (ident["agent_id"], sid))
        c.commit()
        c.close()
        before = {t: self.count(t, sid) for t in ("identities", "bindings", "inbox")}
        self.assertTrue(all(v >= 1 for v in before.values()), before)
        self.assertEqual(self.cli("session", "end", "alpha", "--json")["state"], "ended")
        after = {t: self.count(t, sid) for t in ("identities", "bindings", "inbox")}
        self.assertEqual(before, after)
        self.assertEqual(self.cli("session", "show", "alpha", "--json")["session"]["state"], "ended")

    def test_identity_new_bind_show_list(self):
        self.cli("session", "new", "--name", "alpha", "--json")
        a = self.cli("identity", "new", "bob", "--session", "alpha", "--json")
        self.assertEqual((a["name"], a["session"]), ("bob", "alpha"))
        env = dict(self.env, ROOKERY_SESSION="alpha", ROOKERY_NODE="bob")
        bound = self.cli("identity", "bind", "herdr-pane:p1", "--json", env=env)
        self.assertIn("herdr-pane", json.dumps(bound))
        me = self.cli("identity", "show", "--json", env=env)
        self.assertEqual((me["agent_id"], me["name"]), (a["agent_id"], "bob"))
        lst = self.cli("identity", "list", "--session", "alpha", "--json")
        self.assertEqual([i["name"] for i in lst["identities"]], ["bob"])

    def test_identity_bind_moves_binding(self):
        self.cli("session", "new", "--name", "alpha", "--json")
        self.cli("identity", "new", "bob", "--session", "alpha", "--json")
        alice = self.cli("identity", "new", "alice", "--session", "alpha", "--json")
        e = dict(self.env, ROOKERY_SESSION="alpha")
        self.cli("identity", "bind", "p:1", "--json", env=dict(e, ROOKERY_NODE="bob"))
        out = self.cli("identity", "bind", "p:1", "--json", env=dict(e, ROOKERY_NODE="alice"))
        self.assertEqual(out["result"], "moved")
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT owner_id FROM bindings WHERE owner_type='identity' "
                         "AND kind='p' AND value='1'").fetchall()
        c.close()
        self.assertEqual([r[0] for r in rows], [alice["agent_id"]])

    def test_identity_list_uses_session_binding(self):
        self.cli("session", "new", "--name", "ws", "--bind", "herdr-workspace:w1", "--json")
        env = dict(self.env, HERDR_WORKSPACE_ID="w1")
        out = self.cli("identity", "list", "--json", env=env)
        self.assertEqual(out["session"], "ws")

    def test_duplicate_bind_reported_as_binding(self):
        r = self.cli("session", "new", "--name", "ws", "--bind", "a:b", "--bind", "a:b", code=2)
        self.assertIn("binding", r.stderr.lower())
        self.assertNotIn("name already exists", r.stderr)

    def test_unopenable_db_is_not_reported_busy(self):
        env = dict(self.env, ROOKERY_DB=os.path.join(self.tmp.name, "nodir", "x.db"))
        r = self.cli("session", "list", "--json", env=env, code=2)
        self.assertIn("cannot open database", r.stderr)

    def test_help_lists_old_and_new_commands(self):
        import subprocess
        r = subprocess.run([sys.executable, self.CLI, "--help"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        for w in ("serve", "up", "approve", "session", "identity"):
            self.assertIn(w, r.stdout)


class CarryOver(unittest.TestCase):
    """T023 (FR-036, RQ-1.26): `session new --from` and `identity link --previous`.

    Assumption: the carry-over log line is any line in <root>/logs/turn-block.jsonl
    that mentions the new session (id or name) and the copied path/task; the event
    name is not checked."""

    CLI = CliSessionIdentity.CLI

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "co.db")
        self.root = os.path.join(self.tmp.name, "root")
        os.makedirs(self.root)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        self.env.update(ROOKERY_DB=self.db, HOME=self.tmp.name, ROOKERY_ROOT=self.root)

        self.alpha = self.cli("session", "new", "--name", "alpha", "--json")
        self.me = self.cli("identity", "new", "me", "--session", "alpha", "--json")
        self.law = self.cli("identity", "new", "law-counsel", "--session", "alpha", "--json")
        aenv = dict(self.env, ROOKERY_SESSION="alpha", ROOKERY_NODE="me")
        self.cli("identity", "bind", "herdr-pane:p1", "--json", env=aenv)

        base = os.path.join(self.root, "agents", "alpha")
        self.write(base, "me/runs/TASK-1/HANDOFF.md", "old handoff", mtime=1000000)
        self.write(base, "me/runs/TASK-2/HANDOFF.md", "new handoff", mtime=2000000)
        self.write(base, "law-counsel/runs/TASK-3/HANDOFF.md", "law handoff", mtime=1500000)
        self.write(base, "me/state/turn.json", "{}")

        import rookery as R
        c = R.connect(self.db)
        R.send(c, "x", self.me["agent_id"], "hello", hold=False,
               session_id=self.alpha["session_id"])
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, base, rel, text, mtime=None):
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        if mtime:
            os.utime(path, (mtime, mtime))

    def cli(self, *args, env=None, code=0):
        import subprocess
        r = subprocess.run([sys.executable, self.CLI, *args], env=env or self.env,
                           cwd=self.tmp.name, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return json.loads(r.stdout) if code == 0 else r

    def q(self, sql, *a):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def inbox_counts(self):
        return dict(self.q("SELECT session_id, COUNT(*) FROM inbox GROUP BY session_id"))

    def beta(self):
        return self.cli("session", "new", "--name", "beta", "--from", "alpha", "--json")

    def idents(self, sid):
        return {r[0]: r for r in self.q(
            "SELECT name, agent_id, previous_id FROM identities WHERE session_id=?", sid)}

    def test_from_creates_identity_per_name_with_previous_id(self):
        b = self.beta()
        ids = self.idents(b["session_id"])
        self.assertEqual(set(ids), {"me", "law-counsel"})
        self.assertEqual(ids["me"][2], self.me["agent_id"])
        self.assertEqual(ids["law-counsel"][2], self.law["agent_id"])

    def test_from_copies_newest_handoff_only_and_reports_copied(self):
        b = self.beta()
        base = os.path.join(self.root, "agents", "beta")
        got = os.path.join(base, "me", "runs", "TASK-2", "HANDOFF.md")
        self.assertTrue(os.path.isfile(got))
        with open(got) as f:
            self.assertEqual(f.read(), "new handoff")
        self.assertFalse(os.path.exists(os.path.join(base, "me", "runs", "TASK-1")))
        self.assertEqual(open(os.path.join(base, "law-counsel", "runs", "TASK-3",
                                           "HANDOFF.md")).read(), "law handoff")
        self.assertEqual(len(b["copied"]), 2, b)
        blob = json.dumps(b["copied"])
        self.assertIn("TASK-2", blob)
        self.assertIn("TASK-3", blob)
        self.assertNotIn("TASK-1", blob)

    def test_from_logs_each_copy(self):
        b = self.beta()
        p = os.path.join(self.root, "logs", "turn-block.jsonl")
        self.assertTrue(os.path.exists(p))
        with open(p) as f:
            lines = [ln for ln in f if b["session_id"] in ln or '"beta"' in ln]
        for task in ("TASK-2", "TASK-3"):
            self.assertEqual(len([ln for ln in lines if task in ln]), 1, (task, lines))
        self.assertFalse([ln for ln in lines if "TASK-1" in ln])

    def test_from_copies_no_mail_state_or_binding(self):
        before = self.inbox_counts()
        b = self.beta()
        sid = b["session_id"]
        self.assertEqual(self.inbox_counts(), before)
        self.assertNotIn(sid, before)
        base = os.path.join(self.root, "agents", "beta")
        for name in os.listdir(base):
            self.assertFalse(os.path.exists(os.path.join(base, name, "state")), name)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bindings WHERE session_id=? "
                                "AND owner_type='identity'", sid)[0][0], 0)
        # the old session keeps its binding
        self.assertEqual(self.q("SELECT COUNT(*) FROM bindings WHERE session_id=? "
                                "AND owner_type='identity'", self.alpha["session_id"])[0][0], 1)

    def test_identity_link_previous_sets_id_and_moves_no_mail(self):
        b = self.beta()
        before = self.inbox_counts()
        env = dict(self.env, ROOKERY_SESSION="beta", ROOKERY_NODE="me")
        out = self.cli("identity", "link", "--previous", self.law["agent_id"], "--json", env=env)
        self.assertIn(self.law["agent_id"], json.dumps(out))
        self.assertEqual(self.idents(b["session_id"])["me"][2], self.law["agent_id"])
        self.assertEqual(self.inbox_counts(), before)

    def test_identity_link_unknown_previous_exits_2(self):
        self.beta()
        env = dict(self.env, ROOKERY_SESSION="beta", ROOKERY_NODE="me")
        self.cli("identity", "link", "--previous", "no-such-agent", "--json", env=env, code=2)

    # ---- wave-8 audit fixes ------------------------------------------------
    def no_session(self, name):
        return self.q("SELECT COUNT(*) FROM rookery_sessions WHERE name=?", name)[0][0] == 0

    def test_from_is_atomic_when_identity_insert_aborts(self):
        c = sqlite3.connect(self.db)
        c.execute("CREATE TRIGGER boom BEFORE INSERT ON identities WHEN NEW.previous_id "
                  "IS NOT NULL BEGIN SELECT RAISE(ABORT,'boom'); END")
        c.commit()
        c.close()
        r = self.cli("session", "new", "--name", "s3", "--from", "alpha", "--json", code=2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue(r.stderr.strip())
        self.assertTrue(self.no_session("s3"))
        c = sqlite3.connect(self.db)
        c.execute("DROP TRIGGER boom")
        c.commit()
        c.close()
        out = self.cli("session", "new", "--name", "s3", "--from", "alpha", "--json")
        self.assertEqual(len(self.idents(out["session_id"])), 2)

    def test_from_unknown_creates_no_session(self):
        self.cli("session", "new", "--name", "s9", "--from", "nope", "--json", code=2)
        self.assertTrue(self.no_session("s9"))

    def test_from_empty_session(self):
        self.cli("session", "new", "--name", "empty", "--json")
        b = self.cli("session", "new", "--name", "s2", "--from", "empty", "--json")
        self.assertEqual(b["copied"], [])

    def test_from_unresolvable_root_exits_2_before_creating(self):
        cwd = os.path.join(self.tmp.name, "my project")
        os.makedirs(cwd)
        env = {k: v for k, v in self.env.items() if k != "ROOKERY_ROOT"}
        import subprocess
        r = subprocess.run([sys.executable, self.CLI, "session", "new", "--name", "s4",
                            "--from", "alpha", "--json"], env=env, cwd=cwd,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue(self.no_session("s4"))

    def beta_dir(self, *p):
        return os.path.join(self.root, "agents", "beta", *p)

    def no_tmp_left(self):
        left = []
        for d, _, fs in os.walk(os.path.join(self.root, "agents", "beta")):
            left += [f for f in fs if f.endswith(".tmp") and not os.path.islink(
                os.path.join(d, f))]
        self.assertEqual(left, [])

    def test_failed_copy_reported_and_no_tmp_left(self):
        os.makedirs(self.beta_dir("me", "runs", "TASK-2", "HANDOFF.md"))
        b = self.beta()
        self.assertEqual([f["name"] for f in b["failed"]], ["me"], b)
        self.assertEqual(b["failed"][0]["task"], "TASK-2")
        self.assertTrue(b["failed"][0]["reason"])
        self.assertEqual([c["name"] for c in b["copied"]], ["law-counsel"])
        self.no_tmp_left()

    def test_existing_destination_not_overwritten(self):
        os.makedirs(self.beta_dir("me", "runs", "TASK-2"))
        with open(self.beta_dir("me", "runs", "TASK-2", "HANDOFF.md"), "w") as f:
            f.write("mine")
        b = self.beta()
        self.assertEqual(open(self.beta_dir("me", "runs", "TASK-2", "HANDOFF.md")).read(),
                         "mine")
        f = [x for x in b["failed"] if x["name"] == "me"]
        self.assertEqual(f[0]["reason"], "exists", b)

    def test_planted_tmp_symlink_not_followed(self):
        victim = os.path.join(self.tmp.name, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep")
        os.makedirs(self.beta_dir("me", "runs", "TASK-2"))
        os.symlink(victim, self.beta_dir("me", "runs", "TASK-2", "HANDOFF.md.tmp"))
        self.beta()
        self.assertEqual(open(victim).read(), "keep")

    def test_symlinked_old_workspace_is_skipped(self):
        out = os.path.join(self.tmp.name, "elsewhere")
        self.write(out, "runs/TASK-9/HANDOFF.md", "stolen")
        import shutil
        shutil.rmtree(os.path.join(self.root, "agents", "alpha", "me"))
        os.symlink(out, os.path.join(self.root, "agents", "alpha", "me"))
        b = self.beta()
        self.assertEqual([c["name"] for c in b["copied"]], ["law-counsel"], b)
        self.assertFalse(os.path.exists(self.beta_dir("me", "runs", "TASK-9")))

    def test_copied_file_mode_follows_umask(self):
        b = self.beta()
        um = os.umask(0)
        os.umask(um)
        mode = os.stat(b["copied"][0]["path"]).st_mode & 0o777
        self.assertEqual(mode, 0o666 & ~um)

    def test_link_previous_rejects_cycles(self):
        self.cli("session", "new", "--name", "cyc", "--json")
        ids = {n: self.cli("identity", "new", n, "--session", "cyc", "--json")["agent_id"]
               for n in ("a", "b", "c")}

        def link(node, prev, code=0):
            env = dict(self.env, ROOKERY_SESSION="cyc", ROOKERY_NODE=node)
            return self.cli("identity", "link", "--previous", ids[prev], "--json",
                            env=env, code=code)
        link("a", "b")
        r = link("b", "a", code=2)
        self.assertNotIn("Traceback", r.stderr)
        link("b", "c")
        link("c", "a", code=2)


if __name__ == "__main__":
    unittest.main()


class EndedSessionRefusesJoin(IdentityBase):
    """Q1 (2026-10-16): no path creates or joins an identity in an ended session."""

    def setUp(self):
        super().setUp()
        self.s = RI.new_session(self.conn, name="old")
        self.bob = RI.new_identity(self.conn, self.s["session_id"], "bob")
        RI.end_session(self.conn, "old")
        self.env = {"ROOKERY_SESSION": "old", "ROOKERY_NODE": "ghost"}

    def test_new_identity_refused(self):
        before = self.snapshot()
        with self.assertRaises(RI.SessionEnded):
            RI.new_identity(self.conn, self.s["session_id"], "carol")
        self.assertEqual(self.snapshot(), before)

    def test_resolve_create_refused_and_writes_nothing(self):
        before = self.snapshot()
        with self.assertRaises(RI.SessionEnded):
            self.resolve(self.env, create=True)
        self.assertEqual(self.snapshot(), before)

    def test_resolve_read_only_still_works(self):
        s, i, _ = self.resolve({"ROOKERY_SESSION": "old", "ROOKERY_NODE": self.bob["agent_id"]},
                               create=False)
        self.assertEqual(i["name"], "bob")

    def test_bind_refused(self):
        before = self.snapshot()
        with self.assertRaises(RI.SessionEnded):
            RI.bind(self.conn, self.s["session_id"], self.bob["agent_id"], "herdr-pane", "p1")
        self.assertEqual(self.snapshot(), before)

    def test_link_previous_refused(self):
        other = RI.new_session(self.conn, name="other")
        o = RI.new_identity(self.conn, other["session_id"], "zed")
        with self.assertRaises(RI.SessionEnded):
            RI.link_previous(self.conn, self.bob["agent_id"], o["agent_id"])

    def test_carry_over_from_ended_session_still_works(self):
        _s, made, _c, _f = RI.new_session_from(self.conn, "old", "fresh", (), self.root)
        self.assertEqual([m["name"] for m in made], ["bob"])


class EndedCheckIsLocked(IdentityBase):
    """F4: the ended check runs inside BEGIN IMMEDIATE (no TOCTOU)."""

    def _first_statements(self, fn):
        seen = []
        self.conn.commit()
        self.conn.set_trace_callback(seen.append)
        try:
            fn()
        finally:
            self.conn.set_trace_callback(None)
        return seen

    def _assert_begin_first(self, seen):
        begin = next(i for i, s in enumerate(seen) if s.strip().upper().startswith("BEGIN IMMEDIATE"))
        sel = next(i for i, s in enumerate(seen) if "rookery_sessions" in s)
        self.assertLess(begin, sel, seen)

    def test_new_identity(self):
        s = RI.new_session(self.conn, name="a")
        self._assert_begin_first(self._first_statements(
            lambda: RI.new_identity(self.conn, s["session_id"], "bob")))

    def test_bind(self):
        s = RI.new_session(self.conn, name="a")
        i = RI.new_identity(self.conn, s["session_id"], "bob")
        self._assert_begin_first(self._first_statements(
            lambda: RI.bind(self.conn, s["session_id"], i["agent_id"], "k", "v")))

    def test_link_previous(self):
        s = RI.new_session(self.conn, name="a")
        i = RI.new_identity(self.conn, s["session_id"], "bob")
        j = RI.new_identity(self.conn, s["session_id"], "amy")
        self._assert_begin_first(self._first_statements(
            lambda: RI.link_previous(self.conn, i["agent_id"], j["agent_id"])))

    def test_ended_refusal_leaves_no_open_transaction(self):
        s = RI.new_session(self.conn, name="a")
        RI.end_session(self.conn, "a")
        with self.assertRaises(RI.SessionEnded):
            RI.new_identity(self.conn, s["session_id"], "bob")
        self.assertFalse(self.conn.in_transaction)


class LegacyCannotEnd(IdentityBase):
    def test_end_legacy_refused(self):
        with self.assertRaises(ValueError) as cm:
            RI.end_session(self.conn, "legacy")
        self.assertIn("legacy", str(cm.exception))
        self.assertEqual(RI.get_session(self.conn, "legacy")["state"], "open")


class CliEndedSession(CliSessionIdentity):
    """Runs only the new tests (parent tests are skipped by name filter below)."""

    def _ended(self):
        self.cli("session", "new", "--name", "alpha", "--json")
        self.cli("identity", "new", "bob", "--session", "alpha", "--json")
        self.cli("session", "end", "alpha", "--json")

    def test_identity_new_in_ended_exits_3(self):
        self._ended()
        r = self.cli("identity", "new", "carol", "--session", "alpha", code=3)
        self.assertIn("ended", r.stderr)
        self.assertIn('"alpha"', r.stderr)
        self.assertEqual(r.stdout, "")

    def test_identity_new_env_session_ended_exits_3(self):
        self._ended()
        env = dict(self.env, ROOKERY_SESSION="alpha")
        r = self.cli("identity", "new", "carol", env=env, code=3)
        self.assertIn("ended", r.stderr)

    def test_identity_bind_and_link_in_ended_exit_3(self):
        self._ended()
        env = dict(self.env, ROOKERY_SESSION="alpha", ROOKERY_NODE="bob")
        r = self.cli("identity", "bind", "herdr-pane:p1", env=env, code=3)
        self.assertIn("ended", r.stderr)
        r = self.cli("identity", "link", "--previous", "ag-0000000000", env=env, code=3)
        self.assertIn("ended", r.stderr)

    def test_identity_show_and_list_still_work(self):
        self._ended()
        env = dict(self.env, ROOKERY_SESSION="alpha", ROOKERY_NODE="bob")
        self.assertEqual(self.cli("identity", "show", "--json", env=env)["name"], "bob")
        self.assertEqual(len(self.cli("identity", "list", "--session", "alpha",
                                      "--json")["identities"]), 1)

    def test_session_end_legacy_exits_2(self):
        self.cli("session", "new", "--name", "alpha", "--json")   # creates the legacy row
        r = self.cli("session", "end", "legacy", code=2)
        self.assertIn("legacy", r.stderr)
        self.assertEqual(self.cli("session", "show", "legacy", "--json")["session"]["state"],
                         "open")


for _n in [n for n in dir(CliSessionIdentity) if n.startswith("test_")]:
    if not hasattr(CliEndedSession.__dict__.get(_n), "__call__"):
        setattr(CliEndedSession, _n, None)
