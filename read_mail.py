#!/usr/bin/env python3
"""Read a node's inbox from the LOCAL mailroom — the counterpart to send_mail.py.

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

import rookery as R


def fetch(conn, node, since=0, include_done=False):
    if include_done:
        sql = ("SELECT * FROM inbox WHERE recipient=? AND id>? "
               "ORDER BY created_at, id")
    else:
        sql = ("SELECT * FROM inbox WHERE recipient=? AND id>? "
               "AND status='pending' ORDER BY created_at, id")
    return conn.execute(sql, (node, since)).fetchall()


def main():
    ap = argparse.ArgumentParser(description="Read a node's inbox (local mailroom).")
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
    rows = fetch(conn, a.node, a.since, a.include_done)

    if a.as_json:
        print(json.dumps([dict(r) for r in rows], indent=2))
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

    if a.ack and rows:
        R.ack(conn, [r["id"] for r in rows])
        print(f"acked {len(rows)} message(s).")


if __name__ == "__main__":
    main()
