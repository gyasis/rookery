#!/usr/bin/env python3
"""Rookery node — a headless, own-loop agent node.

The loop is cheap and non-LLM: it polls the mailroom for THIS node's mail and
only wakes the engine when real mail exists (so an idle node costs ~$0). The
engine is either a deterministic `mock` (default, zero API spend — proves the
plumbing) or real headless Claude (`--engine claude` -> `claude -p`).

Wire protocol the engine may emit (identical for mock and claude), one per line:
    MAILTO:<node>:<text>     send mail to another node
    NEEDCRED:<resource>      phone home for a credential, then PAUSE until approved
Lines beginning with `ACK:` are acknowledgements and are never auto-replied to
(loop prevention).

The NEEDCRED pause is a *durable* pause: the node's state lives in the mailroom
DB, so even if this process is killed, a fresh run resumes from the same rows.
While paused it consumes ~nothing. (See README "Pause & resume".)
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

import rookery as R


def log(node_id, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] ({node_id}) {msg}", flush=True)


def summarize(text, n=48):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --- engines ---------------------------------------------------------------
def mock_engine(node_id, messages):
    """Deterministic stand-in for an LLM. It mimics 'the agent read the mail
    and decided what to do' by (a) carrying out any directives the incoming
    mail asked of it and (b) acking the sender. No API, fully reproducible."""
    out = []
    for m in messages:
        body = m["body"]
        if body.startswith("ACK:"):
            continue  # don't react to acknowledgements -> breaks reply loops
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("MAILTO:") or line.startswith("NEEDCRED:"):
                out.append(line)
        out.append(f"MAILTO:{m['sender']}:ACK: {node_id} handled \"{summarize(body)}\"")
    return "\n".join(out)


def claude_engine(node_id, messages):
    """Real headless Claude. Each turn is a fresh `claude -p` subprocess — the
    full coding harness, driven own-loop. The agent talks to the mesh by
    emitting MAILTO:/NEEDCRED: lines, same protocol as mock."""
    if shutil.which("claude") is None:
        log(node_id, "ERROR: `claude` not on PATH. Use --engine mock, or install the CLI.")
        sys.exit(127)
    digest = "\n".join(f"- from {m['sender']}: {m['body']}" for m in messages)
    prompt = (
        f"<ROOKERY> You are mesh node '{node_id}'. New mail:\n{digest}\n\n"
        "Respond concisely. To message another node, emit a line "
        "`MAILTO:<node>:<text>`. To request a credential you lack, emit "
        "`NEEDCRED:<resource>`. Prefix acknowledgements with `ACK:`."
    )
    proc = subprocess.run(
        ["claude", "-p", prompt], capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        log(node_id, f"claude exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


ENGINES = {"mock": mock_engine, "claude": claude_engine}


# --- the phone-home pause --------------------------------------------------
def wait_for_credential(conn, node_id, req_id, poll, max_wait):
    """Block until a human approves/denies the credential request. Cheap: just
    sleeps + heartbeats. Returns the token_ref pointer (never a raw secret)."""
    R.set_status(conn, node_id, "waiting")
    waited = 0
    while True:
        row = R.get_credential(conn, req_id)
        if row["status"] == "approved":
            return row["token_ref"]
        if row["status"] == "denied":
            return None
        R.heartbeat(conn, node_id)
        time.sleep(poll)
        waited += poll
        if max_wait and waited >= max_wait:
            log(node_id, f"credential req #{req_id} timed out after {max_wait}s")
            return None


def handle_directives(conn, node_id, response, poll, max_wait, managed=False):
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("MAILTO:"):
            target, _, text = line[len("MAILTO:"):].partition(":")
            target = target.strip()
            if target:
                R.send(conn, node_id, target, text.strip())
                log(node_id, f"-> mail to {target}: {summarize(text)}")
        elif line.startswith("NEEDCRED:"):
            resource = line[len("NEEDCRED:"):].strip()
            req_id = R.request_credential(conn, node_id, resource)
            if managed:
                # Durable pause: record the need and EXIT. The postmaster will
                # mail the grant later, which wakes a fresh turn. $0 while asleep.
                R.set_status(conn, node_id, "asleep")
                log(node_id, f"phoning home for '{resource}' (req #{req_id}) -- durable pause, exiting until approved")
                return
            # v1 self-poll mode: block in-process until approved.
            log(node_id, f"phoning home for '{resource}' (req #{req_id}) -- PAUSING")
            token = wait_for_credential(conn, node_id, req_id, poll, max_wait)
            if token:
                log(node_id, f"credential granted (token_ref={token}) -- RESUMING")
            else:
                log(node_id, "credential not granted -- continuing without it")
            R.set_status(conn, node_id, "busy")


def run(node_id, engine_name, kind, poll, max_wait, once, managed):
    conn = R.connect()
    R.register_node(conn, node_id, kind=kind, pid=os.getpid())
    log(node_id, f"online (engine={engine_name}, pid={os.getpid()}, managed={managed}). Watching for mail…")
    engine = ENGINES[engine_name]
    try:
        while True:
            R.heartbeat(conn, node_id)
            R.set_status(conn, node_id, "idle")
            msgs = R.fetch_undelivered(conn, node_id)
            if not msgs:
                if once:
                    break
                time.sleep(poll)
                continue
            R.set_status(conn, node_id, "busy")
            R.claim(conn, [m["id"] for m in msgs])  # atomic: never re-inject
            log(node_id, f"woke on {len(msgs)} message(s) -- unprompted")
            response = engine(node_id, msgs)
            handle_directives(conn, node_id, response, poll, max_wait, managed)
            if once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        R.set_status(conn, node_id, "offline")
        log(node_id, "offline")


def main():
    ap = argparse.ArgumentParser(description="Run a Rookery mesh node.")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--engine", choices=list(ENGINES), default="mock")
    ap.add_argument("--kind", default="headless")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between inbox checks")
    ap.add_argument("--max-wait", type=int, default=0, help="credential wait timeout (0=forever)")
    ap.add_argument("--once", action="store_true", help="process one batch then exit")
    ap.add_argument("--managed", action="store_true",
                    help="postmaster mode: on NEEDCRED, exit (durable pause) instead of blocking")
    a = ap.parse_args()
    run(a.node_id, a.engine, a.kind, a.poll, a.max_wait, a.once, a.managed)


if __name__ == "__main__":
    main()
