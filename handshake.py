"""Handshake queue — tracks join requests from peers who want to enter the mesh.

The queue is in-memory (a plain dict keyed by request_id). Entries are lost on
restart, which is intentional: stale unapproved requests should not persist
across sidecar restarts. Wave 3 (T008) will mutate entries here to add a token
and flip status to 'approved' or 'rejected' when an admin decides.
"""
import copy
import secrets
import time

# Module-global pending queue: request_id -> entry dict.
PENDING: dict[str, dict] = {}


def new_request(node_id: str, pubkey_b64: str, slug: str) -> dict:
    """Create a new join request, store it in PENDING, and return a copy.

    Args:
        node_id:    Human-readable identifier the peer wants to claim.
        pubkey_b64: Base64-encoded Ed25519 public key from the peer.
        slug:       Shared human-memorable slug (helps admin recognise the peer).

    Returns:
        A deep copy of the stored entry.
    """
    request_id = "req-" + secrets.token_urlsafe(16)
    entry = {
        "request_id":   request_id,
        "node_id":      node_id,
        "pubkey_b64":   pubkey_b64,
        "slug":         slug,
        "requested_at": time.time(),
        "status":       "pending",
        "decision_at":  None,
        "token":        None,
        "expires_at":   None,
    }
    PENDING[request_id] = entry
    return copy.deepcopy(entry)


def list_pending() -> list[dict]:
    """Return deep copies of all entries whose status is 'pending'."""
    return [copy.deepcopy(v) for v in PENDING.values() if v["status"] == "pending"]


def get(request_id: str) -> dict | None:
    """Return a deep copy of the entry for request_id, or None if not found."""
    entry = PENDING.get(request_id)
    return copy.deepcopy(entry) if entry is not None else None
