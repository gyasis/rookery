#!/usr/bin/env python3
"""Drop a message into the mailroom for a node (or a human)."""
import argparse
import rookery as R


def main():
    ap = argparse.ArgumentParser(description="Send mail into the Rookery mesh.")
    ap.add_argument("--to", required=True, dest="recipient")
    ap.add_argument("--from", default="human", dest="sender")
    ap.add_argument("--topic", default=None)
    ap.add_argument("--body", required=True, help="message body (may contain MAILTO:/NEEDCRED: directives)")
    a = ap.parse_args()
    conn = R.connect()
    mid = R.send(conn, a.sender, a.recipient, a.body, a.topic)
    print(f"sent #{mid}: {a.sender} -> {a.recipient}")


if __name__ == "__main__":
    main()
