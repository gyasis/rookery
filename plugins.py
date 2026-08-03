"""Rookery plugin registry — how OPTIONAL integrations attach to a standalone core.

Rookery is a messenger. It has to build, run and pass its suite with no plugin
present, no plugin installed, and no knowledge of what any plugin does. So the
dependency only ever points ONE way:

    core  <--imports--  plugin        (never the reverse)

Core never imports a plugin by name. Plugins import core and attach themselves
here. Deleting every plugin file leaves a working messenger.

Three extension points, deliberately few:

    injector(name, send, check=None)  a way to type into a pane
                                      send(target, text) -> bool
                                      check() raises SystemExit with a diagnostic
                                      when the operator named this injector but
                                      it cannot currently work
    sink(fn)                          somewhere to raise an alert
                                      fn(event, title, body, **meta) -> bool
    command(fn)                       extra `rookery <...>` subcommands
                                      fn(subparsers) -> None

Plugins are DISCOVERED, not named: any `plugin_*` module or package beside this
file is tried. Loading is best-effort and quiet — one that is broken, or whose
backing tool is not installed, leaves core exactly as it was. Set
ROOKERY_PLUGINS_DEBUG=1 to see why one did not attach, ROOKERY_PLUGINS=a,b to
pin an explicit list, or ROOKERY_PLUGINS= (empty) to disable all of them.

Stdlib only, like everything else here.
"""
import importlib
import os
import pkgutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PREFIX = "plugin_"

_INJECTORS: dict[str, dict] = {}
_SINKS: list = []
_COMMANDS: list = []
_loaded = False


# --- registration (called BY plugins) --------------------------------------
def injector(name: str, send, check=None) -> None:
    """Contribute a pane-injection mechanism under `name`."""
    _INJECTORS[name] = {"send": send, "check": check}


def sink(fn) -> None:
    """Contribute a notification sink."""
    _SINKS.append(fn)


def command(fn) -> None:
    """Contribute `rookery` subcommands; fn(subparsers) is called at parse time."""
    _COMMANDS.append(fn)


# --- loading (called BY core) ----------------------------------------------
def discover() -> list[str]:
    """Every `plugin_*` module sitting beside this file.

    Core therefore names no plugin anywhere: adding one is dropping a file or
    package in, removing one is deleting it. Works inside a zipapp too, since
    pkgutil goes through the same import hooks.
    """
    try:
        return sorted(
            m.name
            for m in pkgutil.iter_modules([_HERE])
            if m.name.startswith(PLUGIN_PREFIX)
        )
    except Exception:
        return []


def load(names: str | None = None) -> None:
    """Import and register the available plugins. Idempotent and never raises.

    Resolution order: an explicit `names` argument, else $ROOKERY_PLUGINS (set
    but empty disables everything), else discovery.
    """
    global _loaded
    if _loaded and names is None:
        return
    _loaded = True
    env = os.environ.get("ROOKERY_PLUGINS")
    if names is not None:
        mods = [m.strip() for m in names.split(",") if m.strip()]
    elif env is not None:
        mods = [m.strip() for m in env.split(",") if m.strip()]
    else:
        mods = discover()
    debug = os.environ.get("ROOKERY_PLUGINS_DEBUG") == "1"
    for mod_name in mods:
        try:
            mod = importlib.import_module(mod_name)
            reg = getattr(mod, "register", None)
            if reg is None:
                raise AttributeError(f"{mod_name} has no register()")
            reg()
            if debug:
                print(f"[plugins] {mod_name} attached", file=sys.stderr)
        except Exception as e:
            # A broken optional plugin must never take down the messenger.
            if debug:
                print(f"[plugins] {mod_name} skipped: {e}", file=sys.stderr)


# --- use (called BY core) --------------------------------------------------
def injectors() -> list[str]:
    """Names of every plugin-contributed injector."""
    load()
    return sorted(_INJECTORS)


def get_injector(name: str) -> dict | None:
    load()
    return _INJECTORS.get(name)


def notify(event: str, title: str, body: str | None = None, **meta) -> int:
    """Raise an alert through every registered sink. Returns how many fired.

    `event` is a short kind ('needcred', 'mail') a sink may use to pick a
    presentation. Zero sinks — the standalone case — returns 0 and is normal,
    so callers must not log "notified" without checking.
    """
    load()
    fired = 0
    for fn in _SINKS:
        try:
            if fn(event, title, body, **meta):
                fired += 1
        except Exception:
            continue
    return fired


def add_commands(subparsers) -> None:
    """Let plugins contribute their own `rookery` subcommands."""
    load()
    for fn in _COMMANDS:
        try:
            fn(subparsers)
        except Exception:
            continue
