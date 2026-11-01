"""Rookery x herdr bridge — herdr is the CONSOLE, Rookery is the NETWORK.

herdr (herdr.dev) is a terminal workspace manager that RECOGNISES coding agents
inside panes and reports each one's lifecycle state (working / blocked / done /
idle / unknown). That is precisely the information the mailroom cannot know:
Rookery knows what an agent was ASKED, herdr knows what it is DOING right now.

Agents talk through MAIL. This module only observes panes, focuses one for the
human and raises toasts.

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


def enabled() -> bool:
    """Should herdr calls actually run?

    auto  — the herdr binary exists AND we are inside a herdr pane
    force — the binary exists; for a Rookery process living outside a pane
            (launchd, systemd, ssh) that still wants to use the console
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
        "or set ROOKERY_HERDR=force when the Rookery process lives outside one "
        "(launchd/systemd/ssh)."
    )


def require(what: str = "herdr integration") -> None:
    """Fail LOUDLY instead of no-opping.

    The silent degrade is right for optional extras (a notification nobody
    sees costs nothing). It is wrong when the operator explicitly asked for
    herdr, such as `rookery herdr focus`.
    """
    why = reason()
    if why:
        raise SystemExit(f"{what}: {why}")


# --- transport -------------------------------------------------------------
def _run_raw(args: list[str], timeout: float = 10.0):
    """Run a herdr subcommand, returning the CompletedProcess (or None).

    For the rare caller that needs to see WHY it failed — herdr reports a
    machine-readable error code on stderr.
    """
    if not enabled():
        return None
    try:
        return subprocess.run(
            ["herdr", *args], capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None


def _run(args: list[str], timeout: float = 10.0) -> str | None:
    """Run a herdr subcommand; stdout on success, None on ANY failure.

    Never raises. A broken or absent console must not take down a Rookery
    process whose real job is delivering mail.
    """
    r = _run_raw(args, timeout)
    if r is None:
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


def focus(target: str) -> bool:
    """Bring the human to this agent's pane — the jump-to-whoever-is-blocked move."""
    return _run(["agent", "focus", target]) is not None
