#!/usr/bin/env python3
"""Drop a message into the mailroom for a node (or a human), through the mail gate.

Exit: 0 sent, 1 not sent (quarantined or held, loop-held too), 2 refused or bad input,
4 database busy. A send that fails for another reason exits 4 (Rookery stores nothing, so the
caller can send again). A sender with no session exits 0 (mail goes to `legacy`).
"""
import argparse
import json
import os
import sqlite3
import sys

import mail_gate
import turn_block


def _bounce_text(conn, res):
    """The note and the suggestion the gate stored for a held mail, as lines to print."""
    row = conn.execute("SELECT body FROM inbox WHERE id=?", (res.get("bounce_id"),)).fetchone() \
        if res.get("bounce_id") else None
    if row is None:
        return []
    try:
        bounce = json.loads(row["body"])
    except ValueError:
        return []
    lines = []
    if bounce.get("note"):
        lines.append(f"note: {bounce['note']}")
    if bounce.get("suggest"):
        s = bounce["suggest"]
        lines.append("suggest: " + (", ".join(s) if isinstance(s, list) else str(s)))
    return lines


def main():
    ap = argparse.ArgumentParser(description="Send mail into the Rookery mesh.")
    ap.add_argument("--to", required=True, dest="recipient")
    ap.add_argument("--from", default=None, dest="sender")
    ap.add_argument("--topic", default=None)
    ap.add_argument("--body", required=True, help="message body (may contain MAILTO:/NEEDCRED: directives)")
    a = ap.parse_args()
    root = turn_block.gate_root()      # one gate log for every path (D15); never fails on the cwd name
    try:
        conn = turn_block.open_db()
        try:
            principal = mail_gate.principal_from_env(conn, os.environ, root)
            res = mail_gate.submit(conn, path="send-mail", principal=principal, raw_sender="human",
                                   to=a.recipient, body=a.body, topic=a.topic or "",
                                   asked_sender=a.sender, root=root)
            lines = [] if res["outcome"] in ("sent", "db-busy", "send-failed", "refused") \
                else _bounce_text(conn, res)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        print("database busy. Rookery sent nothing. Run send_mail.py again.", file=sys.stderr)
        return 4
    except Exception:       # the exception text can hold a path or the body: print a fixed line
        print("send failed. Rookery sent nothing. Run send_mail.py again.", file=sys.stderr)
        return 4
    outcome, reason = res["outcome"], res["reason"]
    if outcome == "sent":
        who = a.sender or (principal[1]["name"] if principal else "human")
        print(f"sent #{res['msg_id']}: {who} -> {a.recipient}")
        return 0
    detail = f"{outcome}" + (f" ({reason})" if reason else "")
    if outcome == "db-busy":
        print("database busy. Rookery sent nothing. Run send_mail.py again.", file=sys.stderr)
        return 4
    if outcome == "send-failed":     # D17: nothing stored; same code as a busy database
        print(f"send failed: {reason}. Rookery sent nothing.", file=sys.stderr)
        return 4
    if outcome == "refused":
        print(f"refused ({reason}). Rookery sent nothing.", file=sys.stderr)
        return 2
    print(f"not sent: {detail}")
    for line in lines:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
