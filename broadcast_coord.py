#!/usr/bin/env python3
"""Broadcast Coordination Backend (TWICE-57160).

A stdlib-only Python sidecar that consumes the WUPHF broker's existing HTTP
API (/tasks, /messages, /channels, /events SSE) and exposes the locked
6-endpoint broadcast-coordination contract consumed by step 5
(TWICE-57164, @coder-qwen) and downstream apps.

Contract (locked with @coder-qwen on #task-twice-57160):
  1. GET  /broadcasts?gateId=&channel=&limit=  list broadcasts (messages)
  2. POST /links                                link a broadcast -> tasks
  3. POST /block                                block tasks tied to a gate/broadcast
  4. POST /release                              release blocked tasks
  5. GET  /links/{broadcastId}                  get task links for a broadcast
  6. GET  /status?gateId=                       gate-scoped cluster status
       -> { taskIds, status, ownerSlugs, rulingState }

Endpoint #6 and #1 accept a `gateId` filter so a downstream app can swap
client-side getTasks filtering for a single gate-scoped read with zero UI
rewrite (drop-in data-source swap).

No external deps. Runs on Python 3.12 stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_BROKER = os.environ.get("WUPHF_BROKER_URL", "http://127.0.0.1:8080")
DEFAULT_PORT = int(os.environ.get("BROADCAST_COORD_PORT", "8090"))
STATE_TTL = int(os.environ.get("BROADCAST_COORD_STATE_TTL", "86400"))


def _broker_get(broker_url: str, path: str, timeout: float = 5.0) -> Any:
    """GET a JSON resource from the broker. Returns None on any failure."""
    url = broker_url.rstrip("/") + path
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            if not data:
                return None
            return json.loads(data)
    except Exception:
        return None


def _is_broadcast(msg: dict) -> bool:
    """A broadcast is a channel message authored by an agent/human (not a
    system trace). Heuristic: has 'id' + 'content' + a non-system sender."""
    if not isinstance(msg, dict) or "id" not in msg:
        return False
    sender = str(msg.get("sender") or msg.get("author") or msg.get("slug") or "")
    if sender.startswith("system") or sender == "@system":
        return False
    return bool(msg.get("content") or msg.get("text"))


def _gate_of(msg: dict) -> str | None:
    """Extract a gateId from a broadcast. A gate is the coordination scope the
    broadcast belongs to. Heuristic: explicit `gateId`/`gate` field, else a
    `#task-<id>` channel tag in content, else the channel slug."""
    for key in ("gateId", "gate", "gate_id"):
        g = msg.get(key)
        if g:
            return str(g)
    content = str(msg.get("content") or msg.get("text") or "")
    for tok in content.split():
        if tok.startswith("#task-"):
            return tok[1:]
    ch = msg.get("channel") or msg.get("channelSlug")
    return str(ch) if ch else None


class CoordState:
    """In-memory coordination state: broadcast->task links and gate blocks.

    A sidecar holds ephemeral state; persistence is the broker's job. This
    state is rebuilt on startup from broker reads and refreshed via the SSE
    /events stream (when connected). Thread-safe.

    rulingState values:
      - "open"     : broadcast has active links, no block ruling
      - "blocked"  : a block ruling is active on the gate
      - "released" : a prior block was released
      - "resolved" : all linked tasks are done/approved/cancelled
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # broadcastId -> {taskIds: set, gateId: str, createdAt: float}
        self.links: dict[str, dict] = {}
        # gateId -> {blocked: bool, taskIds: set, since: float, reason: str}
        self.gates: dict[str, dict] = {}
        # taskId -> blocked bool (mirror of gate block for fast status joins)
        self.task_block: dict[str, bool] = {}

    # ---- links ----------------------------------------------------------
    def add_link(self, broadcast_id: str, task_ids: list[str], gate_id: str | None) -> dict:
        with self._lock:
            rec = self.links.setdefault(
                broadcast_id,
                {"taskIds": set(), "gateId": gate_id, "createdAt": time.time()},
            )
            if gate_id and not rec.get("gateId"):
                rec["gateId"] = gate_id
            for tid in task_ids:
                rec["taskIds"].add(tid)
            return self._link_view(broadcast_id, rec)

    def get_links(self, broadcast_id: str) -> dict | None:
        with self._lock:
            rec = self.links.get(broadcast_id)
            return self._link_view(broadcast_id, rec) if rec else None

    def _link_view(self, broadcast_id: str, rec: dict) -> dict:
        return {
            "broadcastId": broadcast_id,
            "gateId": rec.get("gateId"),
            "taskIds": sorted(rec.get("taskIds", set())),
            "createdAt": rec.get("createdAt"),
        }

    # ---- block / release ------------------------------------------------
    def _gate_view(self, gate_id: str, g: dict) -> dict:
        return {
            "gateId": gate_id,
            "blocked": bool(g.get("blocked")),
            "taskIds": sorted(g.get("taskIds", set())),
            "since": g.get("since"),
            "reason": g.get("reason", ""),
            "rulingState": "blocked" if g.get("blocked") else "open",
        }

    def block(self, gate_id: str, task_ids: list[str], reason: str = "") -> dict:
        with self._lock:
            g = self.gates.setdefault(
                gate_id, {"blocked": False, "taskIds": set(), "since": 0.0, "reason": ""}
            )
            g["blocked"] = True
            g["since"] = time.time()
            g["reason"] = reason
            for tid in task_ids:
                g["taskIds"].add(tid)
                self.task_block[tid] = True
            return self._gate_view(gate_id, g)

    def release(self, gate_id: str, task_ids: list[str] | None = None) -> dict:
        with self._lock:
            g = self.gates.get(gate_id)
            if not g:
                return {"gateId": gate_id, "blocked": False, "released": [], "rulingState": "released"}
            if task_ids:
                for tid in task_ids:
                    g["taskIds"].discard(tid)
                    self.task_block.pop(tid, None)
            else:
                for tid in list(g["taskIds"]):
                    self.task_block.pop(tid, None)
                g["taskIds"].clear()
            if not g["taskIds"]:
                g["blocked"] = False
            return {
                "gateId": gate_id,
                "blocked": g["blocked"],
                "released": sorted(task_ids) if task_ids else "all",
                "rulingState": "released",
            }

    # ---- status ---------------------------------------------------------
    def status(self, gate_id: str, tasks: list[dict] | None) -> dict:
        """Compute the gate-scoped cluster status. `tasks` is the broker's
        /tasks list (already fetched) used to resolve owner + lifecycle state.
        """
        with self._lock:
            g = self.gates.get(gate_id)
            task_ids = sorted(g["taskIds"]) if g else []
            blocked = bool(g and g["blocked"]) if g else False
            owner_slugs: list[str] = []
            lifecycle_states: list[str] = []
            if tasks:
                by_id = {str(t.get("id")): t for t in tasks if isinstance(t, dict) and t.get("id")}
                for tid in task_ids:
                    t = by_id.get(tid)
                    if not t:
                        continue
                    owner = t.get("owner") or t.get("ownerSlug")
                    if owner and owner not in owner_slugs:
                        owner_slugs.append(str(owner))
                    ls = t.get("lifecycleState") or t.get("status") or t.get("state")
                    if ls:
                        lifecycle_states.append(str(ls))
            # ruling state
            if blocked:
                ruling = "blocked"
            elif task_ids and lifecycle_states and all(
                s in ("done", "approved", "cancelled", "archived", "completed") for s in lifecycle_states
            ):
                ruling = "resolved"
            elif task_ids:
                ruling = "open"
            else:
                ruling = "open"
            return {
                "gateId": gate_id,
                "taskIds": task_ids,
                "status": "blocked" if blocked else "active",
                "ownerSlugs": owner_slugs,
                "rulingState": ruling,
            }

    def is_task_blocked(self, task_id: str) -> bool:
        with self._lock:
            return self.task_block.get(task_id, False)

    def apply_event(self, event: dict) -> None:
        """Apply a broker /events SSE payload to refresh state. Best-effort."""
        with self._lock:
            etype = event.get("type") or event.get("event")
            if etype in ("task_updated", "task.completed", "task.approved"):
                tid = str(event.get("taskId") or event.get("id") or "")
                if tid and event.get("state") in ("done", "approved", "cancelled", "archived"):
                    self.task_block.pop(tid, None)
                    for g in self.gates.values():
                        g["taskIds"].discard(tid)


class CoordServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: CoordState, broker_url: str, port: int):
        super().__init__("0.0.0.0", port) if False else super().__init__(("", port), _make_handler(state, broker_url))
        self.state = state
        self.broker_url = broker_url


def _make_handler(state: CoordState, broker_url: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence default stderr noise
            pass

        def _send(self, code: int, obj: Any) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw) if raw else {}
            except Exception:
                return {}

        def _qs(self) -> dict:
            parsed = urllib.parse.urlparse(self.path)
            return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        def _path_parts(self) -> tuple[str, list[str]]:
            parsed = urllib.parse.urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            return parsed.path, parts

        # ---- GET --------------------------------------------------------
        def do_GET(self):
            _, parts = self._path_parts()
            qs = self._qs()
            if not parts:
                return self._send(200, {"service": "broadcast-coord", "ok": True})
            if parts[0] == "broadcasts":
                return self._handle_broadcasts(qs)
            if parts[0] == "status":
                return self._handle_status(qs)
            if parts[0] == "links" and len(parts) >= 2:
                rec = state.get_links(parts[1])
                return self._send(200 if rec else 404, rec or {"error": "no links for broadcast"})
            if parts[0] == "health":
                return self._send(200, {"ok": True, "broker": broker_url})
            return self._send(404, {"error": "not found"})

        # ---- POST -------------------------------------------------------
        def do_POST(self):
            _, parts = self._path_parts()
            body = self._body_json()
            if not parts:
                return self._send(404, {"error": "not found"})
            if parts[0] == "links":
                bid = str(body.get("broadcastId") or "")
                tids = [str(t) for t in (body.get("taskIds") or []) if t]
                gate = body.get("gateId") or (parts[1] if len(parts) >= 2 else None)
                if not bid or not tids:
                    return self._send(400, {"error": "broadcastId and taskIds required"})
                rec = state.add_link(bid, tids, str(gate) if gate else None)
                return self._send(201, rec)
            if parts[0] == "block":
                gate = str(body.get("gateId") or "")
                tids = [str(t) for t in (body.get("taskIds") or []) if t]
                reason = str(body.get("reason") or "")
                if not gate:
                    return self._send(400, {"error": "gateId required"})
                rec = state.block(gate, tids, reason)
                return self._send(200, rec)
            if parts[0] == "release":
                gate = str(body.get("gateId") or (parts[1] if len(parts) >= 2 else ""))
                tids = [str(t) for t in (body.get("taskIds") or []) if t]
                if not gate:
                    return self._send(400, {"error": "gateId required"})
                rec = state.release(gate, tids or None)
                return self._send(200, rec)
            return self._send(404, {"error": "not found"})

        # ---- endpoint impls --------------------------------------------
        def _handle_broadcasts(self, qs: dict):
            gate = qs.get("gateId")
            channel = qs.get("channel")
            limit = int(qs.get("limit", "100") or "100")
            msgs = _broker_get(broker_url, "/messages") or []
            if isinstance(msgs, dict):
                msgs = msgs.get("messages") or msgs.get("items") or []
            out = []
            for m in msgs:
                if not _is_broadcast(m):
                    continue
                g = _gate_of(m)
                if gate and g != gate:
                    continue
                if channel and str(m.get("channel") or m.get("channelSlug") or "") != channel:
                    continue
                out.append(
                    {
                        "id": m.get("id"),
                        "gateId": g,
                        "channel": m.get("channel") or m.get("channelSlug"),
                        "sender": m.get("sender") or m.get("author") or m.get("slug"),
                        "content": m.get("content") or m.get("text"),
                        "createdAt": m.get("createdAt") or m.get("ts"),
                    }
                )
                if len(out) >= limit:
                    break
            self._send(200, {"broadcasts": out, "count": len(out), "gateId": gate, "channel": channel})

        def _handle_status(self, qs: dict):
            gate = qs.get("gateId")
            if not gate:
                return self._send(400, {"error": "gateId required"})
            tasks = _broker_get(broker_url, "/tasks")
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks") or tasks.get("items") or []
            rec = state.status(gate, tasks)
            self._send(200, rec)

    return Handler


def serve(port: int = DEFAULT_PORT, broker_url: str = DEFAULT_BROKER) -> None:
    state = CoordState()
    server = CoordServer(state, broker_url, port)
    print(f"broadcast-coord listening on :{port} (broker={broker_url})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    serve()