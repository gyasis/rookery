"""T006 (D3, FR-003): the five root-resolving sites use ROOKERY_ROOT first, and
~/.rookery/projects/<folder name> when it is unset. Proof tests: no production change.

Each CLI case runs `rookery_cli.py` as a subprocess with HOME, ROOKERY_DB and ROOKERY_ROOT
in a temporary folder. The current folder is <root>/agents/s1/scout (set case) or a folder
named `proj` (unset case)."""
import json
import os
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "rookery_cli.py")


def _log_lines(root):
    path = os.path.join(root, "logs", "turn-block.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return [json.loads(x) for x in f if x.strip()]


class _Base:
    """Subclasses set MODE: 'set' (ROOKERY_ROOT given) or 'unset' (folder name proj)."""

    MODE = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.home = os.path.join(t, "home")
        os.makedirs(self.home)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        self.env.update(HOME=self.home, ROOKERY_DB=os.path.join(t, "r.db"))
        if self.MODE == "set":
            self.root = os.path.join(t, "proj-root")
            self.env["ROOKERY_ROOT"] = self.root
            self.cwd = os.path.join(self.root, "agents", "s1", "scout")
            self.other = os.path.join(self.home, ".rookery", "projects", "scout")
        else:
            self.cwd = os.path.join(t, "work", "proj")
            self.root = os.path.join(self.home, ".rookery", "projects", "proj")
            self.other = os.path.join(t, "proj-root")
        os.makedirs(self.cwd)
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        try:
            self.assert_no_stray_roots()
        finally:
            self.tmp.cleanup()

    def assert_no_stray_roots(self):
        """Nothing was written under a root a wrong implementation could pick."""
        projects = os.path.join(self.home, ".rookery", "projects")
        found = sorted(os.listdir(projects)) if os.path.isdir(projects) else []
        if self.MODE == "set":
            self.assertEqual(found, [], "ROOKERY_ROOT set but ~/.rookery/projects has " + str(found))
        else:
            self.assertEqual(found, ["proj"], "unexpected project roots " + str(found))
            self.assertFalse(os.path.exists(self.other), self.other + " was created")

    def cli(self, *args, env=None, code=0):
        r = subprocess.run([sys.executable, CLI, *args], env=env or self.env, cwd=self.cwd,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, code, r.stderr + r.stdout)
        return json.loads(r.stdout) if code == 0 and "--json" in args else r

    def agent_env(self, session="s1", node="scout"):
        return dict(self.env, ROOKERY_SESSION=session, ROOKERY_NODE=node)

    def setup_team(self):
        self.s1 = self.cli("session", "new", "--name", "s1", "--json")
        self.scout = self.cli("identity", "new", "scout", "--session", "s1", "--json")
        self.peer = self.cli("identity", "new", "peer", "--session", "s1", "--json")

    def assert_root_only(self):
        """The log landed under the expected root and not under the other candidate."""
        self.assertTrue(_log_lines(self.root), "no log line under " + self.root)
        self.assertEqual(_log_lines(self.other), [], "log line under " + self.other)

    def test_session_new_from(self):
        self.setup_team()
        run = os.path.join(self.root, "agents", "s1", "scout", "runs", "TASK-1")
        os.makedirs(run)
        with open(os.path.join(run, "HANDOFF.md"), "w") as f:
            f.write("handoff")
        out = self.cli("session", "new", "--name", "s2", "--from", "s1", "--json")
        got = os.path.join(self.root, "agents", "s2", "scout", "runs", "TASK-1", "HANDOFF.md")
        self.assertTrue(os.path.isfile(got), out)
        self.assertEqual([c["name"] for c in out["copied"]], ["scout"])
        self.assertEqual(os.path.realpath(out["copied"][0]["path"]), os.path.realpath(got))
        self.assertFalse(os.path.exists(os.path.join(self.other, "agents")))

    def test_identity_bind(self):
        self.setup_team()
        self.cli("identity", "bind", "herdr-pane:p1", "--json", env=self.agent_env())
        self.assert_root_only()
        self.assertIn("herdr-pane", json.dumps(_log_lines(self.root)))

    def test_peek(self):
        self.setup_team()
        self.cli("send", "scout", "note to self", "--json", env=self.agent_env())
        r = self.cli("peek", "--json", env=self.agent_env())
        self.assertEqual(r["name"], "scout")
        events = [e.get("event") for e in _log_lines(self.root)]
        self.assertIn("inbox-read", events)
        self.assertEqual(_log_lines(self.other), [])

    def test_send(self):
        self.setup_team()
        r = self.cli("send", "peer", "hello", "--json", env=self.agent_env())
        self.assertEqual(r["outcome"], "sent")
        self.assert_root_only()
        self.assertIn("send", json.dumps(_log_lines(self.root)))

    def test_postmaster_main(self):
        import postmaster
        seen = {}
        old = os.getcwd()
        os.chdir(self.cwd)
        try:
            with mock.patch.dict(os.environ, self.env, clear=True), \
                    mock.patch.object(sys, "argv", ["postmaster", "--nodes", "scout"]), \
                    mock.patch.object(postmaster, "run", lambda *a, **kw: seen.update(kw)):
                postmaster.main()
        finally:
            os.chdir(old)
        self.assertEqual(os.path.realpath(seen["root"]), os.path.realpath(self.root))


class RootFromEnv(_Base, unittest.TestCase):
    MODE = "set"


class RootFromFolderName(_Base, unittest.TestCase):
    MODE = "unset"


if __name__ == "__main__":
    unittest.main()
