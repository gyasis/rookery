"""Rookery x herdr bridge — herdr is the CONSOLE, Rookery is the NETWORK.

herdr (herdr.dev) is a terminal workspace manager that RECOGNISES coding agents
inside panes and reports each one's lifecycle state (working / blocked / done /
idle / unknown). That is precisely the information the mailroom cannot know:
Rookery knows what a node was ASKED, herdr knows what it is DOING right now.

The mechanism boundary this module is careful not to blur (docs/HERDR_INTEGRATION.md):

    human -> agent    pane injection            steering, interrupting, pairing
    agent -> agent    MAIL                      regardless of distance
    agent -> agent    `herdr agent read`        read-only OBSERVATION

so prompt() below is the HUMAN's on-ramp and the terminal-node watcher's
delivery path — it is NOT how two agents talk to each other. Agents mail.

Everything here DEGRADES SILENTLY: herdr is optional and never a dependency.
No herdr binary, no herdr server, or running outside a herdr pane all turn
every call into a no-op returning None/False/[]. The one exception is
require(), for when the operator explicitly named herdr and a silent no-op
would be a lie. Stdlib only.
"""
import json
import os
import shutil
import subprocess

# herdr agent_status -> nodes.status (schema.sql: idle|busy|waiting|asleep|offline).
# 'unknown' maps to None deliberately: herdr's own docs warn it does not mean
# done, so it must never overwrite a status the mesh is surer about.
_STATUS_MAP = {
    "working": "busy",
    "blocked": "waiting",
    "done": "idle",
    "idle": "idle",
    "unknown": None,
}


# --- gating (W5) -----------------------------------------------------------
def _mode() -> str:
    """ROOKERY_HERDR: auto (default) | off | force."""
    return (os.environ.get("ROOKERY_HERDR") or "auto").strip().lower()


def available() -> bool:
    """Is the herdr CLI on PATH at all?"""
    return shutil.which("herdr") is not None


def in_pane() -> bool:
    """Are we running INSIDE a herdr-owned pane? herdr exports HERDR_ENV=1
    there and refuses nested launches, so herdr-aware code must check it."""
    return os.environ.get("HERDR_ENV") == "1"


def self_pane() -> str | None:
    """This process's own pane id (e.g. 'w1:p6') when herdr owns the terminal.

    A node started inside a pane can therefore learn its own address without
    being told — see bind_nodes() for why that matters.
    """
    return os.environ.get("HERDR_PANE_ID") or None


def enabled() -> bool:
    """Should herdr calls actually run?

    auto  — the herdr binary exists AND we are inside a herdr pane
    force — the binary exists; for a mesh process living outside a pane
            (launchd, systemd, ssh) that still wants to drive the console
    off   — never
    """
    mode = _mode()
    if mode == "off" or not available():
        return False
    if mode == "force":
        return True
    return in_pane()


def reason() -> str | None:
    """Why herdr calls are no-ops right now — None when they are not.

    Separate from require() so a status command can EXPLAIN the silence
    instead of exiting on it.
    """
    if enabled():
        return None
    if _mode() == "off":
        return "disabled by ROOKERY_HERDR=off"
    if not available():
        return "the 'herdr' CLI is not on PATH — install it (herdr.dev)"
    return (
        "not inside a herdr pane (HERDR_ENV is unset). Run from a herdr pane, "
        "or set ROOKERY_HERDR=force when the mesh process lives outside one "
        "(launchd/systemd/ssh)."
    )


def require(what: str = "herdr integration") -> None:
    """Fail LOUDLY instead of no-opping.

    The silent degrade is right for optional extras (a notification nobody
    sees costs nothing). It is wrong when the operator explicitly asked for
    herdr — `--injector herdr` that quietly delivers nothing is worse than an
    error, because the mail is acked either way.
    """
    why = reason()
    if why:
        raise SystemExit(f"{what}: {why}")


# --- transport -------------------------------------------------------------
def _run(args: list[str], timeout: float = 10.0) -> str | None:
    """Run a herdr subcommand; stdout on success, None on ANY failure.

    Never raises. A broken or absent console must not take down a mesh
    process whose real job is delivering mail.
    """
    if not enabled():
        return None
    try:
        r = subprocess.run(
            ["herdr", *args], capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def _run_json(args: list[str], timeout: float = 10.0):
    """herdr CLI replies with {"id":..., "result": {...}}. Return the envelope."""
    out = _run(args, timeout)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


# --- inspection ------------------------------------------------------------
def agents() -> list[dict]:
    """Every agent pane herdr currently recognises.

    Returns [] when herdr is absent or disabled, so callers can iterate blind.
    """
    doc = _run_json(["agent", "list"])
    if not doc:
        return []
    return (doc.get("result") or {}).get("agents") or []


def find(target: str | None) -> dict | None:
    """Resolve a node name, pane id or terminal id to its agent record."""
    if not target:
        return None
    for a in agents():
        if target in (a.get("name"), a.get("pane_id"), a.get("terminal_id")):
            return a
    return None


def status_of(target: str) -> str | None:
    """herdr's lifecycle state for one agent: working|blocked|done|idle|unknown."""
    a = find(target)
    return a.get("agent_status") if a else None


# --- actions ---------------------------------------------------------------
def notify(
    title: str,
    body: str | None = None,
    sound: str | None = None,
    position: str | None = None,
) -> bool:
    """Raise a herdr toast. True if shown, False if herdr no-opped.

    sound: none | done | request   position: top-left | top-right | bottom-left | bottom-right
    """
    args = ["notification", "show", title]
    if body:
        args += ["--body", body]
    if sound:
        args += ["--sound", sound]
    if position:
        args += ["--position", position]
    return _run(args) is not None


def prompt(target: str, text: str, timeout: float = 15.0) -> bool:
    """Submit TEXT to the recognised agent in pane TARGET.

    Unlike tmux/wezterm/zellij send-keys this addresses an AGENT rather than a
    terminal, and submits — so there is no separate Enter keystroke to send.
    Reserved for human->agent steering and the terminal-node watcher.
    """
    return _run(["agent", "prompt", target, text], timeout=timeout) is not None


def read(target: str, lines: int = 40, source: str = "recent") -> str | None:
    """Read a pane's recent output — the read-only agent->agent OBSERVATION
    channel. No injection, no state change, nothing to lose or interleave."""
    args = ["agent", "read", target, "--source", source]
    if lines:
        args += ["--lines", str(lines)]
    return _run(args, timeout=15.0)


def focus(target: str) -> bool:
    """Bring the human to this agent's pane — the jump-to-whoever-is-blocked move."""
    return _run(["agent", "focus", target]) is not None


# --- pane <-> node binding (W3) --------------------------------------------
def bind_nodes(conn, register_new: bool = True, sync_status: bool = False) -> list[tuple]:
    """Enroll herdr's NAMED panes as mesh nodes, so a node's identity and its
    pane are the same thing. Returns the (node_id, pane_id) pairs bound.

    Only panes you have named — `herdr agent rename <target> <node-id>` — are
    bound; an unnamed pane has no mesh identity to claim, and guessing one from
    a terminal title would bind the wrong agent.

    This is the standing fix for the failure mode where restarting a mesh agent
    wiped its briefing while its mail survived: the agent came back not knowing
    which node it was, reported an empty inbox it could no longer read, and
    mistook another pane for its peer. Identity that lives in the pane survives
    a restart of what is inside the pane.

    sync_status mirrors herdr's lifecycle state onto nodes.status ('blocked'
    becomes 'waiting'), giving the mailroom a view of what its nodes are
    actually doing. 'unknown' never overwrites.
    """
    import rookery as R

    bound = []
    for a in agents():
        node_id = a.get("name")
        pane = a.get("pane_id")
        if not node_id or not pane:
            continue
        if R.get_node(conn, node_id) is None:
            if not register_new:
                continue
            R.register_node(conn, node_id, kind="terminal")
        R.set_address(conn, node_id, pane)
        if sync_status:
            mapped = _STATUS_MAP.get(a.get("agent_status"))
            if mapped:
                R.set_status(conn, node_id, mapped)
        bound.append((node_id, pane))
    return bound
