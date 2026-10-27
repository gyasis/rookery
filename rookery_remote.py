"""rookery_remote -- the remote check: an agent on another machine checks the mailroom for its own mail
(research R8 of spec 005). `check_once` does one GET /inbox, applies the push rules, types one notice into the
agent's own pane, and remembers the announced ids in ~/.rookery/remote/NODE.json."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import turn_block

CHECK_TIMEOUT = 10          # seconds per socket operation
CHECK_DEADLINE = 20         # seconds for the whole reply: a server that drips bytes cannot hold a hook
MAX_REPLY = 1_000_000       # bytes: a larger reply is refused, not read into memory
BACKOFF_START, BACKOFF_MAX = 30, 600   # a pane command that fails is not retyped on every check


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would carry the Authorization header to the new host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused ({code})", headers, fp)


def _state_path(node):
    return os.path.join(os.path.expanduser("~"), ".rookery", "remote", node + ".json")


def _load_state(node):
    """{"announced": set of ids, "retry_after": epoch, "delay": seconds}; a bad file counts as empty."""
    try:
        with open(_state_path(node)) as fh:
            d = json.load(fh)
        return {"announced": {int(x) for x in d.get("announced", [])},
                "retry_after": float(d.get("retry_after") or 0), "delay": float(d.get("delay") or 0)}
    except (OSError, ValueError, AttributeError, TypeError):
        return {"announced": set(), "retry_after": 0.0, "delay": 0.0}


def _load_announced(node):
    return _load_state(node)["announced"]


def _save_announced(node, ids, retry_after=0.0, delay=0.0):
    path = _state_path(node)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"announced": sorted(ids), "retry_after": retry_after, "delay": delay}, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _fetch(url, token, node, opener):
    """The unread rows of `node` from the mailroom. Raises on any network or HTTP error."""
    import mailctl                      # the same TLS rule as every remote call (ROOKERY_INSECURE_TLS)
    full = url.rstrip("/") + "/inbox?node=" + urllib.parse.quote(node)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(full, method="GET", headers=headers)
    if opener is None:
        handlers = [_NoRedirect()]
        ctx = mailctl._ctx(url)
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        resp = urllib.request.build_opener(*handlers).open(req, timeout=CHECK_TIMEOUT)
    else:
        resp = opener(req, timeout=CHECK_TIMEOUT)
    with resp:
        return json.loads(_read_capped(resp) or b"{}")["messages"]


def _read_capped(resp):
    """The whole reply, at most MAX_REPLY bytes and CHECK_DEADLINE seconds. Raises when over either."""
    end = time.monotonic() + CHECK_DEADLINE
    chunks, size = [], 0
    read = getattr(resp, "read1", None) or resp.read
    while True:
        if time.monotonic() > end:
            raise TimeoutError(f"reply took more than {CHECK_DEADLINE} s")
        chunk = read(65536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_REPLY:
            raise ValueError(f"reply larger than {MAX_REPLY} bytes")
        chunks.append(chunk)


def _pushes(row, node):
    """A self-send and kind `status` do not push. An `answer` does: this side cannot see open asks."""
    import postmaster
    if row.get("sender") == node or row.get("sender_name") == node:
        return False
    return postmaster._kind_of(row.get("body")) not in postmaster.NO_PUSH_KINDS


def _notice(node, rows):
    import postmaster
    f = postmaster._pane_field
    senders, tasks = [], []
    for r in rows:
        s = f(r.get("sender_name") or r.get("sender") or "?")
        if s not in senders:
            senders.append(s)
        t = f(r["topic"]) if r.get("topic") else ""
        if t and t not in tasks:
            tasks.append(t)
    head = (f"[rookery] new mail for {f(node)} from {turn_block.notice_list(senders)} "
            f"about {turn_block.notice_list(tasks) or 'no task'}")
    return turn_block.cap_notice(head, ". Read it with: rookery inbox")


def check_once(url, token, node, config, run=subprocess.run, opener=None, env=None, now=time.time) -> dict:
    """One remote check. Never raises. Returns {"node", "outcome", "count", "reason"?}."""
    out = {"node": node, "outcome": "nothing", "count": 0}
    try:
        if not turn_block.is_safe_name(node):
            out["outcome"] = "bad-node"
            return out
        try:
            rows = _fetch(url, token, node, opener)
        except Exception as exc:
            out.update(outcome="check-failed", reason=str(exc))
            return out
        unread = {int(r["id"]) for r in rows}
        state = _load_state(node)
        announced = state["announced"] & unread
        new = [r for r in rows if int(r["id"]) not in announced and _pushes(r, node)]
        if not new:
            _save_announced(node, announced)
            return out
        if now() < state["retry_after"]:   # the pane command failed recently: wait, do not retype
            out["outcome"] = "backoff-wait"
            return out
        env = os.environ if env is None else env
        import rookery_identity
        entry = value = None
        wake_cfg = (config or {}).get("wake") or {}
        for kind, val in rookery_identity.bindings_from(env, None, config or {}):
            if wake_cfg.get(kind):
                entry, value = wake_cfg[kind], val
                break
        if entry is None:
            _save_announced(node, announced)
            out["outcome"] = "no-wake-binding"
            return out
        import postmaster
        err = postmaster.type_notice(entry, value, _notice(node, new), run)
        if err:
            delay = min(state["delay"] * 2, BACKOFF_MAX) if state["delay"] else BACKOFF_START
            _save_announced(node, announced, retry_after=now() + delay, delay=delay)
            out.update(outcome="wake-failed", reason=err, retry_in=delay)
            return out
        _save_announced(node, announced | {int(r["id"]) for r in new})
        out.update(outcome="pushed", count=len(new))
        return out
    except Exception as exc:
        out.update(outcome="check-failed", reason=f"{type(exc).__name__}: {exc}")
        return out


def _remote_path(node, ext):
    return os.path.join(os.path.expanduser("~"), ".rookery", "remote", node + ext)


def _write_follow(node, start):
    path = _remote_path(node, ".follow")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"start": start}, fh)
    os.replace(tmp, path)


def _read_follow(node):
    """The start of the newest trigger, or None when the file is missing or bad."""
    try:
        with open(_remote_path(node, ".follow")) as fh:
            return float(json.load(fh)["start"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def start_follow(node, now=time.time) -> str:
    """Record a new trigger and start the follower unless one holds NODE.lock. Never raises."""
    import rookery_wake
    try:
        _write_follow(node, now())
        probe = rookery_wake.try_lock(_remote_path(node, ".lock"))
        if probe is None:
            return "follower-running"
        probe.close()
        return "follower-started" if rookery_wake.spawn(["check", "--follower"]) else "follower-failed"
    except Exception:
        return "follower-failed"


def run_follower(url, token, node, config, sleep=time.sleep, now=time.time, run=subprocess.run,
                 opener=None, env=None, interval=300, max_checks=5) -> dict:
    """Check at once, then every `interval` s, at most `max_checks` per trigger (a newer NODE.follow start
    restarts the count). Stops after the first notice. One follower per node (NODE.lock). After it releases
    the lock it reads NODE.follow once more: a trigger written while it was finishing (and that saw the lock
    held) starts a new round instead of being lost."""
    import rookery_wake
    out = {"node": node, "outcome": "follower-running", "checks": 0}
    seen = None
    while True:
        lock = rookery_wake.try_lock(_remote_path(node, ".lock"))
        if lock is None:
            return out                   # another follower runs; it reads the newest trigger
        try:
            count = 0
            while True:
                start = _read_follow(node)
                if start is None:       # no readable trigger: keep the last one, else this run's own start
                    start = seen if seen is not None else now()
                if start != seen:
                    seen, count = start, 0
                res = check_once(url, token, node, config, run=run, opener=opener, env=env, now=now)
                out["outcome"] = res["outcome"]
                out["checks"] += 1
                count += 1
                if res["outcome"] == "pushed" or count >= max_checks:
                    break
                sleep(interval)
        except KeyboardInterrupt:
            return out
        finally:
            lock.close()
        latest = _read_follow(node)
        if latest is None or latest == seen:
            return out                   # no trigger came in while this round finished


def cmd_check(args):
    url, node = os.environ.get("ROOKERY_URL"), os.environ.get("ROOKERY_NODE")
    if not url or not node:
        print("rookery check: ROOKERY_URL and ROOKERY_NODE must be set", file=sys.stderr)
        raise SystemExit(2)
    if getattr(args, "follow", False):
        res = {"node": node, "outcome": start_follow(node)}
        print(json.dumps(res) if args.json else res["outcome"])
        return
    try:
        import rookery_identity
        config = rookery_identity.load_config(persist=False)
    except Exception:
        config = {}
    if getattr(args, "follower", False):
        try:
            run_follower(url, os.environ.get("ROOKERY_TOKEN"), node, config)
        except Exception:
            pass
        return
    res = check_once(url, os.environ.get("ROOKERY_TOKEN"), node, config)
    print(json.dumps(res) if args.json else f"check {node}: {res['outcome']}")


def add_parser(subparsers):
    p = subparsers.add_parser("check", help="Check the remote mailroom for mail and type one notice into this pane.")
    p.add_argument("--json", action="store_true", help="Print the result as JSON.")
    p.add_argument("--follow", action="store_true",
                   help="Check now and every 300 s, at most 5 checks per trigger, in a detached follower.")
    p.add_argument("--follower", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_check)
