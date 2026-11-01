#!/usr/bin/env python3
"""Rookery postmaster — the wake step for session identities.

It scans the outbox roots and types a one-line notice into the pane of an idle identity that has
unread mail. `rookery sweep --session S` runs the periodic safety-net wake of one session.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

import plugins
import rookery_identity
import rookery_wake
import turn_block

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] (postmaster) {msg}", flush=True)


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


def _notice(conn, ident, rows, with_env=False):
    """Push text: names the sender(s) and task(s), points at `rookery inbox`, no body.
    with_env (feature 006 FR-005): the command names ROOKERY_SESSION and ROOKERY_NODE, for an
    agent whose process was started without them.
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
    cmd = "rookery inbox"
    if with_env:
        cmd = (f"ROOKERY_SESSION={_pane_field(ident['sname'])} "
               f"ROOKERY_NODE={_pane_field(ident['name'])} rookery inbox")
    return turn_block.cap_notice(head, ". Read it with: " + cmd)


BACKOFF_START, BACKOFF_MAX = 30, 600


# Mail of these block kinds waits for the recipient's next turn: it never wakes an idle agent (FR-038).
NO_PUSH_KINDS = ("status",)       # "answer" pushes only for an open ask (_needs_push)


def _herdr_state(conn, config, ident, probe_state, now_ts):
    """Feature 006 FR-001: the turn state of a hookless identity, read from herdr.
    (idle | busy | pane-gone | no-idle-state, reason or None). herdr idle/done is idle, working is busy.
    No answer for the pane while herdr lists other agents is pane-gone (FR-006).
    Off when config herdr_probe is false (FR-003)."""
    if not (config or {}).get("herdr_probe", True):
        return "no-idle-state", "herdr:off"
    row = conn.execute(
        "SELECT value FROM bindings WHERE session_id=? AND owner_type='identity' "
        "AND owner_id=? AND kind='herdr-pane' ORDER BY created_at, rowid LIMIT 1",
        (ident["session_id"], ident["agent_id"])).fetchone()
    if row is None:
        return "no-idle-state", "herdr:no-binding"
    cache = probe_state.setdefault("cache", {})
    cache["now"] = now_ts
    got = plugins.probe_status("herdr-pane", row["value"], cache)
    if got in ("idle", "done"):
        return "idle", None
    if got == "working":
        return "busy", None
    listing = cache.get("agents") or []
    if got is None and listing and not any(a.get("pane_id") == row["value"] for a in listing):
        return "pane-gone", None
    return "no-idle-state", f"herdr:{got or 'none'}"


def _kind_of(body):
    try:
        return json.loads(body).get("kind")
    except (ValueError, AttributeError, TypeError):
        return None


def _answers_open_ask(conn, row, agent_id) -> bool:
    """True when agent_id asked row's sender on row's task, and no answer from that sender on that
    task came after the ask and before this mail."""
    open_ask = False
    for r in conn.execute(
            "SELECT sender, body FROM inbox WHERE id<? AND topic=? AND status NOT IN ('quarantined','escalated') "
            "AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?)) ORDER BY id",
            (row["id"], row["topic"], agent_id, row["sender"], row["sender"], agent_id)):
        kind = _kind_of(r["body"])
        if r["sender"] == agent_id and kind == "ask":
            open_ask = True
        elif r["sender"] != agent_id and kind == "answer":
            open_ask = False
    return open_ask


def _needs_push(row, agent_id, conn=None) -> bool:
    """False for a self-send, for kind status, and for kind answer unless it answers an open ask
    of the recipient (the asker waits for it). A bounce, an ask, handoff, recruit, decline, and any
    body that is not a block with a kind (old plain mail) push."""
    if row["sender"] == agent_id:
        return False
    kind = _kind_of(row["body"])
    if kind == "answer":
        return conn is not None and row["topic"] is not None and _answers_open_ask(conn, row, agent_id)
    return kind not in NO_PUSH_KINDS


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


def type_notice(entry, value, text, run=subprocess.run):
    """Run each command of a wake entry with {value}/{text} filled. None on success, else the
    error text of the first command that failed."""
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
    return err


def _backoff_path(root, agent_id):
    if not turn_block.is_safe_name(agent_id):
        return None
    return os.path.join(root, "postmaster", "backoff", agent_id + ".json")


def _backoff_load(root, agent_id):
    """The back-off record from its file, or None (missing, unparsable or wrong shape)."""
    path = _backoff_path(root, agent_id)
    if path is None:
        return None
    try:
        with open(path) as fh:
            d = json.load(fh)
        lg = d["logged"]
        return {"until": float(d["until"]), "delay": float(d["delay"]), "newest": int(d["newest"]),
                "logged": (int(lg[0]), str(lg[1])) if lg else None}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None


def _backoff_save(root, agent_id, rec):
    path = _backoff_path(root, agent_id)
    if path is None:
        return
    try:
        out = dict(rec)
        out["logged"] = list(rec["logged"]) if rec["logged"] else None
        turn_block.write_json_atomic(path, out)
    except OSError as exc:
        return str(exc)                    # the caller logs it: without the file, the next wake types again
    return None


def _backoff_clear(root, agent_id):
    path = _backoff_path(root, agent_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def wake_one(conn, root, config, ident, run=subprocess.run, skip_logged=None, backoff=None,
             now=time.time, nopush=None, probe_state=None):
    """One identity's wake step (the body of the wake_identities loop). Returns one outcome:
    pushed, nothing (no new mail to push), busy (turn busy: quiet wait), backoff-wait (a recent
    wake failed), wake-failed, no-idle-state, no-wake-binding, unsafe-name.
    `backoff` is a dict kept by the caller; None keeps the record in
    <root>/postmaster/backoff/<agent_id>.json instead, so a new process still waits."""
    skip_logged = {} if skip_logged is None else skip_logged
    probe_state = {} if probe_state is None else probe_state   # busy streaks + herdr listing cache
    nopush = {} if nopush is None else nopush    # agent_id -> ids judged "no push"; kept in memory
    wake_cfg = (config or {}).get("wake") or {}
    aid = ident["agent_id"]
    done_ids, last = _load_pushed(root, aid)
    pending = conn.execute(
        "SELECT id, sender, topic, body FROM inbox WHERE recipient=? AND status='pending' "
        "ORDER BY id", (aid,)).fetchall()
    pending_ids = [r["id"] for r in pending]
    if done_ids is None:
        done_ids = {i for i in pending_ids if i <= last}
    skipped = nopush.setdefault(aid, set())
    skipped &= set(pending_ids)              # bounded: forget ids that left pending
    rows = []
    for r in pending:
        if r["id"] in done_ids or r["id"] in skipped:
            continue
        if _needs_push(r, aid, conn):
            rows.append(r)
        else:
            skipped.add(r["id"])
    if not rows:
        if done_ids - set(pending_ids):      # forget ids that left pending (read, requeued)
            _write_pushed(root, aid, pending_ids, [i for i in pending_ids if i in done_ids])
        return "nothing"
    newest = rows[-1]["id"]
    log_f = dict(session=ident["sname"], agent_id=aid, name=ident["name"])

    def skip(reason, why=None):
        if skip_logged.get((aid, reason), 0) < newest:
            skip_logged[(aid, reason)] = newest
            extra = {"reason": why} if why else {}
            turn_block.log_event(root, "postmaster", outcome=reason, mail_id=newest, **extra, **log_f)

    if not (turn_block.is_safe_name(ident["sname"]) and turn_block.is_safe_name(ident["name"])):
        skip("unsafe-name")
        return "unsafe-name"
    tpath = os.path.join(root, "agents", ident["sname"], ident["name"], "state", "turn.json")
    source = "turn"
    try:
        with open(tpath) as fh:
            tdoc = json.load(fh)
        state = tdoc.get("state")
    except FileNotFoundError:             # feature 006: a harness with no turn hook writes no turn.json
        state, why = _herdr_state(conn, config, ident, probe_state, now())
        if state in ("no-idle-state", "pane-gone"):
            skip(state, why)
            return state
        if state != "idle":
            return "busy"
        source, tdoc = "herdr", {}
    except (OSError, ValueError, AttributeError):
        skip("no-idle-state")
        return "no-idle-state"
    if state != "busy":
        probe_state.get("streak", {}).pop(aid, None)       # no busy turn: drop its streak
    if state != "idle":
        if state != "busy" or not _busy_is_over(conn, config, ident, tdoc, tpath, now(), probe_state):
            return "busy"                  # busy: wait quietly for the turn to end
        _log_busy_end(root, skip_logged, aid, tdoc, log_f)
    entry = None
    for b in conn.execute(
            "SELECT kind, value FROM bindings WHERE session_id=? AND owner_type='identity' "
            "AND owner_id=? ORDER BY created_at, rowid", (ident["session_id"], aid)):
        if wake_cfg.get(b["kind"]):
            entry, kind, value = wake_cfg[b["kind"]], b["kind"], b["value"]
            break
    if entry is None:
        skip("no-wake-binding")
        return "no-wake-binding"
    bo = backoff.get(aid) if backoff is not None else _backoff_load(root, aid)
    if bo and now() < bo["until"] and bo["newest"] == newest:
        return "backoff-wait"              # a recent wake failed: wait before typing again
    if (config or {}).get("draft_wait", True):
        draft = plugins.probe_input(kind, value)
        if draft:                          # feature 006: the human is typing; the next round tries again
            skip("draft-in-box", f"{len(draft)} chars")
            return "draft-in-box"
    text = _notice(conn, ident, rows, with_env=source == "herdr")
    err = type_notice(entry, value, text, run)
    if err:
        # a file record for an older mail is stale: start again at 30 s (the in-memory dict keeps its old rule)
        grow = bool(bo) and (backoff is not None or bo["newest"] == newest)
        delay = min(bo["delay"] * 2, BACKOFF_MAX) if grow else BACKOFF_START
        prev_key = bo["logged"] if bo else None
        key = (newest, err)
        rec = {"until": now() + delay, "delay": delay, "newest": newest, "logged": key}
        if backoff is not None:
            backoff[aid] = rec
        else:
            werr = _backoff_save(root, aid, rec)
            if werr:
                turn_block.log_event(root, "postmaster", outcome="backoff-write-failed", reason=werr,
                                     mail_id=newest, **log_f)
        if key != prev_key:
            turn_block.log_event(root, "postmaster", outcome="wake-failed", reason=err,
                                 kind=kind, mail_id=newest, retry_in=delay, **log_f)
        return "wake-failed"
    if backoff is not None:
        backoff.pop(aid, None)
    else:
        _backoff_clear(root, aid)
    announced = done_ids | {r["id"] for r in rows}       # status/answer ids stay un-announced
    _write_pushed(root, aid, pending_ids, [i for i in pending_ids if i in announced])
    turn_block.log_event(root, "postmaster", outcome="pushed", kind=kind, source=source,
                         mail_id=newest, count=len(rows), **log_f)
    return "pushed"


def wake_identities(conn, root, config, run=subprocess.run, skip_logged=None, backoff=None,
                    now=time.time, nopush=None, probe_state=None, session_id=None):
    """Push a notice into each idle identity (open session) with pending mail not yet announced
    in <root>/postmaster/pushed/<agent_id>.json (D10, FR-018, FR-028). The file holds the pending
    ids already announced, so mail released from held/quarantined (an older id) is pushed too.

    Never writes state/turn.json or anything under agents/. The pushed file is written only
    after EVERY command of the wake entry returned 0 within the timeout. On failure
    `wake-failed` is logged once per (agent, newest new id, error) and that agent is not retried
    for 30 s, doubling to 600 s (backoff: a dict kept by the caller, else a file per agent).
    no-idle-state / no-wake-binding are logged once per (agent, newest new mail id) via
    skip_logged. session_id (optional) limits the step to the identities of that session.
    Returns the number of identities pushed."""
    skip_logged = {} if skip_logged is None else skip_logged
    probe_state = {} if probe_state is None else probe_state   # busy streaks + herdr listing cache
    nopush = {} if nopush is None else nopush    # agent_id -> ids judged "no push"; kept in memory
    pushed = 0
    ids = conn.execute(
        "SELECT i.agent_id, i.name, i.session_id, s.name AS sname FROM identities i "
        "JOIN rookery_sessions s ON s.session_id=i.session_id WHERE s.state='open' "
        + ("" if session_id is None else "AND i.session_id=? ")
        + "ORDER BY i.created_at, i.rowid", () if session_id is None else (session_id,)).fetchall()
    for ident in ids:
        # the per-agent lock of rookery_wake.run_wake: an event wake holding it covers this mail
        lock = rookery_wake.try_lock(os.path.join(root, "postmaster", "lock", ident["agent_id"] + ".lock"),
                                     log_root=root)
        if lock is None:
            turn_block.log_event(root, "postmaster", outcome="wake-busy", agent_id=ident["agent_id"])
            continue
        try:
            if wake_one(conn, root, config, ident, run, skip_logged, backoff, now, nopush,
                        probe_state) == "pushed":
                pushed += 1
        finally:
            lock.close()
    return pushed


PROBE_MIN_AGE = 60       # seconds a turn must look busy before a probe may correct it
PROBE_GAP = 10           # idle must show on two probes at least this far apart
SKEW = 300               # a `since` further in the future than this is not trusted


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _mtime(path, now_ts):
    try:
        m = os.path.getmtime(path)
    except (OSError, TypeError, ValueError):
        return None
    return m if m <= now_ts + SKEW else None


def _busy_alive_at(tdoc, tpath, now_ts):
    """Last sign of life of a busy turn: the later of `since` and the mtime of the transcript
    the prompt hook recorded. A bad `since` (not a number, bool, NaN, > now+300 s) falls back to
    the mtime of turn.json. An unreadable transcript is ignored. None when nothing is usable."""
    since = tdoc.get("since")
    base = since if _num(since) and since <= now_ts + SKEW else _mtime(tpath, now_ts)
    tr = tdoc.get("transcript")
    tm = _mtime(tr, now_ts) if isinstance(tr, str) and tr else None
    cands = [x for x in (base, tm) if x is not None]
    return max(cands) if cands else None


def _busy_is_over(conn, config, ident, tdoc, tpath, now_ts, probe):
    """True when a `busy` turn state should count as idle for the push (FR-040). A killed turn
    never writes idle, so: (1) stale: now - last sign of life > busy_stale_s (default 1800), or
    (2) the optional status probe of the first herdr-pane binding (config herdr_probe, default
    true) says idle/done on two probes at least PROBE_GAP s apart while the turn looks busy for
    at least PROBE_MIN_AGE s. `probe` is the caller's memory (streaks, listing cache).
    Sets tdoc["_end"] ("probe"|"stale") and tdoc["_age"]."""
    cfg = config or {}
    alive = _busy_alive_at(tdoc, tpath, now_ts)
    if alive is None:
        return False
    age = now_ts - alive
    tdoc["_age"] = int(age)
    streaks = probe.setdefault("streak", {})     # agent_id -> (turn since, first idle reading)
    aid = ident["agent_id"]
    turn_key = repr(tdoc.get("since"))
    if aid in streaks and streaks[aid][0] != turn_key:
        del streaks[aid]                         # a different turn: its readings do not count
    limit = cfg.get("busy_stale_s", 1800)
    if _num(limit) and age > limit:
        streaks.pop(aid, None)
        tdoc["_end"] = "stale"
        return True
    if cfg.get("herdr_probe", True) and age >= PROBE_MIN_AGE:
        row = conn.execute(
            "SELECT value FROM bindings WHERE session_id=? AND owner_type='identity' "
            "AND owner_id=? AND kind='herdr-pane' ORDER BY created_at, rowid LIMIT 1",
            (ident["session_id"], aid)).fetchone()
        if row is None:
            streaks.pop(aid, None)
        else:
            cache = probe.setdefault("cache", {})
            cache["now"] = now_ts
            if plugins.probe_status("herdr-pane", row["value"], cache) in ("idle", "done"):
                first = streaks.setdefault(aid, (turn_key, now_ts))[1]
                if now_ts - first >= PROBE_GAP:
                    streaks.pop(aid, None)
                    tdoc["_end"] = "probe"
                    return True
            else:
                streaks.pop(aid, None)
    else:
        streaks.pop(aid, None)                   # probe off or turn too young
    return False


def _log_busy_end(root, skip_logged, aid, tdoc, log_f):
    """Log busy-stale / busy-corrected once per (agent, since)."""
    kind = tdoc["_end"]
    since = tdoc.get("since")
    if skip_logged.get((aid, kind)) == since:
        return
    skip_logged[(aid, kind)] = since
    extra = {"source": "herdr"} if kind == "probe" else {}
    turn_block.log_event(root, "postmaster",
                         outcome="busy-corrected" if kind == "probe" else "busy-stale",
                         since=since, age=tdoc["_age"], **extra, **log_f)


def _write_pushed(root, agent_id, pending_ids, announced):
    """pushed/<agent_id>.json: the announced ids still pending (bounded by the pending count);
    last_id is kept for readers of the old format."""
    turn_block.write_json_atomic(
        os.path.join(root, "postmaster", "pushed", agent_id + ".json"),
        {"last_id": max(pending_ids, default=0), "pushed_ids": sorted(announced)})


def outbox_roots(root=None):
    """Every root to scan: each ~/.rookery/projects/*/ that has agents/, ROOKERY_ROOT and the
    given root, de-duplicated by realpath."""
    cands = [root, os.environ.get("ROOKERY_ROOT")]
    base = os.path.expanduser("~/.rookery/projects")
    try:
        cands += [e.path for e in os.scandir(base)
                  if e.is_dir(follow_symlinks=False) and os.path.isdir(os.path.join(e.path, "agents"))]
    except OSError:
        pass
    seen, out = set(), []
    for c in cands:
        if not c:
            continue
        real = os.path.realpath(c)
        if real not in seen:
            seen.add(real)
            out.append(c)
    return out


def scan_outbox_roots(conn, root=None):
    """The outbox scan over every root; one root failing is logged and skipped."""
    for r in outbox_roots(root):
        try:
            turn_block.scan_outbox(conn, r)
        except Exception as exc:
            turn_block.log_event(r, "postmaster", outcome="outbox-scan-error",
                                 reason=f"{type(exc).__name__}: {exc}")


def run_identities_only(root, poll):
    """The postmaster loop (D10, FR-018): the outbox scan and the identity wake step."""
    wake_conn = turn_block.open_db()      # same db_path() as the hooks (D6)
    skip_logged, backoff, nopush, probe_state = {}, {}, {}, {}
    log("online. outbox scan + identity wake step.")
    try:
        while True:
            scan_outbox_roots(wake_conn, root)
            try:
                wake_identities(wake_conn, root, rookery_identity.load_config(persist=False),
                                skip_logged=skip_logged, backoff=backoff, nopush=nopush,
                                    probe_state=probe_state)
            except Exception as exc:     # never kill the loop; retried next tick
                turn_block.log_event(root, "postmaster", outcome="wake-step-error", reason=str(exc))
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        wake_conn.close()
        log("offline")


POLL_MIN = 300      # feature 005: a periodic check runs at most every 5 minutes


def _identity_poll(given):
    """(seconds, raised) for the identities-only mode. None gives 300; below 300 is raised to 300."""
    if given is None:
        return POLL_MIN, False
    if given < POLL_MIN:
        return POLL_MIN, True
    return given, False


def _note_poll_raised(root, was, floor=POLL_MIN, what="--poll"):
    why = ("feature 006: the session watcher runs at most once a minute" if what == "sweep"
           else "feature 005: a periodic check runs at most every 5 minutes")
    sys.stderr.write(f"NOTE: {what} raised to {floor} s ({why})\n")
    try:
        turn_block.log_event(root, "postmaster", outcome="poll-raised", reason=f"{what} {was} raised to {floor}")
    except Exception:
        pass


def _sweep_screen(conn, root, cfg, sid):
    """One screening pass for the session when screening is on (feature 005, FR-022). Imports are
    local: screener is slow to import and must not load when screening is off. Shares the lock of an
    event-started `rookery screen run --session` (screen-SID.lock): held means skip this round.
    The pass runs inline, so a round that screens mail takes as long as its model calls (a few seconds,
    bounded by screener.run: 5 mails a pass, 3 tries a mail). The wake round and the outbox scan of this
    session wait for it, once every 300 seconds at most."""
    lock = None
    try:
        import screener_config
        held = conn.execute("SELECT 1 FROM inbox WHERE session_id=? AND status='held' AND reason='screening' "
                            "LIMIT 1", (sid,)).fetchone()   # held for screening: t1 is live, so it must be screened
        if held is None and not screener_config.screening_on(conn, cfg, sid):
            return
        lock = rookery_wake.try_lock(os.path.join(root, "postmaster", "lock", f"screen-{sid}.lock"),
                                     log_root=root)
        if lock is None:
            turn_block.log_event(root, "postmaster", outcome="screen-busy", session_id=sid)
            return
        import screener
        screener.run(sessions=[sid], once=True)
    except Exception as exc:               # never kill the loop; retried next round
        turn_block.log_event(root, "postmaster", outcome="screen-step-error", reason=str(exc))
    finally:
        if lock is not None:
            lock.close()


SESSION_CHECK_S = 5      # feature 006 FR-008: the watcher reads its session state this often


def _session_sweep_s(conn, sid):
    """The session's sweep-s binding (set by `rookery up --sweep` or team sweep_s) as a number, else None."""
    row = conn.execute("SELECT value FROM bindings WHERE session_id=? AND owner_type='session' "
                       "AND kind='sweep-s' ORDER BY created_at DESC, rowid DESC LIMIT 1", (sid,)).fetchone()
    try:
        return float(row[0]) if row is not None else None
    except (TypeError, ValueError):
        return None


def _sweep_should_end(conn, root, sess, db_id):
    """True (after logging) when the sweep must stop: the project folder is gone (no log), the DB file
    was deleted or replaced (a rollback: this connection is stale), or the session is not open."""
    if not os.path.isdir(root):           # the project folder is gone: recreate nothing, log nothing
        return True
    try:
        st = os.stat(turn_block.db_path())
        now_id = (st.st_ino, st.st_dev)
    except OSError:
        now_id = None
    if now_id != db_id:
        turn_block.log_event(root, "postmaster", outcome="sweep-end", session=sess["name"],
                             reason="db-replaced")
        return True
    row = conn.execute("SELECT state FROM rookery_sessions WHERE session_id=?",
                       (sess["session_id"],)).fetchone()
    if row is None or row[0] != "open":
        turn_block.log_event(root, "postmaster", outcome="sweep-end", session=sess["name"])
        return True
    return False


def run_session_sweep(root, config, session, sleep=time.sleep, now=time.time, rounds=None):
    """Periodic safety net for ONE session (feature 005, R7): wake the identities of that session
    that have mail the event wake missed, then sleep sweep_interval. Returns 0, or 3 when the
    session does not exist. One sweep per session (a lock file); a second one logs sweep-busy."""
    conn = turn_block.open_db()
    lock = None
    try:
        sess = rookery_identity.get_session(conn, session)
        if sess is None:
            sys.stderr.write(f"rookery: no session {session!r}\n")
            return 3
        sid = sess["session_id"]
        lock = rookery_wake.try_lock(os.path.join(root, "postmaster", "lock", f"sweep-{sid}.lock"),
                                     log_root=root)
        if lock is None:
            turn_block.log_event(root, "postmaster", outcome="sweep-busy", session=sess["name"])
            return 0
        if rounds is None and os.environ.get("ROOKERY_SWEEP_ROUNDS"):
            try:
                rounds = int(os.environ["ROOKERY_SWEEP_ROUNDS"])
            except ValueError:
                rounds = None
        skip_logged, nopush, probe_state = {}, {}, {}
        done, noted = 0, False
        try:
            st = os.stat(turn_block.db_path())
            db_id = (st.st_ino, st.st_dev)
        except OSError:
            db_id = None
        while True:
            if _sweep_should_end(conn, root, sess, db_id):
                return 0
            cfg = config
            try:
                cfg = rookery_identity.load_config(persist=False)
            except Exception:
                pass                      # keep the last good config
            override = _session_sweep_s(conn, sid)
            interval, raised = rookery_wake.sweep_interval(cfg, override)
            if raised and not noted:
                noted = True
                was = override if override is not None else (cfg.get("sweep_s") if isinstance(cfg, dict) else None)
                _note_poll_raised(root, was, rookery_wake.MIN_SWEEP_S, "sweep")
            try:                          # the outbox drop-box rides the sweep (a root-wide scan lock)
                turn_block.scan_outbox(conn, root)
            except Exception as exc:
                turn_block.log_event(root, "postmaster", outcome="outbox-error", reason=str(exc))
            try:
                wake_identities(conn, root, cfg, skip_logged=skip_logged, backoff=None,
                                nopush=nopush, probe_state=probe_state, session_id=sid)
            except Exception as exc:      # never kill the loop; retried next round
                turn_block.log_event(root, "postmaster", outcome="wake-step-error", reason=str(exc))
            _sweep_screen(conn, root, cfg, sid)
            done += 1
            if rounds is not None and done >= rounds:
                return 0
            waited = 0.0
            while waited < interval:      # 006 FR-008: notice `session end` within SESSION_CHECK_S, not one round
                step = min(SESSION_CHECK_S, interval - waited)
                sleep(step)
                waited += step
                if _sweep_should_end(conn, root, sess, db_id):
                    return 0
    except KeyboardInterrupt:              # SIGTERM in main() raises this: a clean stop, no traceback
        return 0
    finally:
        if lock is not None:
            lock.close()
        conn.close()


def cmd_sweep(args):
    conn = turn_block.open_db()
    try:
        sess = rookery_identity.get_session(conn, args.session)
        root = args.root or (rookery_wake.resolve_root(conn, sess["session_id"]) if sess else None)
    finally:
        conn.close()
    if sess is None:
        sys.stderr.write(f"rookery: no session {args.session!r}\n")
        return 3
    if not root:
        sys.stderr.write("rookery: no project root (pass --root or set ROOKERY_ROOT)\n")
        return 2
    return run_session_sweep(os.path.abspath(root), rookery_identity.load_config(persist=False),
                             args.session)


def add_sweep_parser(subparsers):
    p = subparsers.add_parser("sweep", help="Periodic safety-net wake for one session.")
    p.add_argument("--session", required=True, help="Session name or ID.")
    p.add_argument("--root", default=None, help="Project root (default: the session's rookery-root).")
    p.set_defaults(func=lambda a: sys.exit(cmd_sweep(a)))


def _on_sigterm(_signum, _frame):
    raise KeyboardInterrupt        # run the same clean-exit path as Ctrl-C (finally: close, offline)


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)
    ap = argparse.ArgumentParser(description="Run the Rookery postmaster wake step.")
    ap.add_argument("--root", default=None,
                    help="project root for the identity wake step (default: ROOKERY_ROOT, else "
                         "--project, else the current folder name)")
    ap.add_argument("--project", default=None, help="project name (root = ~/.rookery/projects/NAME)")
    ap.add_argument("--poll", type=float, default=None,
                    help="seconds between loops (default 300, never below 300)")
    ap.add_argument("--session", default=None,
                    help="run the periodic sweep for this one session (name or ID)")
    a = ap.parse_args()
    if a.session:
        conn = turn_block.open_db()
        try:
            sess = rookery_identity.get_session(conn, a.session)
            root = a.root or (rookery_wake.resolve_root(conn, sess["session_id"]) if sess else None)
        finally:
            conn.close()
        if sess is None:
            sys.stderr.write(f"postmaster: no session {a.session!r}\n")
            sys.exit(3)
        if not root:
            try:
                root = turn_block.project_root(a.project or os.path.basename(os.getcwd()))
            except ValueError:
                sys.stderr.write("postmaster: no resolvable project root; pass --root\n")
                sys.exit(2)
        sys.exit(run_session_sweep(os.path.abspath(root),
                                   rookery_identity.load_config(persist=False), a.session))
    try:
        root = os.path.abspath(a.root or turn_block.project_root(
            a.project or os.path.basename(os.getcwd())))
    except ValueError:
        sys.stderr.write("postmaster: no resolvable project root (set ROOKERY_ROOT, "
                         "or pass --root/--project); nothing to do, exiting.\n")
        sys.exit(2)
    poll, raised = _identity_poll(a.poll)
    if raised:
        _note_poll_raised(root, a.poll)
    run_identities_only(root, poll)


if __name__ == "__main__":
    main()
