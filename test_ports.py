"""T020: `rookery up` gives each agent one free port per name (US7, FR-008, D16, R13)."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import unittest

import test_up_build as tb

HERE = tb.HERE


def free_block(n=20):
    """A start port with n consecutive free ports on 127.0.0.1."""
    for start in range(52000, 60000, 50):
        socks = []
        try:
            for p in range(start, start + n):
                s = socket.socket()
                s.bind(("127.0.0.1", p))
                socks.append(s)
            return start
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise unittest.SkipTest("no free port block")


@unittest.skipUnless(tb.HAVE_GIT, "git is missing")
class Ports(unittest.TestCase):
    up = tb.UpBuild.up
    write_team = tb.UpBuild.write_team
    setUp = tb.UpBuild.setUp
    tearDown = tb.UpBuild.tearDown

    def config(self, lo, hi, names=("api", "web")):
        os.makedirs(os.path.join(self.home, ".rookery"), exist_ok=True)
        with open(os.path.join(HERE, "rookery_config.default.json")) as fh:
            cfg = json.load(fh)
        cfg["ports"] = {"range": [lo, hi], "names": list(names)}
        with open(os.path.join(self.home, ".rookery", "config.json"), "w") as fh:
            json.dump(cfg, fh)

    def rows(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT i.name, p.name, p.port FROM ports p JOIN identities i "
                             "ON i.agent_id=p.agent_id ORDER BY p.port").fetchall()
        finally:
            c.close()

    def test_distinct_ports_inside_range(self):
        lo = free_block()
        self.config(lo, lo + 19)
        self.up(self.team, "--no-launch")
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({r[2] for r in rows}), 4)
        self.assertTrue(all(lo <= r[2] <= lo + 19 for r in rows))
        self.assertEqual(sorted({(r[0], r[1]) for r in rows}),
                         [("mapper", "api"), ("mapper", "web"), ("scout", "api"), ("scout", "web")])

    def test_open_port_is_skipped(self):
        lo = free_block()
        self.config(lo, lo + 19)
        held = socket.socket()
        held.bind(("127.0.0.1", lo))
        try:
            self.up(self.team, "--no-launch")
        finally:
            held.close()
        self.assertNotIn(lo, [r[2] for r in self.rows()])
        self.assertEqual(len(self.rows()), 4)

    def test_second_run_same_map(self):
        lo = free_block()
        self.config(lo, lo + 19)
        self.up(self.team, "--no-launch")
        first = self.rows()
        held = socket.socket()
        held.bind(("127.0.0.1", first[0][2]))      # even a now-busy port stays with its owner
        try:
            self.up(self.team, "--no-launch")
        finally:
            held.close()
        self.assertEqual(self.rows(), first)

    def test_exhausted_range_reports_one_agent_only(self):
        lo = free_block()
        self.config(lo, lo + 2, names=("api", "web"))        # 3 ports, 4 needed
        r = self.up(self.team, "--no-launch", "--json")
        agents = {a["name"]: a for a in json.loads(r.stdout)["agents"]}
        self.assertEqual(agents["scout"]["launch"].startswith("not started"), False)
        self.assertEqual(agents["mapper"]["launch"], f"not started: no free port in {lo}-{lo + 2}")

    def test_two_children_bind_their_api_port_r13(self):
        lo = free_block()
        self.config(lo, lo + 19)
        self.up(self.team, "--no-launch")
        c = sqlite3.connect(self.db)
        maps = {}
        for aid, name, port in c.execute("SELECT p.agent_id, p.name, p.port FROM ports p"):
            maps.setdefault(aid, {})[name] = port
        c.close()
        code = ("import json,os,socket,sys,time\\n"
                "p=json.loads(os.environ['ROOKERY_PORTS'])['api']\\n"
                "s=socket.socket(); s.bind(('127.0.0.1',p)); print('bound',flush=True); time.sleep(1.5)\\n")
        kids = [subprocess.Popen([sys.executable, "-c", code.replace("\\n", "\n")],
                                 env=dict(self.env, ROOKERY_PORTS=json.dumps(m)),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for m in maps.values()]
        for k in kids:
            out, err = k.communicate(timeout=20)
            self.assertEqual(k.returncode, 0, err)
            self.assertIn("bound", out)


if __name__ == "__main__":
    unittest.main()
