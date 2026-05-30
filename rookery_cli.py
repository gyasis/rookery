#!/usr/bin/env python3
"""Rookery CLI — unified entrypoint for the Rookery agent-mesh sidecar.

rookery_cli.py serve   [--host] [--port] [--public-url] [--card-key] [--token]
rookery_cli.py up      [url]    [--node-id] [--insecure-tls]
rookery_cli.py approve [--url]  [--token]
"""

import argparse
import os

from cli_approve import cmd_approve
from cli_known_hosts import cmd_known_hosts_forget, cmd_known_hosts_list
from cli_serve import cmd_serve
from cli_up import cmd_up

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser():
    ap = argparse.ArgumentParser(
        prog="rookery_cli",
        description="Rookery agent-mesh CLI.",
    )
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # --- serve ---
    p_serve = sub.add_parser("serve", help="Run the mailroom HTTP sidecar.")
    p_serve.add_argument(
        "--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)"
    )
    p_serve.add_argument(
        "--port", type=int, default=8765, help="Bind port (default 8765)"
    )
    p_serve.add_argument(
        "--public-url",
        default=None,
        help="How peers reach this sidecar (used in the A2A Agent Card)",
    )
    p_serve.add_argument(
        "--card-key",
        default=None,
        help="Ed25519 private-key PEM — signs the agent card",
    )
    p_serve.add_argument(
        "--token",
        default=os.environ.get("ROOKERY_TOKEN"),
        help="Bearer token for non-public endpoints (default $ROOKERY_TOKEN)",
    )
    p_serve.set_defaults(func=cmd_serve)

    # --- up ---
    p_up = sub.add_parser("up", help="Bring a peer node online via a remote mailroom.")
    p_up.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Mailroom URL (e.g. http://host:8765); defaults to $ROOKERY_URL",
    )
    p_up.add_argument("--node-id", default=None, help="Node identifier to register")
    p_up.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Skip TLS certificate verification (trusted LAN only)",
    )
    p_up.set_defaults(func=cmd_up)

    # --- approve ---
    p_approve = sub.add_parser("approve", help="Approve a pending credential request.")
    p_approve.add_argument(
        "--url",
        default="http://localhost:8765",
        help="Mailroom URL (default http://localhost:8765)",
    )
    p_approve.add_argument(
        "--token",
        default=os.environ.get("ROOKERY_TOKEN"),
        help="Bearer token (default $ROOKERY_TOKEN)",
    )
    p_approve.set_defaults(func=cmd_approve)

    # --- known-hosts ---
    p_kh = sub.add_parser(
        "known-hosts", help="Manage trusted peers (~/.rookery/known_hosts)."
    )
    kh_sub = p_kh.add_subparsers(dest="kh_command", metavar="<kh_command>")
    kh_sub.required = True

    p_kh_list = kh_sub.add_parser("list", help="List known hosts.")
    p_kh_list.set_defaults(func=cmd_known_hosts_list)

    p_kh_forget = kh_sub.add_parser("forget", help="Remove a known host.")
    p_kh_forget.add_argument("host", help="Host (URL or hostname) to remove.")
    p_kh_forget.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt."
    )
    p_kh_forget.set_defaults(func=cmd_known_hosts_forget)

    return ap


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    ap = build_parser()
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
