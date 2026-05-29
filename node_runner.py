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
import threading
import time
import uuid

import rookery as R
import security  # inbound message inspection (swappable policy)


def _heartbeat_loop(node_id, stop_evt, interval=10):
    """Keep a node's last_seen fresh WHILE it works (its own DB connection, since
    the engine call may be long — e.g. a slow local model). Lets the supervisor
    tell 'slow but alive' apart from 'crashed'."""
    hc = R.connect()
    while not stop_evt.wait(interval):
        try:
            R.heartbeat(hc, node_id)
        except Exception:
            pass


def log(node_id, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] ({node_id}) {msg}", flush=True)


def summarize(text, n=48):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --- engines ---------------------------------------------------------------
def mock_engine(node_id, new_msgs, history=None, **kw):
    """Deterministic stand-in for an LLM. It mimics 'the agent read the mail
    and decided what to do' by (a) carrying out any directives the incoming
    mail asked of it and (b) acking the sender. No API, fully reproducible.
    (history/session kwargs ignored — the mock needs no memory.)"""
    out = []
    for m in new_msgs:
        body = m["body"]
        if body.startswith("ACK:"):
            continue  # don't react to acknowledgements -> breaks reply loops
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("RESEARCH:"):
                # simulate "search a topic, hand findings to <next>" (real version
                # = a Claude node calling the DeepLake MCP). Format RESEARCH:<topic>:<next>
                topic, _, nxt = line[len("RESEARCH:"):].partition(":")
                topic = topic.strip()
                nxt = nxt.strip() or "writer"
                out.append(f"MAILTO:{nxt}:FINDINGS({topic}): [mock] 3 key points on {topic} retrieved.")
            elif line.startswith("MAILTO:") or line.startswith("NEEDCRED:"):
                out.append(line)
        out.append(f"MAILTO:{m['sender']}:ACK: {node_id} handled \"{summarize(body)}\"")
    return "\n".join(out)


def _digest(messages):
    return "\n".join(f"- from {m['sender']}: {m['body']}" for m in messages)


def _mesh_prompt(node_id, new_msgs, history=None):
    """Same instructions for every real engine -> same wire protocol as mock.
    For persistent agents, `history` is the rehydrated conversation (memory)."""
    p = f"You are agent node '{node_id}' in a multi-agent mesh.\n"
    if history:
        convo = "\n".join(f"  [{m['sender']}->{m['recipient']}] {m['body']}" for m in history)
        p += f"Conversation so far (your memory, rehydrated):\n{convo}\n\n"
    p += (
        f"NEW mail to respond to now:\n{_digest(new_msgs)}\n\n"
        "Reply with a SHORT plain-text message (no markdown code fences).\n"
        "Mesh directives — each on its OWN line, exact format:\n"
        "  MAILTO:<node>:<message>   send mail to another node\n"
        "  NEEDCRED:<resource>       request a credential/secret you lack\n"
        "Prefix an acknowledgement with ACK:. "
        "Output only your message plus any directive lines."
    )
    return p


def claude_engine(node_id, new_msgs, history=None, session_id=None, resume=False,
                  model=None, allowed_tools=None, **kw):
    """Real headless Claude — a fresh `claude -p` turn. Prompt goes via STDIN
    (avoids the variadic --mcp-config eating a positional prompt). MCP stays ON
    so tool-using agents (e.g. DeepLake search) work; pass allowed_tools so the
    agent can fire those MCP tools non-interactively. Persistent agents pass a
    stable session_id + --resume to keep LLM memory across kills.
    Note: ~115s startup per call in this env (SessionStart hooks), not fixed by
    model/MCP — a long-lived session (Agent SDK) is the real latency fix."""
    if shutil.which("claude") is None:
        log(node_id, "ERROR: `claude` not on PATH. Use --engine mock.")
        sys.exit(127)
    cmd = ["claude", "-p"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]   # safe with stdin prompt (no positional to eat)
    if resume and session_id:
        cmd += ["--resume", session_id]
        prompt = _mesh_prompt(node_id, new_msgs, None)   # session already holds history
    elif session_id:
        cmd += ["--session-id", session_id]
        prompt = _mesh_prompt(node_id, new_msgs, history)
    else:
        prompt = _mesh_prompt(node_id, new_msgs, history)
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        log(node_id, f"claude exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def codex_engine(node_id, new_msgs, history=None, **kw):
    """Real OpenAI Codex — non-interactive `codex exec`, read-only sandbox.
    `-o` writes just the final assistant message, so we capture it cleanly."""
    if shutil.which("codex") is None:
        log(node_id, "ERROR: `codex` not on PATH. Use --engine mock/claude.")
        sys.exit(127)
    import tempfile
    fd, out_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        proc = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", "-s", "read-only",
             "--color", "never", "-o", out_path, _mesh_prompt(node_id, new_msgs, history)],
            capture_output=True, text=True, timeout=300,
        )
        msg = ""
        try:
            with open(out_path) as fh:
                msg = fh.read().strip()
        except OSError:
            pass
        if not msg:
            msg = proc.stdout
        if proc.returncode != 0 and not msg:
            log(node_id, f"codex exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        return msg
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def gemini_engine(node_id, new_msgs, history=None, model=None, **kw):
    """Real Google Gemini — non-interactive `gemini --skip-trust -p`. Same mesh
    protocol. (--skip-trust is required for headless/automated use.)"""
    if shutil.which("gemini") is None:
        log(node_id, "ERROR: `gemini` not on PATH. Use --engine mock/claude/codex.")
        sys.exit(127)
    cmd = ["gemini", "--skip-trust"]
    if model:
        cmd += ["-m", model]
    cmd += ["-p", _mesh_prompt(node_id, new_msgs, history)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        log(node_id, f"gemini exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


ENGINES = {"mock": mock_engine, "claude": claude_engine, "codex": codex_engine,
           "gemini": gemini_engine}


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
    """Returns (paused, acted): paused=True if a managed NEEDCRED exited the turn;
    acted=True if the agent sent mail or requested a credential."""
    acted = False
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("MAILTO:"):
            target, _, text = line[len("MAILTO:"):].partition(":")
            target = target.strip()
            if target:
                R.send(conn, node_id, target, text.strip())
                log(node_id, f"-> mail to {target}: {summarize(text)}")
                acted = True
        elif line.startswith("NEEDCRED:"):
            resource = line[len("NEEDCRED:"):].strip()
            req_id = R.request_credential(conn, node_id, resource)
            acted = True
            if managed:
                # Durable pause: record the need and EXIT. The postmaster will
                # mail the grant later, which wakes a fresh turn. $0 while asleep.
                R.set_status(conn, node_id, "asleep")
                log(node_id, f"phoning home for '{resource}' (req #{req_id}) -- durable pause, exiting until approved")
                return True, acted
            # v1 self-poll mode: block in-process until approved.
            log(node_id, f"phoning home for '{resource}' (req #{req_id}) -- PAUSING")
            token = wait_for_credential(conn, node_id, req_id, poll, max_wait)
            if token:
                log(node_id, f"credential granted (token_ref={token}) -- RESUMING")
            else:
                log(node_id, "credential not granted -- continuing without it")
            R.set_status(conn, node_id, "busy")
    return False, acted


def screen(conn, node_id, msgs):
    """Run each inbound message through the security policy BEFORE the agent sees
    it (the prompt-injection / content seam, covering ALL mail — internal node->
    node included). Rejected mail is quarantined (acked out of the queue) and the
    sender is told. No-op under DefaultPolicy."""
    pol = security.get_policy()
    allowed = []
    for m in msgs:
        d = pol.inspect_inbound(m["sender"], node_id, m["body"])
        if d:
            allowed.append(m)
        else:
            log(node_id, f"BLOCKED inbound #{m['id']} from {m['sender']}: {d.reason}")
            R.send(conn, node_id, m["sender"], f"REJECTED by policy: {d.reason}")
            R.ack(conn, [m["id"]])  # quarantine: remove from the pending queue
    return allowed


def run(node_id, engine_name, kind, poll, max_wait, once, managed, lifecycle, idle_timeout,
        allowed_tools=None, model=None):
    conn = R.connect()
    R.register_node(conn, node_id, kind=kind, pid=os.getpid(), lifecycle=lifecycle)
    log(node_id, f"online (engine={engine_name}, pid={os.getpid()}, lifecycle={lifecycle}, "
                 f"managed={managed}, idle_timeout={idle_timeout}s). Watching for mail…")
    engine = ENGINES[engine_name]
    idle = 0.0
    try:
        while True:
            R.heartbeat(conn, node_id)
            R.set_status(conn, node_id, "idle")
            new_msgs = R.fetch_undelivered(conn, node_id)
            if new_msgs:
                new_msgs = screen(conn, node_id, new_msgs)  # policy vets inbound
            if not new_msgs:
                if once:
                    break
                if idle_timeout and idle >= idle_timeout:
                    log(node_id, f"idle {int(idle)}s >= warm window -- sleeping ($0); rehydrates on next wake")
                    break
                time.sleep(poll)
                idle += poll
                continue
            idle = 0.0
            R.set_status(conn, node_id, "busy")
            # Persistent agents rehydrate their full thread from the DB (memory).
            history = None
            if lifecycle == "persistent":
                thread = R.fetch_thread(conn, node_id)
                new_ids = {m["id"] for m in new_msgs}
                history = [m for m in thread if m["id"] not in new_ids]
                if history:
                    log(node_id, f"rehydrated {len(history)} prior message(s) from the mailroom")
            engine_kw = {}
            if engine_name == "claude":
                if allowed_tools:
                    engine_kw["allowed_tools"] = allowed_tools
                if model:
                    engine_kw["model"] = model
                # Persistent claude nodes keep LLM memory via a stable session id
                # (stored in the DB so it survives the kill -> --resume on next wake).
                if lifecycle == "persistent":
                    node = R.get_node(conn, node_id)
                    sid = node["session_ref"] if node else None
                    if not sid:
                        sid = str(uuid.uuid4())
                        R.set_session_ref(conn, node_id, sid)
                        engine_kw.update(session_id=sid, resume=False)
                    else:
                        engine_kw.update(session_id=sid, resume=True)
            ids = [m["id"] for m in new_msgs]
            R.claim(conn, ids)  # in-flight: won't re-inject; requeued only if we go silent
            log(node_id, f"woke on {len(new_msgs)} message(s) -- unprompted")
            # heartbeat WHILE the engine runs (a slow model is alive, not crashed)
            _stop = threading.Event()
            threading.Thread(target=_heartbeat_loop, args=(node_id, _stop), daemon=True).start()
            try:
                response = engine(node_id, new_msgs, history, **engine_kw)
            finally:
                _stop.set()
            paused, acted = handle_directives(conn, node_id, response, poll, max_wait, managed)
            # Fallback: an ephemeral worker that emitted no directive still returns
            # its result to whoever asked (robust round-trip even if the model
            # forgets the MAILTO: protocol).
            if not acted and lifecycle == "ephemeral" and response and response.strip():
                reply_to = new_msgs[-1]["sender"]
                if reply_to and reply_to != node_id:
                    R.send(conn, node_id, reply_to, response.strip()[:4000])
                    log(node_id, f"-> auto-reply to {reply_to}: {summarize(response)}")
            R.ack(conn, ids)  # turn handled these -> done
            if once or paused:
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
    ap.add_argument("--lifecycle", choices=["ephemeral", "persistent"], default="ephemeral",
                    help="ephemeral=no memory; persistent=rehydrate full thread from the mailroom")
    ap.add_argument("--idle-timeout", type=float, default=0.0,
                    help="persistent warm window: exit after this many idle seconds (0=never)")
    ap.add_argument("--allowed-tools", default="",
                    help="space/comma-separated MCP tools the claude agent may fire, "
                         "e.g. mcp__deeplakesearch__retrieve_context")
    ap.add_argument("--model", default=None, help="claude model override (e.g. haiku)")
    a = ap.parse_args()
    allowed = [t for t in a.allowed_tools.replace(",", " ").split() if t]
    run(a.node_id, a.engine, a.kind, a.poll, a.max_wait, a.once, a.managed,
        a.lifecycle, a.idle_timeout, allowed_tools=allowed, model=a.model)


if __name__ == "__main__":
    main()
