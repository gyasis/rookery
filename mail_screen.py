#!/usr/bin/env python3
"""The screener side of held mail (ROOKERY_HOLD=1).

No args                         -> list held messages awaiting a decision.
--id N --release                -> deliver it (recipient gets woken by the postmaster).
--id N --quarantine REASON      -> quarantine it and bounce the reason to the sender.
     [--note TEXT] [--suggest NODE] [--no-bounce]
--id N --unquarantine           -> overrule a quarantine and deliver after all.
--quarantined [--reason R]      -> list the quarantine folder.
--export                        -> JSONL of every screened message (training/eval labels).

--by NAME records who decided (default: human:<your user name>). A classifier or the comms
monitor passes its own id so its decisions can be scored later. A person's release, quarantine
and unquarantine also write the user calls of the screening report (screen_calls, D20).
"""
import argparse
import getpass
import json
import sys

import rookery as R
import screener_t0


def _line(r):
    body = r["body"].replace("\n", " ")
    return f"  #{r['id']}  {r['sender']} -> {r['recipient']}  topic={r['topic']}  {body[:100]}"


def main():
    ap = argparse.ArgumentParser(description="Screen held Rookery mail.")
    ap.add_argument("--id", type=int, default=None, dest="msg_id")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--release", action="store_true")
    g.add_argument("--quarantine", choices=R.QUARANTINE_REASONS, metavar="REASON",
                   help="one of: " + ", ".join(R.QUARANTINE_REASONS))
    g.add_argument("--unquarantine", action="store_true")
    g.add_argument("--quarantined", action="store_true", help="list quarantined mail")
    g.add_argument("--export", action="store_true", help="JSONL of screening decisions")
    ap.add_argument("--reason", default=None, help="filter for --quarantined")
    ap.add_argument("--note", default=None)
    ap.add_argument("--suggest", default=None, help="recipient the sender should use instead")
    ap.add_argument("--no-bounce", action="store_true")
    ap.add_argument("--by", default=f"human:{getpass.getuser()}", dest="decided_by")
    a = ap.parse_args()
    if a.decided_by.startswith("human:") and not screener_t0.check_decided_by(a.decided_by):
        ap.error(f"--by {a.decided_by!r}: use letters, digits and . _ : @ / - only, with no space")
    conn = R.connect()

    if a.export:
        for r in R.screening_labels(conn):
            print(json.dumps(dict(r)))
        return
    if a.quarantined:
        rows = R.list_quarantined(conn, a.reason)
        print(f"{len(rows)} quarantined" + (f" ({a.reason})" if a.reason else "") + ":")
        for r in rows:
            print(_line(r) + f"  [{r['reason']} by {r['decided_by']}]")
        return
    if a.msg_id is None:
        rows = R.list_held(conn)
        if not rows:
            print("no held mail.")
        for r in rows:
            print(_line(r))
        return

    def calls(action, reason=None):
        # The calls and the status change share one transaction: the status change commits both.
        if a.decided_by.startswith("human:"):
            screener_t0.calls_for_action(conn, a.msg_id, action, reason, a.decided_by)

    try:
        if a.release:
            calls("release")
            R.release(conn, a.msg_id, a.decided_by)
            print(f"released #{a.msg_id}")
        elif a.quarantine:
            calls("quarantine", a.quarantine)
            bid = R.quarantine(conn, a.msg_id, a.quarantine, a.decided_by,
                               note=a.note, suggest=a.suggest, bounce=not a.no_bounce)
            print(f"quarantined #{a.msg_id} ({a.quarantine})"
                  + (f"; bounced to sender as #{bid}" if bid else ""))
        elif a.unquarantine:
            calls("unquarantine")
            R.unquarantine(conn, a.msg_id, a.decided_by)
            print(f"unquarantined #{a.msg_id} -> pending")
        else:
            print(_line(R.get_message(conn, a.msg_id)) if R.get_message(conn, a.msg_id)
                  else f"no such message #{a.msg_id}")
    except ValueError as e:
        conn.rollback()
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
