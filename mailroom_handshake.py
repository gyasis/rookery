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
