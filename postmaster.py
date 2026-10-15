#!/usr/bin/env python3
"""Rookery postmaster — the central watcher (mode B, additive to v1).

The agents stay ASLEEP. This is the only always-on process. It is cheap and
non-LLM: it polls the whole mailroom and, when mail lands for a managed node,
it WAKES that node by spawning a single one-shot turn
(`node_runner --once --managed`). The node processes its mail and exits — back
to $0, nothing running. When a credential request is approved, the postmaster
mails the grant to the waiting node, which naturally wakes it.

This is the "polling agent that says 'you've got mail' and wakes the agent"
model. v1 (self-polling nodes) is untouched; this is the richer orchestration
where agents can be truly paused and still get driven.

  $0 while asleep · survives reboot (state is in the mailroom) · one poller, N
  sleeping agents.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import rookery as R
import rookery_identity
import turn_block

_MENTION = re.compile(r"@([A-Za-z0-9_-]+)")

_HERE = os.path.dirname(os.path.abspath(__file__))
NODE_RUNNER = os.path.join(_HERE, "node_runner.py")
SDK_NODE = os.path.join(_HERE, "sdk_node.py")
# A node heartbeats while it works; requeue its in-flight mail only after it has
# been silent this long (genuinely dead) — NOT because a turn is slow.
NODE_DEAD_AFTER = 60


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] (postmaster) {msg}", flush=True)


def has_mail(conn, node):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM inbox WHERE recipient=? AND status='pending'", (node,)
    ).fetchone()
    return row["c"] > 0


def wake(node, engine, lifecycle, idle_timeout, allowed_tools="", model=None, mcp=""):
    if engine == "claude-sdk":
        # warm long-lived Claude session (hooks off) — kills the ~115s cold start.
        cmd = [sys.executable, SDK_NODE, "--node-id", node,
               "--idle-timeout", str(idle_timeout), "--poll", "1"]
        if allowed_tools:
            cmd += ["--allowed-tools", allowed_tools]
        if model:
            cmd += ["--model", model]
        if mcp:
            cmd += ["--mcp", mcp]
        proc = subprocess.Popen(cmd)
        log(f"mail for '{node}' -> woke a WARM SDK session (window {idle_timeout:g}s, pid {proc.pid})")
        return proc
    cmd = [sys.executable, NODE_RUNNER, "--node-id", node, "--engine", engine, "--managed"]
    if lifecycle == "persistent":
        # warm window: self-polls and self-reaps after idle_timeout; rehydrates from DB.
        cmd += ["--lifecycle", "persistent", "--idle-timeout", str(idle_timeout)]
    else:
        cmd += ["--once"]   # ephemeral: one turn then gone, no memory
    if allowed_tools:
        cmd += ["--allowed-tools", allowed_tools]   # only claude nodes use it
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd)
    tag = (f"WARM persistent (window {idle_timeout:g}s)"
           if lifecycle == "persistent" else "one-shot ephemeral")
    log(f"mail for '{node}' -> woke a {tag} turn (pid {proc.pid})")
    return proc


_TOKEN = re.compile(r"\{(value|text)\}")
WAKE_TIMEOUT = 10


def _fill(argv, value, text):
    """Fill {value}/{text} in each argv element in one pass (no re-expansion)."""
    vals = {"value": value, "text": text}
    return [_TOKEN.sub(lambda m: vals[m.group(1)], a) for a in argv]


_PANE_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


def _pane_field(s):
    """A field typed into a pane that may be a shell keeps only safe-name characters."""
    return _PANE_UNSAFE.sub("", turn_block.notice_field(s)).strip() or "?"


def _notice(conn, ident, rows):
    """Push text: names the sender(s) and task(s), points at `rookery inbox`, no body.
    Every field keeps only safe-name characters (_pane_field), at most 5 senders and 5 tasks are named,
    and the whole text is capped at 400 characters: it is typed into a live pane as one argv."""
    senders, tasks, names = [], [], {}
    for r in rows:
        n = names.get(r["sender"])
        if n is None:
            who = conn.execute("SELECT name FROM identities WHERE agent_id=?",
                               (r["sender"],)).fetchone()
            n = names[r["sender"]] = _pane_field(who["name"] if who else r["sender"])
        if n not in senders:
            senders.append(n)
        t = _pane_field(r["topic"]) if r["topic"] else ""
        if t and t not in tasks:
            tasks.append(t)
    head = (f"[rookery] new mail for {_pane_field(ident['name'])} from "
            f"{turn_block.notice_list(senders)} about "
            f"{turn_block.notice_list(tasks) or 'no task'}")
    return turn_block.cap_notice(head, ". Read it with: rookery inbox")


BACKOFF_START, BACKOFF_MAX = 30, 600


def _load_pushed(root, agent_id):
    """(pushed_ids or None, last_id). New files carry "pushed_ids"; an old {"last_id": N} file
    means every id <= N was pushed."""
    try:
        with open(os.path.join(root, "postmaster", "pushed", agent_id + ".json")) as fh:
            data = json.load(fh)
        ids = data.get("pushed_ids")
        ids = {int(x) for x in ids} if isinstance(ids, list) else None
        return ids, int(data.get("last_id", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return None, 0


def wake_identities(conn, root, config, run=subprocess.run, skip_logged=None, backoff=None,
                    now=time.time):
    """Push a notice into each idle identity (open session) with pending mail not yet announced
    in <root>/postmaster/pushed/<agent_id>.json (D10, FR-018, FR-028). The file holds the pending
    ids already announced, so mail released from held/quarantined (an older id) is pushed too.

    Never writes state/turn.json or anything under agents/. The pushed file is written only
    after EVERY command of the wake entry returned 0 within the timeout. On failure
    `wake-failed` is logged once per (agent, newest new id, error) and that agent is not retried
    for 30 s, doubling to 600 s (backoff: a dict kept by the caller). no-idle-state /
    no-wake-binding are logged once per (agent, newest new mail id) via skip_logged.
    Returns the number of identities pushed."""
    skip_logged = {} if skip_logged is None else skip_logged
    backoff = {} if backoff is None else backoff
    wake_cfg = (config or {}).get("wake") or {}
    pushed = 0
    ids = conn.execute(
        "SELECT i.agent_id, i.name, i.session_id, s.name AS sname FROM identities i "
        "JOIN rookery_sessions s ON s.session_id=i.session_id WHERE s.state='open' "
        "ORDER BY i.created_at, i.rowid").fetchall()
    for ident in ids:
        aid = ident["agent_id"]
        done_ids, last = _load_pushed(root, aid)
        pending = conn.execute(
            "SELECT id, sender, topic FROM inbox WHERE recipient=? AND status='pending' "
            "ORDER BY id", (aid,)).fetchall()
        pending_ids = [r["id"] for r in pending]
        if done_ids is None:
            done_ids = {i for i in pending_ids if i <= last}
        rows = [r for r in pending if r["id"] not in done_ids]
        if not rows:
            if done_ids - set(pending_ids):      # forget ids that left pending (read, requeued)
                _write_pushed(root, aid, pending_ids, [i for i in pending_ids if i in done_ids])
            continue
        newest = rows[-1]["id"]
        log_f = dict(session=ident["sname"], agent_id=aid, name=ident["name"])

        def skip(reason):
            if skip_logged.get((aid, reason), 0) < newest:
                skip_logged[(aid, reason)] = newest
                turn_block.log_event(root, "postmaster", outcome=reason, mail_id=newest, **log_f)

        if not (turn_block.is_safe_name(ident["sname"]) and turn_block.is_safe_name(ident["name"])):
            skip("unsafe-name")
            continue
        tpath = os.path.join(root, "agents", ident["sname"], ident["name"], "state", "turn.json")
        try:
            with open(tpath) as fh:
                state = json.load(fh).get("state")
        except (OSError, ValueError, AttributeError):
            skip("no-idle-state")
            continue
        if state != "idle":
            continue                       # busy: wait quietly for the turn to end
        entry = None
        for b in conn.execute(
                "SELECT kind, value FROM bindings WHERE session_id=? AND owner_type='identity' "
                "AND owner_id=? ORDER BY created_at, rowid", (ident["session_id"], aid)):
            if wake_cfg.get(b["kind"]):
                entry, kind, value = wake_cfg[b["kind"]], b["kind"], b["value"]
                break
        if entry is None:
            skip("no-wake-binding")
            continue
        bo = backoff.get(aid)
        if bo and now() < bo["until"] and bo["newest"] == newest:
            continue                       # a recent wake failed: wait before typing again
        text = _notice(conn, ident, rows)
        err = None
        for argv in entry:
            try:
                res = run(_fill(argv, value, text), shell=False, timeout=WAKE_TIMEOUT,
                          capture_output=True)
                if res.returncode != 0:
                    err = f"{argv[0]} exited {res.returncode}"
            except (OSError, subprocess.SubprocessError) as exc:
                err = f"{argv[0]}: {exc}"
            if err:
                break
        if err:
            delay = min(bo["delay"] * 2, BACKOFF_MAX) if bo else BACKOFF_START
            prev_key = bo["logged"] if bo else None
            key = (newest, err)
            backoff[aid] = {"until": now() + delay, "delay": delay, "newest": newest, "logged": key}
            if key != prev_key:
                turn_block.log_event(root, "postmaster", outcome="wake-failed", reason=err,
                                     kind=kind, mail_id=newest, retry_in=delay, **log_f)
            continue
        backoff.pop(aid, None)
        _write_pushed(root, aid, pending_ids, pending_ids)
        turn_block.log_event(root, "postmaster", outcome="pushed", kind=kind,
                             mail_id=newest, count=len(rows), **log_f)
        pushed += 1
    return pushed


def _write_pushed(root, agent_id, pending_ids, announced):
    """pushed/<agent_id>.json: the announced ids still pending (bounded by the pending count);
    last_id is kept for readers of the old format."""
    turn_block.write_json_atomic(
        os.path.join(root, "postmaster", "pushed", agent_id + ".json"),
        {"last_id": max(pending_ids, default=0), "pushed_ids": sorted(announced)})


def run(node_engine, persistent_set, max_warm, idle_timeout, poll, allowed_tools="", model=None, mcp="", root=None):
    conn = R.connect()
    wake_conn = turn_block.open_db() if root else None   # same db_path() as the hooks (D6)
    skip_logged = {}
    backoff = {}
    nodes = list(node_engine)

    def lifecycle(n):
        # claude-sdk nodes are inherently warm/persistent (they hold a live session)
        if node_engine.get(n) == "claude-sdk" or n in persistent_set:
            return "persistent"
        return "ephemeral"

    # register the roster up front so discovery (the A2A agent card) lists them
    for n in nodes:
        kind = "sdk" if node_engine[n] == "claude-sdk" else node_engine[n]
        R.register_node(conn, n, kind=kind, lifecycle=lifecycle(n))
        R.set_status(conn, n, "offline")

    log(f"online. managing {node_engine}. persistent={sorted(persistent_set)} "
        f"(max_warm={max_warm}, warm window={idle_timeout:g}s). agents sleep until mail.")
    running = {}        # node_id -> Popen (at most one live turn per node)
    woke_at = {}        # node_id -> last wake time (for warm-pool LRU eviction)
    granted_seen = set()
    last_mention_id = 0
    try:
        while True:
            # 0. durability: return mail claimed by a node that has gone SILENT
            #    (dead) — a slow-but-heartbeating node keeps its claim.
            requeued = R.requeue_stale(conn, NODE_DEAD_AFTER)
            if requeued:
                log(f"requeued {requeued} in-flight message(s) (claiming node went silent)")

            # 1. reap finished turns (ephemeral one-shots, or persistent warm windows that timed out)
            for n, p in list(running.items()):
                if p.poll() is not None:
                    del running[n]
                    log(f"'{n}' finished its turn -> asleep ($0)")

            # 2. approved credentials -> mail the grant to the waiting node
            for r in conn.execute(
                "SELECT * FROM credential_requests WHERE status='approved'"
            ).fetchall():
                if r["id"] in granted_seen:
                    continue
                granted_seen.add(r["id"])
                if r["node_id"] in nodes:
                    R.send(conn, "postmaster", r["node_id"],
                           f"CRED_GRANTED:{r['resource']}:{r['token_ref']}")
                    log(f"credential #{r['id']} approved -> mailed grant to '{r['node_id']}'")

            # 3. "they're talking about you": notify a managed node @mentioned in
            #    new mail it isn't the recipient of (scan each message once).
            for r in conn.execute(
                "SELECT * FROM inbox WHERE id > ? ORDER BY id", (last_mention_id,)
            ).fetchall():
                last_mention_id = max(last_mention_id, r["id"])
                body = r["body"] or ""
                # skip postmaster notices and acks (acks often echo the original
                # text, which would re-trigger the same mention)
                if r["sender"] == "postmaster" or body.startswith("ACK:"):
                    continue
                for m in set(_MENTION.findall(body)):
                    if m in nodes and m != r["recipient"] and m != r["sender"]:
                        R.send(conn, "postmaster", m,
                               f"MENTION by {r['sender']} (in mail to {r['recipient']}): "
                               f"{(r['body'] or '')[:140]}")
                        log(f"@{m} mentioned by {r['sender']} -> notified")

            # 4. wake any managed node that has mail and isn't already running
            for n in nodes:
                if n in running or not has_mail(conn, n):
                    continue
                if lifecycle(n) == "persistent":
                    warm = [x for x in running if lifecycle(x) == "persistent"]
                    if len(warm) >= max_warm:
                        # warm pool full -> evict the least-recently-woken partner.
                        # Its context is safe in the DB; it rehydrates when next needed.
                        victim = min(warm, key=lambda x: woke_at.get(x, 0))
                        log(f"warm pool full ({len(warm)}/{max_warm}) -> evicting '{victim}' (rehydrates later)")
                        running[victim].terminate()
                        continue   # free the slot this tick; wake n next tick
                running[n] = wake(n, node_engine[n], lifecycle(n), idle_timeout,
                                  allowed_tools, model, mcp)
                woke_at[n] = time.time()

            # 5. push a notice into idle identities with unread mail (D10)
            if wake_conn is not None:
                try:
                    wake_identities(wake_conn, root, rookery_identity.load_config(persist=False),
                                    skip_logged=skip_logged, backoff=backoff)
                except Exception as exc:     # never kill the loop; retried next tick
                    turn_block.log_event(root, "postmaster", outcome="wake-step-error",
                                         reason=str(exc))

            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        for p in running.values():
            p.terminate()
        log("offline")


def main():
    ap = argparse.ArgumentParser(description="Run the Rookery central postmaster.")
    ap.add_argument("--nodes", required=True,
                    help="roster, comma-separated. Per-node engine with "
                         "name=engine, e.g. architect=claude,reviewer=codex")
    ap.add_argument("--engine", choices=["mock", "claude", "codex", "gemini", "claude-sdk"],
                    default="mock",
                    help="default engine for roster entries with no =engine "
                         "(claude-sdk = warm long-lived session, no cold start)")
    ap.add_argument("--persistent", default="",
                    help="comma list of nodes that are persistent power-partners "
                         "(rehydrate from DB + join the warm pool); others are ephemeral")
    ap.add_argument("--max-warm", type=int, default=5,
                    help="max concurrently-warm persistent agents (the warm pool cap)")
    ap.add_argument("--idle-timeout", type=float, default=600.0,
                    help="persistent warm window in seconds before a partner sleeps ($0)")
    ap.add_argument("--allowed-tools", default="",
                    help="MCP tools claude nodes may fire, e.g. mcp__deeplakesearch__retrieve_context")
    ap.add_argument("--model", default=None, help="claude model override for all claude nodes")
    ap.add_argument("--mcp", default="",
                    help="comma list of MCP servers (from ~/.claude.json) to attach to claude-sdk nodes")
    ap.add_argument("--root", default=None,
                    help="project root for the identity wake step (default: ROOKERY_ROOT, else "
                         "--project, else the current folder name)")
    ap.add_argument("--project", default=None, help="project name (root = ~/.rookery/projects/NAME)")
    ap.add_argument("--poll", type=float, default=0.5)
    a = ap.parse_args()
    node_engine = {}
    for entry in a.nodes.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, eng = entry.split("=", 1)
            node_engine[name.strip()] = eng.strip()
        else:
            node_engine[entry] = a.engine
    persistent_set = {x.strip() for x in a.persistent.split(",") if x.strip()}
    try:
        root = os.path.abspath(a.root or turn_block.project_root(
            a.project or os.path.basename(os.getcwd())))
    except ValueError:      # unsafe folder name and no ROOKERY_ROOT: roster steps only
        root = None
    run(node_engine, persistent_set, a.max_warm, a.idle_timeout, a.poll,
        allowed_tools=a.allowed_tools, model=a.model, mcp=a.mcp, root=root)


if __name__ == "__main__":
    main()
