#!/usr/bin/env python3
"""Consume a Rookery invite file -> join the mesh.

An invite is a JSON bundle minted by `POST /invite` on the sidecar:
    { "url": "...", "node_id": "...", "token": "...",
      "card_pubkey": "<base64 ed25519>", "may_task": [...], "expires_at": ... }

Usage:
    python3 rookery_join.py invite.json --mode verify   # pin check (default)
    python3 rookery_join.py invite.json --mode mcp      # print ~/.claude.json snippet
    python3 rookery_join.py invite.json --mode loop     # exec mailctl.py loop now

Verification: fetches the served agent card and confirms its embedded public key
EQUALS the `card_pubkey` pinned in the invite (TOFU). Also runs the signature
check via security.verify_card() so a tampered card fails.
"""
import argparse
import base64
import json
import os
import ssl
import sys
import urllib.request


def _ctx(url, insecure_tls):
    if url.startswith("https") and insecure_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def fetch_card(url, insecure_tls=False):
    u = url.rstrip("/") + "/.well-known/agent-card.json"
    with urllib.request.urlopen(u, timeout=15, context=_ctx(url, insecure_tls)) as r:
        return json.loads(r.read())


def verify(invite, insecure_tls=False, quiet=False):
    pinned = invite.get("card_pubkey")
    if not pinned:
        if not quiet:
            print("WARN: invite has no card_pubkey -- TOFU pin skipped (sidecar not signing cards)")
        return True
    try:
        card = fetch_card(invite["url"], insecure_tls)
    except Exception as e:
        print(f"FAIL: could not fetch agent card from {invite['url']}: {e}")
        return False
    sig = card.get("signature") or {}
    served = sig.get("publicKey")
    if served != pinned:
        print(f"FAIL: served card pubkey != invite pin")
        print(f"  pinned in invite : {(pinned or '')[:32]}...")
        print(f"  served by sidecar: {(served or '')[:32]}...")
        return False
    # also run the full signature check (re-uses security.verify_card)
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import security  # noqa: E402
    ok, info = security.verify_card(card)
    if not ok:
        print(f"FAIL: card signature invalid ({info})")
        return False
    if not quiet:
        print(f"OK: pinned pubkey matches and card signature is valid ({pinned[:24]}...)")
    return True


def as_mcp(invite):
    snippet = {
        "rookery": {
            "command": "python3",
            "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "rookery_mcp.py")],
            "env": {
                "ROOKERY_NODE": invite["node_id"],
                "ROOKERY_URL": invite["url"],
                "ROOKERY_TOKEN": invite["token"],
            },
        }
    }
    if invite["url"].startswith("https") and os.environ.get("ROOKERY_INSECURE_TLS"):
        snippet["rookery"]["env"]["ROOKERY_INSECURE_TLS"] = "1"
    print("# Add this entry under `mcpServers` in ~/.claude.json:")
    print(json.dumps(snippet, indent=2))


def as_loop(invite, insecure_tls=False):
    env = os.environ.copy()
    env["ROOKERY_TOKEN"] = invite["token"]
    if insecure_tls:
        env["ROOKERY_INSECURE_TLS"] = "1"
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = ["python3", os.path.join(here, "mailctl.py"), "loop",
           "--url", invite["url"], "--node", invite["node_id"]]
    print(f"$ ROOKERY_TOKEN=*** {' '.join(cmd)}")
    os.execvpe(cmd[0], cmd, env)


def main():
    ap = argparse.ArgumentParser(description="Consume a Rookery invite -> join the mesh.")
    ap.add_argument("invite", help="path to invite.json")
    ap.add_argument("--mode", choices=["verify", "mcp", "loop"], default="verify")
    ap.add_argument("--insecure-tls", action="store_true",
                    help="accept self-signed certs (lab use)")
    a = ap.parse_args()
    with open(a.invite) as fh:
        invite = json.load(fh)
    # verify always runs (the pin check is the whole point of an invite)
    if not verify(invite, a.insecure_tls):
        sys.exit(1)
    if a.mode == "verify":
        return
    if a.mode == "mcp":
        as_mcp(invite)
    elif a.mode == "loop":
        as_loop(invite, a.insecure_tls)


if __name__ == "__main__":
    main()
