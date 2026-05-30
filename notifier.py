"""Rookery out-of-band notifier — dispatches a push notification when a peer
POSTs /join so the admin can approve from anywhere (ntfy, Pushover, webhook).

Single public function:
    notify_pending_join(request_id, node_id, slug, pubkey_b64_short, approve_url)

Reads ROOKERY_NOTIFY_METHOD env.  Default 'none' = silent no-op.
NEVER raises — logs failures to stderr only.  Stdlib only.
"""

import json
import os
import sys
import urllib.parse
import urllib.request


def notify_pending_join(
    request_id: str,
    node_id: str,
    slug: str,
    pubkey_b64_short: str,
    approve_url: str | None = None,
) -> None:
    """Dispatch an out-of-band notification about a pending join request.

    Reads ROOKERY_NOTIFY_METHOD env (one of: none, ntfy, pushover, webhook).
    Default 'none' = silently no-op.  NEVER raises — logs failures to stderr.
    """
    method = (os.environ.get("ROOKERY_NOTIFY_METHOD") or "none").strip().lower()
    if not method or method == "none":
        return

    if method == "ntfy":
        _notify_ntfy(request_id, node_id, slug, approve_url)
    elif method == "pushover":
        _notify_pushover(request_id, node_id, slug, approve_url)
    elif method == "webhook":
        _notify_webhook(request_id, node_id, slug, pubkey_b64_short, approve_url)
    else:
        print(
            f"[notifier] unknown ROOKERY_NOTIFY_METHOD={method!r}; "
            "use none/ntfy/pushover/webhook",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------


def _notify_ntfy(
    request_id: str, node_id: str, slug: str, approve_url: str | None
) -> None:
    topic = os.environ.get("ROOKERY_NTFY_TOPIC", "").strip()
    if not topic:
        print("[notifier:ntfy] ROOKERY_NTFY_TOPIC not set", file=sys.stderr)
        return
    url = f"https://ntfy.sh/{topic}"
    msg = f'Peer "{node_id}" wants to join. Slug: {slug}.'
    if approve_url:
        msg += f" Approve: {approve_url}"
    headers = {
        "Title": "Rookery join request",
        "Priority": "high",
        "Content-Type": "text/plain",
    }
    if approve_url:
        headers["Click"] = approve_url
    req = urllib.request.Request(url, data=msg.encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[notifier:ntfy] {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pushover
# ---------------------------------------------------------------------------


def _notify_pushover(
    request_id: str, node_id: str, slug: str, approve_url: str | None
) -> None:
    token = os.environ.get("ROOKERY_PUSHOVER_TOKEN", "").strip()
    user = os.environ.get("ROOKERY_PUSHOVER_USER", "").strip()
    if not token or not user:
        print(
            "[notifier:pushover] ROOKERY_PUSHOVER_TOKEN and/or ROOKERY_PUSHOVER_USER not set",
            file=sys.stderr,
        )
        return
    msg = f'Peer "{node_id}" wants to join. Slug: {slug}.'
    if approve_url:
        msg += f" Approve: {approve_url}"
    fields = {
        "token": token,
        "user": user,
        "title": "Rookery join request",
        "message": msg,
    }
    if approve_url:
        fields["url"] = approve_url
        fields["url_title"] = "Approve"
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[notifier:pushover] {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------


def _notify_webhook(
    request_id: str,
    node_id: str,
    slug: str,
    pubkey_b64_short: str,
    approve_url: str | None,
) -> None:
    webhook_url = os.environ.get("ROOKERY_NOTIFY_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("[notifier:webhook] ROOKERY_NOTIFY_WEBHOOK_URL not set", file=sys.stderr)
        return
    webhook_token = os.environ.get("ROOKERY_NOTIFY_WEBHOOK_TOKEN", "").strip()
    payload = {
        "request_id": request_id,
        "node_id": node_id,
        "slug": slug,
        "pubkey": pubkey_b64_short,
        "approve_url": approve_url,
    }
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"
    req = urllib.request.Request(webhook_url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[notifier:webhook] {e}", file=sys.stderr)
