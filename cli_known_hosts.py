"""cli_known_hosts — `rookery known-hosts` subcommand implementations."""

import sys

import known_hosts


def _ansi(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def cmd_known_hosts_list(args):
    """List all entries in ~/.rookery/known_hosts."""
    entries = known_hosts.load()
    if not entries:
        print("No known hosts.")
        return
    for e in entries:
        host = _ansi("1", e.host)
        pk_short = e.pubkey[:24] + "..."
        date = e.added[:10] if e.added else "unknown"
        print(f"{host}  {pk_short}  ({e.label}, added {date})")


def cmd_known_hosts_forget(args):
    """Remove a host from ~/.rookery/known_hosts."""
    entries = known_hosts.load()
    match = next((e for e in entries if e.host == args.host), None)
    if match is None:
        print(f"Host '{args.host}' not in known_hosts.", file=sys.stderr)
        sys.exit(1)

    pk_short = match.pubkey[:16] + "..."
    date = match.added[:10] if match.added else "unknown"

    if not getattr(args, "yes", False):
        try:
            answer = (
                input(
                    f"Remove '{match.host}' (pubkey {pk_short}, added {date})? [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            print("Cancelled.")
            return
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return

    known_hosts.remove(args.host)
    print(f"Removed '{args.host}'.")
