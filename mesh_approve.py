#!/usr/bin/env python3
"""The human side of the CIBA credential gate.

No args  -> list pending credential requests.
--id N --approve  -> approve request N. Mints a short-lived token *pointer*
                     (token_ref). The REAL secret is never stored here; in
                     production this ref resolves against the OS keychain / a
                     JIT broker (Aembit etc.) at the moment of use.
--id N --deny     -> deny request N.
"""
import argparse
import os
import re
import shutil
import subprocess
import rookery as R


def mint_pointer(resource):
    """Pick a real, verified pointer to the secret for <resource> — never the
    secret itself. Prefers the OS keychain (secret-tool), falls back to an env
    var. Returns (token_ref, status)."""
    env_var = "ROOKERY_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", resource).upper()
    if shutil.which("secret-tool"):
        try:
            r = subprocess.run(
                ["secret-tool", "lookup", "service", "rookery", "account", resource],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout:
                return f"keychain://rookery/{resource}", "keychain (verified present)"
        except Exception:
            pass
    if os.environ.get(env_var):
        return f"env://{env_var}", "env (verified set)"
    return f"env://{env_var}", f"NOT FOUND — store it first: export {env_var}=…  (or: secret-tool store --label rookery service rookery account {resource})"


def list_pending(conn):
    rows = R.pending_credentials(conn)
    if not rows:
        print("no pending credential requests.")
        return
    print("pending credential requests:")
    for r in rows:
        print(f"  #{r['id']}  node={r['node_id']}  resource={r['resource']}")


def main():
    ap = argparse.ArgumentParser(description="Approve/deny Rookery credential requests.")
    ap.add_argument("--id", type=int, default=None, dest="req_id")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true")
    g.add_argument("--deny", action="store_true")
    ap.add_argument("--token-ref", default=None,
                    help="explicit secret pointer; default mints a verified keychain/env ref")
    a = ap.parse_args()
    conn = R.connect()

    if a.req_id is None:
        list_pending(conn)
        return

    row = R.get_credential(conn, a.req_id)
    if row is None:
        print(f"no such request #{a.req_id}")
        return

    if a.deny:
        R.deny_credential(conn, a.req_id)
        print(f"denied #{a.req_id}")
        return

    # default action is approve
    if a.token_ref:
        token_ref, status = a.token_ref, "explicit"
    else:
        token_ref, status = mint_pointer(row["resource"])
    R.approve_credential(conn, a.req_id, token_ref)
    print(f"approved #{a.req_id} for node '{row['node_id']}' -> token_ref={token_ref}  [{status}]")
    print("(pointer only — the agent calls rookery.resolve_secret(token_ref) at use time; "
          "the secret is never written to the DB)")


if __name__ == "__main__":
    main()
