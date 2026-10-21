#!/usr/bin/env python3
"""In-process test for broadcast_coord.py (TWICE-57160).

Spins a mock broker (live HTTP on a free port) and the broadcast-coord
sidecar in the same process, then asserts the full 6-endpoint contract
round-trip. No bash; pure stdlib; runs on Python 3.12.

Run:  python3 test_broadcast_coord.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import broadcast_coord as bc  # noqa: E402

MOCK_MESSAGES = [
    {"id": "msg-1", "channel": "task-twice-55639", "sender": "@ceo",
     "content": "#task-twice-55639 step 4 backend kick-off", "createdAt": 1725100000},
    {"id": "msg-2", "channel": "task-twice-55639", "sender": "@senior-software-engineer",
     "content": "contract locked", "createdAt": 1725100010},
    {"id": "msg-3", "channel": "general", "sender": "@system",
     "content": "trace noise", "createdAt": 1725100020},
    {"id": "msg-4", "channel": "task-twice-56059", "sender": "@coder-qwen",
     "content": "#task-twice-55639 gate ack from app side", "createdAt": 1725100030},
]
MOCK_TASKS = [
    {"id": "TWICE-57160", "owner": "senior-software-engineer", "status": "in_progress"},
    {"id": "TWICE-57164", "owner": "coder-qwen", "status": "ready"},
    {"id": "TWICE-57168", "owner": "sa", "status": "ready"},
]

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ok: {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL: {msg}")


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_mock_broker(port: int) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            payload = None
            if self.path.startswith("/messages"):
                payload = MOCK_MESSAGES
            elif self.path.startswith("/tasks"):
                payload = MOCK_TASKS
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_sidecar(broker_url: str, port: int) -> ThreadingHTTPServer:
    state = bc.CoordState()
    srv = bc.CoordServer(state, broker_url, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def wait_up(url: str, tries: int = 50) -> None:
    for _ in range(tries):
        try:
            _get(url)
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"never came up: {url}")


def main() -> int:
    broker_port = _free_port()
    coord_port = _free_port()
    broker = start_mock_broker(broker_port)
    broker_url = f"http://127.0.0.1:{broker_port}"
    sidecar = start_sidecar(broker_url, coord_port)
    base = f"http://127.0.0.1:{coord_port}"
    wait_up(base + "/health")

    print("== 1. GET /broadcasts (gate filter) ==")
    out = _get(base + "/broadcasts?gateId=task-twice-55639")
    if out["count"] == 3:
        ok("gate filter returns 3 broadcasts (msg-1,msg-2,msg-4)")
    else:
        bad(f"expected 3 got {out['count']}")

    print("== 2. POST /links ==")
    out = _post(base + "/links", {
        "broadcastId": "msg-1", "gateId": "task-twice-55639",
        "taskIds": ["TWICE-57160", "TWICE-57164"],
    })
    if sorted(out["taskIds"]) == ["TWICE-57160", "TWICE-57164"]:
        ok("link recorded")
    else:
        bad("link shape")

    print("== 3. GET /links/{broadcastId} ==")
    out = _get(base + "/links/msg-1")
    if out["broadcastId"] == "msg-1":
        ok("links readback")
    else:
        bad("links readback")

    print("== 4. POST /block ==")
    out = _post(base + "/block", {
        "gateId": "task-twice-55639", "taskIds": ["TWICE-57160"],
        "reason": "awaiting contract sign",
    })
    if out["blocked"] is True:
        ok("block ruling active")
    else:
        bad("block")

    print("== 5. GET /status?gateId= (drop-in cluster shape) ==")
    out = _get(base + "/status?gateId=task-twice-55639")
    expected_keys = {"gateId", "taskIds", "status", "ownerSlugs", "rulingState"}
    if (set(out) == expected_keys and out["rulingState"] == "blocked"
            and out["status"] == "blocked"
            and "senior-software-engineer" in out["ownerSlugs"]):
        ok("status cluster shape matches contract")
    else:
        bad(f"status shape: {out}")

    print("== 6. POST /release ==")
    out = _post(base + "/release", {
        "gateId": "task-twice-55639", "taskIds": ["TWICE-57160"],
    })
    if out["rulingState"] == "released":
        ok("release ruling recorded")
    else:
        bad("release")

    print("== 7. GET /health ==")
    out = _get(base + "/health")
    if out["ok"] is True:
        ok("health endpoint")
    else:
        bad("health")

    print("== 8. GET /broadcasts unfiltered (system trace excluded) ==")
    out = _get(base + "/broadcasts")
    if out["count"] == 3:
        ok("system trace excluded, 3 broadcasts total")
    else:
        bad(f"unfiltered count {out['count']}")

    print("== 9. apply_event clears block on task completion ==")
    sidecar.state.apply_event({"type": "task_updated", "taskId": "TWICE-57164",
                               "state": "approved"})
    if not sidecar.state.is_task_blocked("TWICE-57164"):
        ok("event-driven unblock on completion")
    else:
        bad("event unblock")

    print("== 10. POST /links missing fields -> 400 ==")
    try:
        _post(base + "/links", {"broadcastId": "msg-2"})
        bad("expected 400 on missing taskIds")
    except urllib.error.HTTPError as e:
        ok(f"400 as expected (status={e.code})") if e.code == 400 else bad(f"wrong status {e.code}")

    broker.shutdown()
    sidecar.shutdown()
    print(f"\nRESULT: PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())