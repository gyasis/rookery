"""herdr plugin for Rookery — attaches herdr as an optional CONSOLE.

This directory is the ENTIRE herdr surface. Core imports none of it:
`plugins.load()` discovers any `plugin_*` module beside it and calls register(),
which attaches whatever herdr can currently provide. Delete this directory and
Rookery is unchanged — still a standalone messenger, still multiplexer-agnostic,
suite still green, `rookery --help` no longer mentions herdr.

    __init__.py   this file — the attachment points and nothing else
    bridge.py     the herdr CLI wrapper (every subprocess call lives here)
    cli.py        the `rookery herdr ...` subcommands

What it contributes:

    injector "herdr"   `herdr agent prompt` as a --injector for terminal_node
    an alert sink      `herdr notification show` for postmaster events
    `rookery herdr`    status / bind / focus / notify

The mechanism boundary the plugin exists to respect (docs/HERDR_INTEGRATION.md):
human->agent is pane injection, agent->agent is MAIL at any distance. The
injector is the human's on-ramp and the terminal-node watcher's delivery path —
never how two agents talk. Rookery keeps owning the messaging; herdr only
renders it and hands the human a keyboard.
"""
import re
import time

import plugins

from . import bridge as H

# event kind -> herdr toast sound. Unlisted events stay silent.
_SOUNDS = {"needcred": "request"}


def _send(target: str, text: str) -> bool:
    """Deliver to a recognised agent (bridge.prompt confirms submission)."""
    return H.prompt(target, text)


def _check() -> None:
    """Explicit opt-in, so fail loudly rather than silently dropping mail."""
    H.require("--injector herdr")


def _sink(event: str, title: str, body: str | None = None, **meta) -> bool:
    return H.notify(title, body=body, sound=_SOUNDS.get(event))


PROBE_TTL = 5.0     # seconds: one `herdr agent list` serves every identity of a tick


def _probe(pane: str, cache: dict | None = None) -> str | None:
    """herdr's agent_status for one pane, or None. `cache` is a dict the caller keeps
    ({"now": ts} is set by the caller); the listing in it is reused for PROBE_TTL seconds.
    Uses the bridge, so a disabled or absent herdr (enabled() false) gives None and no call."""
    if not H.enabled():
        return None
    cache = {} if cache is None else cache
    ts = cache.get("now", time.time())
    if "agents" not in cache or ts - cache.get("at", -1e18) >= PROBE_TTL or ts < cache["at"]:
        doc = H._run_json(["agent", "list"], timeout=2)
        cache["at"] = ts
        cache["agents"] = ((doc or {}).get("result") or {}).get("agents") or []
    for a in cache["agents"]:
        if a.get("pane_id") == pane:
            return a.get("agent_status")
    return None


# Feature 006: the input line of each agent kind, as herdr names the kind. The LAST match on the
# screen is the input box (sent prompts above it use the same mark). Dim text (a placeholder) is
# dropped first. A kind with no entry here (pi, omp) is not checked: the wake types as before.
INPUT_LINES = {
    "claude": re.compile(r"^\s*\u276f\s?(.*)$"),         # ❯
    "codex": re.compile(r"^\s*\u203a\s?(.*)$"),          # ›
    "kimi": re.compile(r"^\s*\u2502 >(.*?)\u2502?\s*$"),  # │ > ... │
}
_SGR = re.compile(r"\x1b\[([0-9;]*)m|\x1b\[[0-9;?]*[A-Za-z]")


def _undimmed(text: str) -> str:
    """Screen text with escape codes removed and dim (SGR 2) runs dropped."""
    out, dim, pos = [], False, 0
    for m in _SGR.finditer(text):
        if not dim:
            out.append(text[pos:m.start()])
        pos = m.end()
        if m.group(1) is not None:
            codes = (m.group(1) or "0").split(";")
            i = 0
            while i < len(codes):
                if codes[i] in ("38", "48", "58"):        # a color: 5;n or 2;r;g;b follows
                    i += 3 if codes[i + 1:i + 2] == ["5"] else 5
                    continue
                if codes[i] == "2":
                    dim = True
                elif codes[i] in ("0", "", "22"):
                    dim = False
                i += 1
    if not dim:
        out.append(text[pos:])
    return "".join(out)


def _input(pane: str) -> str | None:
    """The text in the input box of the agent in `pane`: "" when empty, None when unknown
    (herdr off, a kind with no INPUT_LINES entry, or no input line on the screen)."""
    if not H.enabled():
        return None
    doc = H._run_json(["agent", "get", pane], timeout=2)
    kind = (((doc or {}).get("result") or {}).get("agent") or {}).get("agent")
    pattern = INPUT_LINES.get(kind)
    if pattern is None:
        return None
    screen = H._run(["pane", "read", pane, "--source", "visible", "--format", "ansi"], timeout=2)
    if screen is None:
        return None
    found = None
    for line in _undimmed(screen).splitlines():
        m = pattern.match(line)
        if m:
            found = m.group(1).strip()
    return found


def _commands(subparsers) -> None:
    from . import cli

    p = subparsers.add_parser(
        "herdr", help="Console integration — pane<->node binding, focus, toasts."
    )
    sub = p.add_subparsers(dest="herdr_command", metavar="<herdr_command>")
    sub.required = True

    p_status = sub.add_parser(
        "status", help="What herdr sees vs what the mailroom knows (+ NEEDCRED)."
    )
    p_status.set_defaults(func=cli.cmd_herdr_status)

    p_bind = sub.add_parser("bind", help="Enroll named herdr panes as mesh nodes.")
    p_bind.add_argument(
        "--existing-only",
        action="store_true",
        help="Only bind panes whose node already exists (register nothing new).",
    )
    p_bind.add_argument(
        "--sync-status",
        action="store_true",
        help="Also mirror herdr's lifecycle state onto nodes.status "
             "(blocked -> waiting); 'unknown' never overwrites.",
    )
    p_bind.set_defaults(func=cli.cmd_herdr_bind)

    p_start = sub.add_parser(
        "start", help="Launch a BRIEFED mesh node in a pane, then bind it."
    )
    p_start.add_argument("node", help="Mesh node id; also the agent's herdr name.")
    p_start.add_argument("--pane", required=True, help="Existing pane at a shell prompt.")
    p_start.add_argument("--kind", default="claude",
                         help="Agent kind herdr should launch (default claude).")
    p_start.add_argument("--no-brief", action="store_true",
                         help="Skip the system-prompt briefing (not advised: an "
                              "unbriefed session treats delivered mail as "
                              "untrusted third-party text).")
    p_start.add_argument("--wait-ready", type=float, default=15.0, metavar="SECS",
                         help="retry this long while herdr reports the pane is "
                              "not yet an available shell (default 15; 0 = one "
                              "attempt). A just-created pane needs about a second.")
    p_start.add_argument("--print-briefing", action="store_true",
                         help="Print the briefing that would be used and exit.")
    p_start.set_defaults(func=cli.cmd_herdr_start)

    p_tmpl = sub.add_parser(
        "briefing-template",
        help="Show or write ~/.rookery/briefing_template.md (customise without "
             "editing the plugin)."
    )
    p_tmpl.add_argument("--write", action="store_true",
                        help="Write the built-in default out for editing.")
    p_tmpl.add_argument("--force", action="store_true",
                        help="Overwrite an existing template.")
    p_tmpl.set_defaults(func=cli.cmd_herdr_template)

    p_rebrief = sub.add_parser(
        "rebrief", help="Re-deliver a node's briefing after its session restarted."
    )
    p_rebrief.add_argument("node", nargs="?", default=None,
                           help="Node to re-brief; omit for every bound node.")
    p_rebrief.add_argument("--file-only", action="store_true",
                           help="Refresh ~/.rookery/briefings/<node>.md without "
                                "typing into the pane.")
    p_rebrief.set_defaults(func=cli.cmd_herdr_rebrief)

    p_focus = sub.add_parser("focus", help="Jump to a node's pane.")
    p_focus.add_argument("node", help="Node id (or a raw herdr pane id).")
    p_focus.set_defaults(func=cli.cmd_herdr_focus)

    p_notify = sub.add_parser("notify", help="Raise a console toast.")
    p_notify.add_argument("title")
    p_notify.add_argument("--body", default=None)
    p_notify.add_argument(
        "--sound", choices=["none", "done", "request"], default=None
    )
    p_notify.set_defaults(func=cli.cmd_herdr_notify)


def register() -> None:
    """Attach to the core registry. Called by plugins.load()."""
    if not H.available():
        # No herdr binary: contribute nothing at all, so `--injector herdr` is
        # not offered and `rookery herdr` never appears in --help. An install
        # that ships this plugin is indistinguishable from one that does not.
        return

    # The injector attaches whenever herdr is INSTALLED, even if unusable right
    # now (outside a pane, say) — so the operator gets _check()'s diagnostic
    # rather than a bare "unknown injector".
    plugins.injector("herdr", _send, check=_check)
    plugins.sink(_sink)
    plugins.status_probe("herdr-pane", _probe)
    plugins.input_probe("herdr-pane", _input)
    plugins.command(_commands)
