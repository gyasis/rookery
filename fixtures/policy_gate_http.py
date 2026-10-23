"""Test policy (T024). Load with ROOKERY_SECURITY=fixtures.policy_gate_http:GateHttpPolicy.
A body with DENY-ME is quarantined (fixture-deny). A body with BOOM-ME makes the screen raise. A body with LOCK-ME takes the database write lock on
another connection for 6.5 s (the screen runs before the gate takes its own lock), so the gate says db-busy.
The shared token still applies (DefaultPolicy). A task for the recipient `forbidden` is not authorized."""
import os
import sqlite3
import threading
import time

import security


def _hold_write_lock(seconds):
    got = threading.Event()

    def run():
        conn = sqlite3.connect(os.environ["ROOKERY_DB"], timeout=1, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        got.set()
        time.sleep(seconds)
        conn.execute("ROLLBACK")
        conn.close()
    threading.Thread(target=run, daemon=True).start()
    got.wait(5)


class GateHttpPolicy(security.DefaultPolicy):
    def inspect_inbound(self, sender, recipient, body):
        if "BOOM-ME" in (body or ""):
            raise RuntimeError("fixture boom")
        if "LOCK-ME" in (body or ""):
            _hold_write_lock(6.5)
        if "DENY-ME" in (body or ""):
            return security.Decision(False, "fixture-deny")
        return security.ALLOW

    def authorize(self, principal, action, target):
        if target == "forbidden":
            return security.Decision(False, "fixture-forbidden")
        return security.ALLOW
