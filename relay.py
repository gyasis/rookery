#!/usr/bin/env python3
"""Rookery relay — federation between mailrooms (the "SMTP" of the mesh).

Each mailroom runs its OWN relay. The relay watches its local mailroom for mail
addressed to `node@<remote>` and forwards it to that remote mailroom's sidecar
`/send`, rewriting the sender to `<orig>@<self>` so replies route back. Mailroom
ids resolve via a directory (JSON), e.g.:

    { "home": {"url": "http://192.168.0.146:8765", "token": "..."},
      "mac":  {"url": "http://192.168.0.159:8765", "token": "..."} }

Address format: `node@mailroom` ('@' is reserved as the separator; bare `node`
= local). Multi-hop is bounded by a hop count carried in `topic` (relay/<n>).

Env: ROOKERY_DB (local mailroom), ROOKERY_MAILROOM (this mailroom's id),
     ROOKERY_MAILROOMS (directory path, default mailrooms.json next to this file).
"""
import argparse
import json
import os
import ssl
import time
import urllib.request

import rookery as R

_HERE = os.path.dirname(os.path.abspath(__file__))
DIR_PATH = os.environ.get("ROOKERY_MAILROOMS", os.path.join(_HERE, "mailrooms.json"))
MAX_HOPS = 4


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] (relay) {msg}", flush=True)


def load_directory():
    try:
        d = json.load(open(DIR_PATH))
    except Exception:
        d = {}
    self_id = os.environ.get("ROOKERY_MAILROOM") or d.get("self")
    return self_id, d


def _post(base_url, body, token=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url.rstrip("/") + "/send", data=data,
                                 method="POST", headers=headers)
    ctx = None
    if base_url.startswith("https") and os.environ.get("ROOKERY_INSECURE_TLS"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read() or b"{}")


def _hops(topic):
    if topic and topic.startswith("relay/"):
        try:
            return int(topic.split("/", 1)[1])
        except ValueError:
            return 0
    return 0


def run(self_id, directory, poll):
    conn = R.connect()
    log(f"online as mailroom '{self_id}'. routes: {[k for k in directory if isinstance(directory.get(k), dict)]}")
    while True:
        rows = conn.execute(
            "SELECT * FROM inbox WHERE status='pending' AND recipient LIKE '%@%' ORDER BY id"
        ).fetchall()
        for r in rows:
            local, _, mr = r["recipient"].partition("@")
            if not mr or mr == self_id:
                # node@self -> normalize to a bare local recipient so a local node gets it
                conn.execute("UPDATE inbox SET recipient=? WHERE id=?", (local, r["id"]))
                conn.commit()
                continue
            entry = directory.get(mr)
            if not isinstance(entry, dict) or not entry.get("url"):
                continue  # unknown mailroom -> leave pending (could DLQ later)
            if _hops(r["topic"]) >= MAX_HOPS:
                R.ack(conn, [r["id"]])
                log(f"dropped #{r['id']} (>{MAX_HOPS} hops) -> loop guard")
                continue
            # reply-routable sender: <orig>@<self>
            sender = r["sender"] if "@" in r["sender"] else f"{r['sender']}@{self_id}"
            try:
                _post(entry["url"], {"sender": sender, "recipient": local,
                                     "body": r["body"], "topic": f"relay/{_hops(r['topic'])+1}"},
                      entry.get("token"))
                R.ack(conn, [r["id"]])  # relayed -> done locally
                log(f"relayed #{r['id']}: {sender} -> {local}@{mr}")
            except Exception as e:
                log(f"relay of #{r['id']} to '{mr}' FAILED ({e}); leaving pending")
        time.sleep(poll)


def main():
    ap = argparse.ArgumentParser(description="Run the Rookery federation relay.")
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    self_id, directory = load_directory()
    if not self_id:
        raise SystemExit("set ROOKERY_MAILROOM (this mailroom's id) or a 'self' key in the directory")
    try:
        run(self_id, directory, a.poll)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
