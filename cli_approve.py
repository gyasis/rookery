"""cli_approve — `rookery approve` subcommand implementation."""

import json
import sys
import time
import urllib.request


def cmd_approve(args):
    base = args.url.rstrip("/")
    token = args.token or ""

    def _get(path):
        req = urllib.request.Request(
            base + path, headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            base + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    try:
        pend = _get("/pending").get("pending", [])
    except Exception as e:
        print(f"could not reach {base}/pending: {e}", file=sys.stderr)
        sys.exit(1)

    if not pend:
        print("No pending join requests.")
        return

    print(f"{len(pend)} pending request(s):")
    for i, e in enumerate(pend, 1):
        age = int(time.time() - e.get("requested_at", time.time()))
        print(
            f"  {i}) node={e['node_id']:<24} slug={e['slug']:<24} "
            f"pubkey={e['pubkey_b64'][:24]}...  ({age}s ago)"
        )
    print()

    for i, e in enumerate(pend, 1):
        try:
            choice = (
                input(
                    f"Approve {i}/{len(pend)} (node={e['node_id']}, slug={e['slug']})?"
                    " [y/N/s=skip]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice in ("s", "skip"):
            continue
        decision = "approve" if choice in ("y", "yes") else "deny"
        try:
            out = _post(
                "/approve", {"request_id": e["request_id"], "decision": decision}
            )
            tok = (out.get("token") or "")[:16]
            print(
                f"  -> {decision} ({out.get('status')}, token={tok}...)"
                if tok
                else f"  -> {decision} ({out.get('status')})"
            )
        except Exception as err:
            print(f"  -> error: {err}", file=sys.stderr)
