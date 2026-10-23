#!/usr/bin/env python3
"""LEGACY script: read a node's inbox from the LOCAL mailroom (counterpart to send_mail.py).

Use `rookery inbox` to read and `rookery ack` to mark mail read; this script stays for old callers.
It reads through the mail gate: with a session identity (ROOKERY_SESSION / ROOKERY_NODE) it shows
that identity's mail only, and --node must name it (else exit 2); with no identity it shows
`legacy` mail to --node.


`mailctl.py inbox` is the remote HTTP client and requires --url, so a node
sitting on a local DB had no supported way to read its own mail. It had to be
told to write one, which is exactly what happened the first time an agent was
briefed against this repo.

The read is NON-DESTRUCTIVE by default, matching `mailctl inbox`: messages stay
pending so nothing is lost if the turn dies mid-handling. That is also why the
mail discipline says to track the highest id you have handled and act only on
ids above it — re-reading WILL show you old mail again.

  read_mail.py --node alpha                 pending mail, oldest first
  read_mail.py --node alpha --since 7       only ids above 7
  read_mail.py --node alpha --all           include already-handled mail
  read_mail.py --node alpha --ack           mark what was shown as done
  read_mail.py --node alpha --json          machine-readable
"""
import argparse
import json
import os
import sys

import mail_gate
import rookery as R
import turn_block


def _done_rows(conn, identity, node):
    """Done rows in the same row set `unread` / `unread_legacy` return (same session + legacy)."""
    if identity:
        sid, aid, name = identity["session_id"], identity["agent_id"], identity["name"]
        cond = ("(m.session_id = ? AND m.recipient = ?) OR (m.session_id = ? AND m.recipient IN (?, ?))")
        params = (sid, aid, mail_gate.LEGACY, name, aid)
    else:
        cond, params = "m.session_id = ? AND m.recipient = ?", (mail_gate.LEGACY, node)
    return [dict(r) for r in conn.execute(
        "SELECT m.id, m.sender, m.topic, m.created_at, m.session_id, m.body, m.status "
        "FROM inbox m WHERE m.status='done' AND (" + cond + ")", params)]


def fetch(conn, identity, node, since=0, include_done=False):
    if identity:
        rows = mail_gate.unread(conn, identity, with_body=True)
    else:
        rows = mail_gate.unread_legacy(conn, node, with_body=True)
    rows = [dict(r, status="pending") for r in rows]
    if include_done:
        rows += _done_rows(conn, identity, node)
    rows = [r for r in rows if r["id"] > since]
    rows.sort(key=lambda r: (r["created_at"], r["id"]))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="LEGACY: read a node's inbox (local mailroom). Prefer `rookery inbox` to read "
                    "and `rookery ack` to mark mail read.")
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", type=int, default=0,
                    help="only messages with id greater than this")
    ap.add_argument("--all", action="store_true", dest="include_done",
                    help="include mail already handled, not just pending")
    ap.add_argument("--ack", action="store_true",
                    help="mark the shown messages done (default is a "
                         "non-destructive read)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args()

    conn = R.connect()
    principal = mail_gate.principal_from_env(conn, os.environ, turn_block.gate_root())
    identity = principal[1] if principal else None
    if identity and a.node not in (identity["name"], identity["agent_id"]):
        print(f"--node '{a.node}' is not you: this session identity is '{identity['name']}'. "
              f"Use `rookery inbox` to read your mail.", file=sys.stderr)
        return 2
    rows = fetch(conn, identity, a.node, a.since, a.include_done)

    if a.as_json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print(f"no mail for '{a.node}'" + (f" above id {a.since}" if a.since else ""))
    else:
        print(f"{len(rows)} message(s) for '{a.node}':")
        for r in rows:
            print(f"  #{r['id']}  from {r['sender']}"
                  f"{'  [' + r['topic'] + ']' if r['topic'] else ''}"
                  f"  ({r['status']})")
            for line in (r["body"] or "").splitlines():
                print(f"      {line}")
        print(f"\nhighest id shown: {rows[-1]['id']} — act only on ids above the "
              f"last you handled.")

    pending = [r["id"] for r in rows if r["status"] == "pending"]
    if a.ack and pending:
        if identity:
            mail_gate.ack(conn, identity, pending)
        else:
            mail_gate.ack_legacy(conn, a.node, pending)
        print(f"acked {len(pending)} message(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
