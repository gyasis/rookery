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
import uuid
import rookery as R


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
                    help="explicit secret pointer; default mints a JIT ref")
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
    token_ref = a.token_ref or f"jit://{row['resource']}/{uuid.uuid4().hex[:12]}"
    R.approve_credential(conn, a.req_id, token_ref)
    print(f"approved #{a.req_id} for node '{row['node_id']}' -> token_ref={token_ref}")
    print("(pointer only — the real secret resolves from the keychain/JIT broker at use time)")


if __name__ == "__main__":
    main()
