#!/usr/bin/env python3
"""Hardened SecurityPolicy — PER-NODE identity + per-target authorization.

The DefaultPolicy uses ONE shared bearer token (anyone with it is "bearer" and
may task anything). This hardened drop-in instead gives every principal its OWN
token and an ACL of which nodes it may task. It changes NOTHING else in the mesh
— same sidecar, same nodes, same CLIs — because every decision routes through
the swappable policy object.

Install it:
    ROOKERY_SECURITY="policy_hardened:HardenedPolicy" \\
    ROOKERY_NODE_IDENTITY=/path/to/node_identity.json \\
    python3 mailroom_server.py --host 0.0.0.0 --port 8765 --public-url https://host:8765

Config (node_identity.json — keep it out of git, chmod 600):
    {
      "principals": {
        "architect": {"token": "<secret>", "may_task": ["researcher", "reviewer", "coder"]},
        "guest":     {"token": "<secret>", "may_task": ["researcher"]}
      }
    }
  - The bearer token identifies the CALLER as that principal (per-node identity).
  - may_task is the ACL; "*" means task anything. Unknown principal / target -> deny.

It also keeps a defense-in-depth inbound filter (blocks a few injection patterns)
so it's strictly safer than the default, not just authz-only.
"""
import hmac
import json
import os
import re

import security

_BLOCKED = re.compile(
    r"\brm\s+-rf\b|\bDROP\s+TABLE\b|ignore\s+(all\s+)?(previous|prior)\s+instructions",
    re.I,
)


class HardenedPolicy(security.SecurityPolicy):
    def __init__(self, token=None):
        self.principals = {}
        path = os.environ.get("ROOKERY_NODE_IDENTITY")
        if path and os.path.exists(path):
            with open(path) as fh:
                self.principals = (json.load(fh) or {}).get("principals", {}) or {}
        # token -> principal id
        self._by_token = {
            v["token"]: name
            for name, v in self.principals.items()
            if isinstance(v, dict) and v.get("token")
        }

    # --- per-node identity: the bearer token IS the principal ----------------
    def authenticate(self, headers):
        h = (headers.get("Authorization", "") if headers else "")
        if not h.startswith("Bearer "):
            return None
        tok = h[7:].strip()
        for known, principal in self._by_token.items():
            if hmac.compare_digest(tok, known):
                return principal
        return None  # unknown token -> reject (no anonymous fallthrough)

    def security_schemes(self):
        return {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearerAuth": []}],
        }

    # --- per-target authorization (default deny) -----------------------------
    def authorize(self, principal, action, target):
        allow = (self.principals.get(principal) or {}).get("may_task", [])
        if "*" in allow or target in allow:
            return security.ALLOW
        return security.Decision(False, f"principal '{principal}' may not task '{target}'")

    # --- defense-in-depth inbound filter -------------------------------------
    def inspect_inbound(self, sender, recipient, body):
        if body and _BLOCKED.search(body):
            return security.Decision(False, "blocked content pattern")
        return security.ALLOW
