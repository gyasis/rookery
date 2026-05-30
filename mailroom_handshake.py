"""Mailroom handshake HTTP route handlers.

Extracted from mailroom_server.py. These four functions handle the join/approve
flow; they are called from do_GET / do_POST in mailroom_server.py after auth has
already been enforced by the caller.

All handlers follow the signature:
    handle_*(handler, conn, principal, d)
        handler   – the BaseHTTPRequestHandler instance (for _reply / headers)
        conn      – rookery DB connection (unused by most handshake routes, kept
                    for API uniformity)
        principal – authenticated principal string, or None for public endpoints
        d         – parsed JSON body (POST), or None (GET)
"""

import time
from urllib.parse import urlparse, parse_qs

import handshake


def handle_join(handler, conn, principal, d):
    """POST /join — public; peer posts node_id + pubkey_b64 + slug."""
    try:
        node_id = d.get("node_id")
        pubkey_b64 = d.get("pubkey_b64")
        slug = d.get("slug")
        if not (node_id and pubkey_b64 and slug):
            return handler._reply({"error": "node_id, pubkey_b64, slug required"}, 400)
        entry = handshake.new_request(node_id, pubkey_b64, slug)
        # Out-of-band push notification so the admin can approve from anywhere.
        try:
            import notifier
            from mailroom_server import PUBLIC_URL

            approve_url = (
                PUBLIC_URL.rstrip("/") + "/approve?request_id=" + entry["request_id"]
            )
            notifier.notify_pending_join(
                entry["request_id"],
                entry["node_id"],
                entry["slug"],
                (entry.get("pubkey_b64") or "")[:24],
                approve_url=approve_url,
            )
        except Exception:
            pass  # notifier errors must never block the join response
        return handler._reply(
            {"request_id": entry["request_id"], "status": entry["status"]}
        )
    except Exception as e:
        return handler._reply({"error": str(e)}, 500)


def handle_pending(handler, conn, principal, d):
    """GET /pending — admin only; list all pending join requests."""
    return handler._reply({"pending": handshake.list_pending()})


def handle_approve(handler, conn, principal, d):
    """POST /approve — admin only; approve or deny a join request."""
    import security

    request_id = (d or {}).get("request_id")
    decision = (d or {}).get("decision")
    pol = security.get_policy()
    if decision == "approve":
        result = handshake.approve(request_id, pol.mint_invite)
    else:
        result = handshake.deny(request_id)
    if result is None:
        return handler._reply({"error": "unknown request_id"}, 404)
    return handler._reply(
        {
            "ok": True,
            "status": result["status"],
            "token": result.get("token"),
            "expires_at": result.get("expires_at"),
        }
    )


def handle_approve_form(handler, conn, principal, d):
    """GET /approve?request_id=… — public; renders a phone-friendly HTML page
    with Approve / Deny buttons that POST back to /approve (JSON endpoint).

    The GET form is intentionally public (anyone with the request_id can view
    it).  The actual POST to /approve is still auth-gated by do_POST in
    mailroom_server.py.
    """
    qs = parse_qs(urlparse(handler.path).query)
    request_id = (qs.get("request_id") or [""])[0]
    entry = handshake.get(request_id) if request_id else None

    def _html_reply(body: str, code: int = 200) -> None:
        b = body.encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(b)))
        handler.end_headers()
        handler.wfile.write(b)

    if entry is None:
        _html_reply(
            "<!doctype html><html><body><h2>Unknown request</h2>"
            "<p>No join request found for that ID.</p></body></html>",
            404,
        )
        return

    import time as _time

    requested_iso = _time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(entry.get("requested_at", 0))
    )
    pubkey_short = (entry.get("pubkey_b64") or "")[:24]
    status = entry.get("status", "pending")

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rookery Join Request</title>
<style>
  body{{font-family:system-ui,sans-serif;max-width:480px;margin:2rem auto;padding:0 1rem;color:#222}}
  h1{{font-size:1.3rem;margin-bottom:1rem}}
  table{{border-collapse:collapse;width:100%;margin-bottom:1.5rem}}
  td{{padding:.4rem .6rem;border-bottom:1px solid #ddd;word-break:break-all}}
  td:first-child{{font-weight:600;white-space:nowrap;width:7rem}}
  .status{{font-weight:700;color:{'#b45309' if status=='pending' else '#166534' if status=='approved' else '#991b1b'}}}
  label{{display:block;margin-bottom:.4rem;font-size:.9rem}}
  input[type=text]{{width:100%;padding:.4rem;box-sizing:border-box;border:1px solid #bbb;border-radius:4px}}
  .btns{{display:flex;gap:.8rem;margin-top:1rem}}
  button{{flex:1;padding:.7rem;font-size:1rem;border:none;border-radius:6px;cursor:pointer}}
  .approve{{background:#16a34a;color:#fff}}
  .deny{{background:#dc2626;color:#fff}}
  #msg{{margin-top:.8rem;font-size:.9rem}}
</style>
</head>
<body>
<h1>Rookery Join Request</h1>
<table>
  <tr><td>Node</td><td>{entry.get('node_id','')}</td></tr>
  <tr><td>Slug</td><td>{entry.get('slug','')}</td></tr>
  <tr><td>Pubkey</td><td>{pubkey_short}…</td></tr>
  <tr><td>Requested</td><td>{requested_iso}</td></tr>
  <tr><td>Status</td><td class="status">{status}</td></tr>
</table>
<label for="tok">Bearer token (required to approve/deny):</label>
<input type="text" id="tok" placeholder="your ROOKERY_TOKEN" autocomplete="off">
<div class="btns">
  <button class="approve" onclick="act('approve')">Approve</button>
  <button class="deny" onclick="act('deny')">Deny</button>
</div>
<p id="msg"></p>
<script>
function act(decision){{
  var tok=document.getElementById('tok').value.trim();
  var msg=document.getElementById('msg');
  fetch('/approve',{{method:'POST',
    headers:{{'Content-Type':'application/json','Authorization':'Bearer '+tok}},
    body:JSON.stringify({{request_id:'{request_id}',decision:decision}})
  }}).then(function(r){{return r.json().then(function(j){{return{{ok:r.ok,j:j}}}});}})
  .then(function(x){{msg.textContent=x.ok?'Done: '+x.j.status:('Error: '+(x.j.error||JSON.stringify(x.j)));msg.style.color=x.ok?'green':'red';}})
  .catch(function(e){{msg.textContent='Network error: '+e;msg.style.color='red';}});
}}
</script>
</body>
</html>"""
    _html_reply(html)


def handle_join_poll(handler, conn, principal, request_id, card_pubkey_fn=None):
    """GET /join/<request_id> — long-poll until approved/denied/timeout (30 s).

    card_pubkey_fn: zero-arg callable that returns the base64 Ed25519 public key
    of the sidecar's card-signing key, or None if the sidecar is not signing.
    Callers in mailroom_server.py should pass _card_pubkey directly so the
    correct runtime module globals are used (avoids the __main__ vs named-module
    split when the server file is run as a script).
    """
    if not request_id:
        return handler._reply({"error": "not found"}, 404)
    if handshake.get(request_id) is None:
        return handler._reply({"error": "unknown request_id"}, 404)
    deadline = time.time() + 30
    while time.time() < deadline:
        entry = handshake.get(request_id)
        if entry is None:
            return handler._reply({"error": "unknown request_id"}, 404)
        if entry["status"] == "approved":
            return handler._reply(
                {
                    "status": "approved",
                    "token": entry["token"],
                    "card_pubkey": card_pubkey_fn() if card_pubkey_fn else None,
                    "expires_at": entry["expires_at"],
                }
            )
        if entry["status"] == "denied":
            return handler._reply({"status": "denied"})
        time.sleep(0.5)
    return handler._reply({"status": "pending"})
