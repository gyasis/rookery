"""cli_join — `rookery join` subcommand implementation."""

import json
import os
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

import discovery
import identity
import known_hosts
import turn_block


def _ansi(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stderr.isatty() else text


_WORDS = (
    "apricot blackberry cinnamon dandelion eclipse falcon ginger harbor indigo "
    "jasmine kestrel lantern mango nectar opal pomegranate quail raven sapphire "
    "tangerine umber violet walnut xenon yarrow zinnia amber basil clover delta "
    "ember fjord glacier helix iris jade kelp linden meadow nimbus orchid plum "
    "quartz reed sage thistle ulu vanilla willow xylophone yew zephyr almond "
    "birch cardinal dahlia ember fern gardenia hawthorn ivy juniper kale "
    "larkspur maple"
).split()


def cmd_up(args):
    # ------------------------------------------------------------------ #
    # 1. Resolve target mailroom
    # ------------------------------------------------------------------ #
    if args.url:
        url = args.url.rstrip("/")
    else:
        mdns = discovery.discover_mailrooms(timeout=2.0)
        udp = discovery.listen(timeout=2.0)
        # Dedupe by (host, port)
        seen, combined = set(), []
        for e in mdns + udp:
            key = (e["host"], e["port"])
            if key not in seen:
                seen.add(key)
                combined.append(e)
        if not combined:
            print(
                "No mailroom found on the LAN. "
                "Pass `rookery join <url>` for an explicit address.",
                file=sys.stderr,
            )
            sys.exit(1)
        elif len(combined) == 1:
            e = combined[0]
            url = f"http://{e['host']}:{e['port']}"
            print(f"Discovered mailroom at {url}")
        else:
            print("Multiple mailrooms found:")
            for i, e in enumerate(combined, 1):
                print(f"  {i}) http://{e['host']}:{e['port']}  ({e.get('name', '')})")
            try:
                choice = input(f"Which one? [1-{len(combined)}]: ").strip()
                idx = int(choice) - 1
                if not (0 <= idx < len(combined)):
                    raise ValueError
            except (ValueError, EOFError):
                print("Invalid selection.", file=sys.stderr)
                sys.exit(1)
            e = combined[idx]
            url = f"http://{e['host']}:{e['port']}"

    # ------------------------------------------------------------------ #
    # 2. SSL context (for https:// URLs with --insecure-tls)
    # ------------------------------------------------------------------ #
    def _ssl_ctx():
        if args.insecure_tls and url.startswith("https://"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def _open(req, timeout=35):
        ctx = _ssl_ctx()
        return (
            urllib.request.urlopen(req, timeout=timeout, context=ctx)
            if ctx
            else urllib.request.urlopen(req, timeout=timeout)
        )

    # ------------------------------------------------------------------ #
    # 3. 3-word slug
    # ------------------------------------------------------------------ #
    slug = "-".join(secrets.choice(_WORDS) for _ in range(3))

    # ------------------------------------------------------------------ #
    # 4. Node identity
    # ------------------------------------------------------------------ #
    node_id = args.node_id or f"peer-{socket.gethostname().replace('.', '-')}"
    if not turn_block.is_safe_name(node_id):      # it names the token file and `rookery check` needs it
        print(f"rookery join: {node_id!r} is not a safe node name. Pass --node-id NAME "
              "(letters, digits, '.', '_', '-').", file=sys.stderr)
        sys.exit(2)
    identity.load_or_create()
    pubkey_b64 = identity.pubkey_b64()

    # ------------------------------------------------------------------ #
    # 5. POST /join
    # ------------------------------------------------------------------ #
    join_body = json.dumps(
        {"node_id": node_id, "pubkey_b64": pubkey_b64, "slug": slug}
    ).encode()
    req = urllib.request.Request(
        f"{url}/join",
        data=join_body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _open(req, timeout=15) as r:
            join_resp = json.loads(r.read())
    except Exception as e:
        print(f"Failed to reach mailroom at {url}: {e}", file=sys.stderr)
        sys.exit(1)

    request_id = join_resp.get("request_id")
    if not request_id:
        print(f"Unexpected /join response: {join_resp}", file=sys.stderr)
        sys.exit(1)

    slug_display = _ansi("1;93", slug)
    print(f"Waiting for approval on the mailroom. Your code:\n  → {slug_display}\n")

    # ------------------------------------------------------------------ #
    # 6. Long-poll /join/<request_id>
    # ------------------------------------------------------------------ #
    deadline = time.monotonic() + 300  # 5-minute outer timeout
    token = card_pubkey = None
    while time.monotonic() < deadline:
        poll_req = urllib.request.Request(f"{url}/join/{request_id}")
        try:
            with _open(poll_req, timeout=35) as r:
                resp = json.loads(r.read())
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1.0)
            continue

        status = resp.get("status")
        if status == "approved":
            token = resp.get("token")
            card_pubkey = resp.get("card_pubkey")
            break
        elif status == "denied":
            print("Approval denied.", file=sys.stderr)
            sys.exit(1)
        # "pending" → keep polling

    else:
        print("Timed out waiting for approval (5 min).", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 7. TOFU pin on card_pubkey
    # ------------------------------------------------------------------ #
    if not card_pubkey:
        print("WARN: sidecar is not signing its agent card — TOFU pin skipped.")
    else:
        state = known_hosts.check(url, card_pubkey)
        if state == "changed":
            # Read stored key for the warning
            stored = next(
                (e.pubkey for e in known_hosts.load() if e.host == url), "<not found>"
            )
            print(
                "\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
                "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
                f"The Ed25519 card public key for {url} does NOT match the one\n"
                f"stored in ~/.rookery/known_hosts.\n"
                f"  expected: {stored}\n"
                f"  got:      {card_pubkey}\n"
                "Someone could be eavesdropping (man-in-the-middle attack).\n"
                "If you trust the new key, remove the old line from ~/.rookery/known_hosts\n"
                "and re-run `rookery join`.",
                file=sys.stderr,
            )
            sys.exit(1)
        elif state == "unknown":
            known_hosts.add(url, card_pubkey, label=node_id)
            print(f"Pinned card pubkey for {url} (first contact).")
        else:  # 'ok'
            print(f"Pinned card pubkey for {url} matches known_hosts.")

    # ------------------------------------------------------------------ #
    # 8. Store the token (mode 600) for `rookery check`
    # ------------------------------------------------------------------ #
    tok_path = os.path.join(os.path.expanduser("~"), ".rookery", "remote", node_id + ".token")
    os.makedirs(os.path.dirname(tok_path), exist_ok=True)
    fd = os.open(tok_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token or "")
    print(f"token: {tok_path}")

    # ------------------------------------------------------------------ #
    # 9. Success block
    # ------------------------------------------------------------------ #
    check = _ansi("1;32", "✓")
    tls = "\n  export ROOKERY_INSECURE_TLS=1" if args.insecure_tls else ""
    print(
        "\n"
        "═" * 64 + "\n"
        f"{check} joined the mailroom as `{node_id}`  →  {url}\n"
        "\n"
        "Set these, then run `rookery check` to read your mail:\n"
        "\n"
        f"  export ROOKERY_URL={url}\n"
        f"  export ROOKERY_NODE={node_id}\n"
        f"  export ROOKERY_TOKEN=\"$(cat {tok_path})\"{tls}\n"
        "\n"
        "A remote node cannot send mail: Rookery takes mail only from a\n"
        "sender in a session on the mailroom machine.\n"
        "\n" + "═" * 64
    )
