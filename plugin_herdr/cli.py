"""`rookery herdr` subcommands — the console side of Rookery.

    rookery herdr status            each herdr pane and the identity bound to it
    rookery herdr focus <name>      jump to an identity's pane
    rookery herdr notify <title>    raise a console toast by hand
"""
import sys

import turn_block

from . import bridge as H

# The identity and session bound to each herdr pane (`rookery up` or `rookery identity bind`).
_PANE_BINDINGS = (
    "SELECT b.value AS pane, i.name, s.name AS session, s.state FROM bindings b "
    "JOIN identities i ON i.agent_id = b.owner_id AND i.session_id = b.session_id "
    "JOIN rookery_sessions s ON s.session_id = b.session_id "
    "WHERE b.owner_type = 'identity' AND b.kind = 'herdr-pane' ORDER BY b.created_at, b.rowid")


def _warn_if_off():
    """Print why herdr is silent, if it is. Returns True when it is live."""
    why = H.reason()
    if why:
        print(f"herdr: {why}\n", file=sys.stderr)
        return False
    return True


def _bindings():
    """{pane id: [(identity name, session name, session state), ...]}."""
    conn = turn_block.open_db()
    try:
        out = {}
        for r in conn.execute(_PANE_BINDINGS):
            out.setdefault(r["pane"], []).append((r["name"], r["session"], r["state"]))
        return out
    finally:
        conn.close()


def cmd_herdr_status(args):
    """Each pane herdr sees, with the Rookery identity and session bound to it."""
    live = _warn_if_off()
    agents = H.agents()
    if not agents:
        if live:
            print("herdr is running but recognises no agent panes.")
        return
    bound = _bindings()
    print(f"herdr panes ({len(agents)}):")
    print(f"  {'PANE':<8} {'AGENT':<8} {'HERDR':<9} {'IDENTITY':<16} SESSION")
    for a in sorted(agents, key=lambda x: x.get("pane_id") or ""):
        pane = a.get("pane_id") or ""
        rows = bound.get(pane) or [("—", "— (no binding)", None)]
        for name, session, state in rows:
            ended = " (ended)" if state and state != "open" else ""
            print(f"  {pane:<8} {a.get('agent', ''):<8} {a.get('agent_status', ''):<9} "
                  f"{name:<16} {session}{ended}")


def cmd_herdr_focus(args):
    """Jump the human to the pane of an identity name (open sessions first), or to a raw pane id."""
    H.require("rookery herdr focus")
    hits = [(state != "open", pane) for pane, rows in _bindings().items()
            for name, _session, state in rows if name == args.name]
    target = min(hits)[1] if hits else args.name
    if not H.focus(target):
        print(f"could not focus '{target}' — is it a live herdr pane? "
              f"(`rookery herdr status` lists them)", file=sys.stderr)
        sys.exit(1)
    print(f"focused {args.name} ({target})")


def cmd_herdr_notify(args):
    """Raise a console toast by hand — useful from scripts."""
    H.require("rookery herdr notify")
    if not H.notify(args.title, body=args.body, sound=args.sound):
        print("notification not shown", file=sys.stderr)
        sys.exit(1)
