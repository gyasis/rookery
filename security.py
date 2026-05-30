#!/usr/bin/env python3
"""Rookery security — a SWAPPABLE policy module.

Every security decision in the mesh routes through ONE SecurityPolicy object, so
a hardened layer can replace it WITHOUT touching the sidecar, nodes, or CLIs.

Decision points (override any in a subclass; base defaults are permissive):
  - is_public_path(path)                 paths that skip transport auth
  - authenticate(headers) -> principal   who is calling the sidecar (None = reject)
  - security_schemes() -> dict|None      what the A2A agent card advertises
  - authorize(principal, action, target) may they do it (e.g. task a node)
  - inspect_inbound(sender, recipient, body)  vet mail before an agent sees it
                                         (prompt-injection / content-policy hook)
  - mint_pointer(resource) -> (ref, status)   approve a credential -> a POINTER
  - resolve_secret(token_ref) -> secret  resolve a pointer at use time

Swap it: set ROOKERY_SECURITY="my.module:MyPolicy" (a SecurityPolicy subclass),
or call security.set_policy(obj). Default = DefaultPolicy (current behavior).
"""

import base64
import hmac
import importlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

import rookery as R


@dataclass
class Decision:
    allow: bool
    reason: str = ""

    def __bool__(self):
        return self.allow


ALLOW = Decision(True)


class SecurityPolicy:
    """Base policy — permissive. Subclass and override to harden."""

    PUBLIC_PATHS = {
        "/health",
        "/.well-known/agent-card.json",
        "/.well-known/agent.json",
        "/join",
        "/bootstrap",
    }

    # --- transport auth ---------------------------------------------------
    def is_public_path(self, path):
        return path in self.PUBLIC_PATHS

    def authenticate(self, headers):
        """Return a principal id if accepted, else None to reject. Base: open."""
        return "anon"

    def security_schemes(self):
        """The securitySchemes/security to merge into the A2A agent card, or None."""
        return None

    # --- authorization ----------------------------------------------------
    def authorize(self, principal, action, target):
        return ALLOW

    # --- message safety (the prompt-injection / content seam) -------------
    def inspect_inbound(self, sender, recipient, body):
        return ALLOW

    # --- credentials ------------------------------------------------------
    def mint_pointer(self, resource):
        return mint_pointer(resource)

    def resolve_secret(self, token_ref):
        return R.resolve_secret(token_ref)

    # --- invite / handshake ----------------------------------------------
    def mint_invite(self, node_id, may_task=None, ttl_minutes=60):
        """Return a fresh invite for a new node. Default: re-issue the shared
        token (no per-node identity, no expiry). HardenedPolicy overrides this
        to MINT a per-principal bearer token with TTL."""
        return {
            "token": getattr(self, "token", None),
            "node_id": node_id,
            "may_task": may_task,
            "expires_at": None,
        }


class DefaultPolicy(SecurityPolicy):
    """Today's behavior: optional bearer-token transport auth, allow-all authz,
    pass-through message inspection, env/keychain credential resolution."""

    def __init__(self, token=None):
        self.token = token or os.environ.get("ROOKERY_TOKEN") or None

    def authenticate(self, headers):
        if not self.token:
            return "anon"  # auth disabled
        h = headers.get("Authorization", "") if headers else ""
        if h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), self.token):
            return "bearer"
        return None

    def security_schemes(self):
        if not self.token:
            return None
        return {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearerAuth": []}],
        }


def mint_pointer(resource):
    """Approve a credential -> a verified POINTER (keychain or env), never the
    secret. Lives here so a hardened policy (JIT broker, vault) can replace it."""
    env_var = "ROOKERY_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", resource).upper()
    if shutil.which("secret-tool"):
        try:
            r = subprocess.run(
                ["secret-tool", "lookup", "service", "rookery", "account", resource],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout:
                return f"keychain://rookery/{resource}", "keychain (verified present)"
        except Exception:
            pass
    if os.environ.get(env_var):
        return f"env://{env_var}", "env (verified set)"
    return f"env://{env_var}", f"NOT FOUND — store it first: export {env_var}=…"


# --- A2A signed agent cards (Ed25519) --------------------------------------
def _canonical(card):
    """Deterministic bytes of the card MINUS its signature (so signer + verifier
    agree)."""
    body = {k: v for k, v in card.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_card(card, key_pem_path):
    """Attach an Ed25519 signature (+ the public key) to an agent card."""
    from cryptography.hazmat.primitives import serialization

    with open(key_pem_path, "rb") as fh:
        priv = serialization.load_pem_private_key(fh.read(), password=None)
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    card["signature"] = {
        "alg": "ed25519",
        "publicKey": base64.b64encode(pub).decode(),
        "value": base64.b64encode(priv.sign(_canonical(card))).decode(),
    }
    return card


def verify_card(card):
    """Verify a card's Ed25519 signature against its embedded public key.
    Returns (ok, info). NOTE: embedding the key proves integrity; for true
    identity, pin the public key out-of-band (JWKS/DID) rather than trusting
    the one in the card."""
    sig = card.get("signature")
    if not isinstance(sig, dict) or not sig.get("value"):
        return False, "no signature"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["publicKey"]))
        pub.verify(base64.b64decode(sig["value"]), _canonical(card))
        return True, sig["publicKey"][:16] + "…"
    except Exception as e:
        return False, str(e)


_POLICY = None


def get_policy(token=None):
    """Return the active policy (singleton). First call wins; pass token to seed
    the default. Override class via ROOKERY_SECURITY='module:Class'."""
    global _POLICY
    if _POLICY is not None:
        return _POLICY
    spec = os.environ.get("ROOKERY_SECURITY")
    if spec:
        mod, _, cls = spec.partition(":")
        klass = getattr(importlib.import_module(mod), cls)
        try:
            _POLICY = klass(token=token)
        except TypeError:
            _POLICY = klass()
    else:
        _POLICY = DefaultPolicy(token=token)
    return _POLICY


def set_policy(policy):
    """Install a policy explicitly (e.g. a hardened layer at startup)."""
    global _POLICY
    _POLICY = policy
    return policy
