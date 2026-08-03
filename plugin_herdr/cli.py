"""`rookery herdr` subcommands — the console side of the mesh.

    rookery herdr status            what herdr sees vs what the mailroom knows
    rookery herdr bind              enroll named panes as mesh nodes
    rookery herdr focus <node>      jump to a node's pane (e.g. whoever is blocked)
    rookery herdr notify <title>    raise a console toast by hand

`status` is the approval surface: it puts a blocked node, its pane, and the
exact approve command on one line, so a NEEDCRED goes from "buried in a log"
to "focus that pane and decide".
"""
import sys

import rookery as R

from . import bridge as H


def _warn_if_off():
    """Print why herdr is silent, if it is. Returns True when it is live."""
    why = H.reason()
    if why:
        print(f"herdr: {why}\n", file=sys.stderr)
        return False
    return True


def cmd_herdr_status(args):
    """Console + mailroom side by side — the two halves of a node's story."""
    live = _warn_if_off()
    conn = R.connect()
    agents = H.agents()
    by_name = {a["name"]: a for a in agents if a.get("name")}

    if agents:
        print(f"herdr panes ({len(agents)} recognised, "
              f"{len(by_name)} named/bindable):")
        print(f"  {'PANE':<8} {'AGENT':<8} {'HERDR':<9} {'NODE':<16} {'MESH':<9} MAIL")
        for a in sorted(agents, key=lambda x: x.get("pane_id") or ""):
            name = a.get("name") or ""
            node = R.get_node(conn, name) if name else None
            pending = conn.execute(
                "SELECT COUNT(*) c FROM inbox WHERE recipient=? AND status='pending'",
                (name,),
            ).fetchone()["c"] if name else 0
            print(f"  {a.get('pane_id',''):<8} {a.get('agent',''):<8} "
                  f"{a.get('agent_status',''):<9} {name or '—':<16} "
                  f"{(node['status'] if node else '—'):<9} "
                  f"{pending if name else '—'}")
        # A named pane with no matching node has an identity nobody enrolled.
        unbound = [n for n in by_name if R.get_node(conn, n) is None]
        if unbound:
            print(f"\n  not enrolled yet: {', '.join(sorted(unbound))} "
                  f"— run `rookery herdr bind`")
    elif live:
        print("herdr is running but recognises no agent panes.")

    # Mesh nodes herdr cannot see — headless/ephemeral, and that is correct.
    rows = conn.execute("SELECT * FROM nodes ORDER BY node_id").fetchall()
    offstage = [r for r in rows if r["node_id"] not in by_name]
    if offstage:
        print(f"\nmesh nodes with no pane ({len(offstage)}) — headless, $0 when idle:")
        for r in offstage:
            print(f"  {r['node_id']:<16} kind={r['kind'] or '—':<9} "
                  f"status={r['status'] or '—'}")

    # The approval surface.
    pend = R.pending_credentials(conn)
    print()
    if not pend:
        print("no credential requests waiting on you.")
        return
    print(f"NEEDCRED — {len(pend)} request(s) waiting on a human:")
    for r in pend:
        pane = R.get_address(conn, r["node_id"])
        seen = (by_name.get(r["node_id"]) or {}).get("agent_status")
        where = f"pane {pane}" + (f", herdr says {seen}" if seen else "") if pane else "no pane"
        print(f"  #{r['id']}  {r['node_id']} -> {r['resource']}  ({where})")
        if pane:
            print(f"      rookery herdr focus {r['node_id']}")
        print(f"      mesh_approve.py --id {r['id']} --approve")


def cmd_herdr_bind(args):
    """Make a node's identity and its pane the same thing.

    Name the pane first: `herdr agent rename w1:p7 learner`.
    """
    H.require("rookery herdr bind")
    conn = R.connect()
    bound = H.bind_nodes(conn, register_new=not args.existing_only,
                         sync_status=args.sync_status)
    if not bound:
        print("nothing bound — no herdr pane carries a name. "
              "Name one first: herdr agent rename <pane> <node-id>")
        return
    for node_id, pane in bound:
        print(f"bound {node_id:<16} -> {pane}")
    print(f"\n{len(bound)} node(s) bound. A restarted agent can now be told which "
          f"node it is from its pane, instead of losing that with its context.")


def cmd_herdr_focus(args):
    """Jump the human to a node's pane."""
    H.require("rookery herdr focus")
    conn = R.connect()
    # Accept a node id (resolved through the mailroom binding) or a raw pane id.
    target = R.get_address(conn, args.node) or args.node
    if not H.focus(target):
        print(f"could not focus '{target}' — is it a live herdr pane? "
              f"(`rookery herdr status` lists them)", file=sys.stderr)
        sys.exit(1)
    print(f"focused {args.node} ({target})")


def cmd_herdr_notify(args):
    """Raise a console toast by hand — useful from scripts and demos."""
    H.require("rookery herdr notify")
    if not H.notify(args.title, body=args.body, sound=args.sound):
        print("notification not shown", file=sys.stderr)
        sys.exit(1)
