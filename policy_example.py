#!/usr/bin/env python3
"""Example hardened SecurityPolicy — a REFERENCE/template for your own.

Subclasses DefaultPolicy (so it keeps bearer-auth + env/keychain creds) and
overrides the content + authorization seams:
  - inspect_inbound: block obvious injection / dangerous patterns in ANY mail
    (internal node->node included — screened at the consuming node).
  - authorize: refuse tasking a sensitive node.

Install (no changes to the sidecar or nodes):
    ROOKERY_SECURITY="policy_example:ExamplePolicy" python3 mailroom_server.py ...
    ROOKERY_SECURITY="policy_example:ExamplePolicy" python3 postmaster.py ...
"""
import re

import security

_BLOCKED = re.compile(
    r"\brm\s+-rf\b|\bDROP\s+TABLE\b|ignore\s+(all\s+)?previous\s+instructions|\bexfiltrat",
    re.IGNORECASE,
)


class ExamplePolicy(security.DefaultPolicy):
    def inspect_inbound(self, sender, recipient, body):
        if body and _BLOCKED.search(body):
            return security.Decision(False, "matched a blocked pattern")
        return security.ALLOW

    def authorize(self, principal, action, target):
        if target == "vault":
            return security.Decision(False, "tasking 'vault' is not allowed")
        return security.ALLOW
