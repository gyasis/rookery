#!/usr/bin/env python3
"""Mail gate, T028 (amended 2026-10-23: a local mailroom needs no token): the start rules of mailroom_server.py (contract: HTTP, Start).

    python3 -m unittest test_mailroom_start -v

Each test starts mailroom_server.py as a subprocess on a free local port, with its own temp DB.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import security
from test_hooks import HERE
from test_paths_mailroom import _free_port

TOKEN = "T"
LIMIT = 10.0


def _lan_address():
    """The host's LAN address, or None. A UDP connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


class Start(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.procs = []
        self.addCleanup(self._stop_all)

    def _stop_all(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)
            p.stdout.close()
            p.stderr.close()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("ROOKERY_") or k.startswith("CLAUDE_"))}
        env.update(HOME=self.tmp.name, ROOKERY_DB=os.path.join(self.tmp.name, "m.db"),
                   ROOKERY_ROOT=self.tmp.name)
        env.update(extra)
        return env

    def command(self):
        return [sys.executable, os.path.join(HERE, "mailroom_server.py"), "--port", str(self.port)]

    def launch(self, *args, env=None):
        p = subprocess.Popen(
            [*self.command(), *args],
            env=env or self.env(), cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.procs.append(p)
        return p

    def refused(self, *args, env=None):
        """Start; it must exit by itself within the limit. Returns (code, output)."""
        p = self.launch(*args, env=env)
        try:
            p.wait(timeout=LIMIT)
        except subprocess.TimeoutExpired:
            self.fail(f"server kept running with {args}")
        return p.returncode, p.stdout.read().decode() + p.stderr.read().decode()

    def serving(self, *args, env=None):
        """Start; it must answer /health within the limit. Returns the process."""
        p = self.launch(*args, env=env)
        deadline = time.time() + LIMIT
        while True:
            if p.poll() is not None:
                self.fail(f"server exited {p.returncode}: {p.stderr.read().decode()}")
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1).read()
                return p
            except (urllib.error.URLError, OSError):
                if time.time() > deadline:
                    self.fail("server did not answer within 10 s")
                time.sleep(0.05)

    def stop_and_read(self, p):
        p.kill()
        p.wait(timeout=10)
        return p.stdout.read().decode() + p.stderr.read().decode()

    def call(self, method, path, payload=None, token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, self._body(r.read())
        except urllib.error.HTTPError as e:
            return e.code, self._body(e.read())

    @staticmethod
    def _body(raw):
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {"text": raw.decode(errors="replace")}  # e.g. the HTML approve form

    # --- refusals -------------------------------------------------------
    def test_public_host_without_token_exits_2_and_names_the_token(self):
        code, out = self.refused("--host", "0.0.0.0", "--allow-public")
        self.assertEqual(code, 2)
        self.assertIn("--token", out)

    def test_public_host_allow_no_auth_still_needs_a_token(self):
        code, out = self.refused("--host", "0.0.0.0", "--allow-public", "--allow-no-auth")
        self.assertEqual(code, 2)
        self.assertIn("--token", out)

    def test_public_host_without_allow_public_exits_2(self):
        code, out = self.refused("--host", "0.0.0.0", "--token", TOKEN)
        self.assertEqual(code, 2)
        self.assertIn("--allow-public", out)

    def test_public_host_with_no_auth_but_not_allow_public_exits_2(self):
        code, out = self.refused("--host", "0.0.0.0", "--allow-no-auth")
        self.assertEqual(code, 2)

    # --- warnings -------------------------------------------------------
    def test_allow_public_prints_one_warning(self):
        p = self.serving("--host", "0.0.0.0", "--token", TOKEN, "--allow-public")
        out = self.stop_and_read(p)
        self.assertEqual([l for l in out.splitlines() if l.startswith("WARNING:")].__len__(), 1, out)

    def test_local_host_with_no_token_serves_and_prints_one_note(self):
        p = self.serving()
        out = self.stop_and_read(p)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("NOTE:")]), 1, out)
        self.assertNotIn("WARNING:", out)
        self.assertNotIn("ERROR:", out)

    def test_allow_no_auth_on_local_host_changes_nothing(self):
        p = self.serving("--allow-no-auth")
        out = self.stop_and_read(p)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("NOTE:")]), 1, out)
        self.assertNotIn("WARNING:", out)

    def test_token_with_allow_no_auth_keeps_auth_on_and_is_silent(self):
        p = self.serving("--token", TOKEN, "--allow-no-auth")
        code, _ = self.call("POST", "/send", {"recipient": "x", "body": "b"})
        out = self.stop_and_read(p)
        self.assertEqual(code, 401)
        self.assertNotIn("WARNING:", out)
        self.assertNotIn("NOTE:", out)

    def test_env_token_counts_as_a_token(self):
        p = self.serving(env=self.env(ROOKERY_TOKEN=TOKEN))
        code, _ = self.call("POST", "/send", {})
        self.assertEqual(code, 401)
        self.assertNotIn("WARNING:", self.stop_and_read(p))

    # --- bind -----------------------------------------------------------
    def test_default_host_is_local_only(self):
        lan = _lan_address()
        self.serving("--token", TOKEN)
        if lan is None:
            self.skipTest("host has no LAN address")
        with self.assertRaises(OSError):
            socket.create_connection((lan, self.port), timeout=2).close()

    def test_empty_host_is_local_only(self):
        lan = _lan_address()
        self.serving("--host", "", "--token", TOKEN)
        if lan is None:
            self.skipTest("host has no LAN address")
        with self.assertRaises(OSError):
            socket.create_connection((lan, self.port), timeout=2).close()

    def test_ipv6_loopback_host_serves(self):
        try:
            with socket.socket(socket.AF_INET6) as s:
                s.bind(("::1", 0))
        except OSError:
            self.skipTest("host has no IPv6 loopback")
        self.url = f"http://[::1]:{self.port}"
        self.serving("--host", "::1", "--token", TOKEN)
        code, body = self.call("GET", "/health")
        self.assertEqual((code, body.get("ok")), (200, True))

    # --- routes ---------------------------------------------------------
    def test_local_no_token_call_without_token_works(self):
        self.serving()
        code, _ = self.call("POST", "/register", {"node_id": "n1"})
        self.assertEqual(code, 200)
        code, _ = self.call("GET", "/inbox?node=n1")
        self.assertEqual(code, 200)

    def test_with_token_non_public_routes_answer_401_without_token(self):
        self.serving("--token", TOKEN)
        for path in ("/inbox?node=n1", "/thread?node=n1", "/pending"):
            code, _ = self.call("GET", path)
            self.assertEqual(code, 401, path)
            self.assertNotIn(path.split("?")[0], security.SecurityPolicy.PUBLIC_PATHS)
        for path in ("/send", "/register", "/invite", "/a2a", "/claim"):
            code, _ = self.call("POST", path, {})
            self.assertEqual(code, 401, path)
        code, _ = self.call("GET", "/inbox?node=n1", token=TOKEN)
        self.assertEqual(code, 200)

    def test_with_token_join_routes_need_no_token(self):
        self.serving("--token", TOKEN)
        code, _ = self.call("POST", "/join", {})
        self.assertEqual(code, 400)  # reached the handler: bad body, not 401
        code, _ = self.call("GET", "/join/nosuchid")
        self.assertEqual(code, 404)  # reached the handler: unknown id, not 401

    def test_invite_and_approve_still_answer(self):
        self.serving("--token", TOKEN)
        code, body = self.call("POST", "/invite", {}, token=TOKEN)
        self.assertEqual(code, 200, body)
        code, _ = self.call("GET", "/approve")  # the form page is public, as today
        self.assertNotIn(code, (401, 500))
        code, _ = self.call("POST", "/approve", {}, token=TOKEN)
        self.assertNotIn(code, (401, 500))


class ServeStart(Start):
    """T028a: `rookery serve` follows the same start rules -- every case above, run through the CLI."""

    def command(self):
        return [sys.executable, os.path.join(HERE, "rookery_cli.py"), "serve", "--port", str(self.port)]


if __name__ == "__main__":
    unittest.main()
