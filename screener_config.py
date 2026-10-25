"""screener_config -- the `screener` block of the configuration (research D21, D11, D13).

load() returns the block with a default in code for each missing key, so an old
~/.rookery/config.json with no `screener` key still works. check() validates a
loaded block and raises ValueError that names the bad key. set_mode() writes only
screener.checks.<check> in ~/.rookery/config.json. Standard library only.
"""
import copy
import fcntl
import json
import os

import rookery_identity
import screener_t0

CHECKS = ("injection", "duplicate", "budget", "thread", "gates", "t1", "hunch", "t2")
MODES = ("shadow", "live", "off")
REVIEW_MODES = ("pre", "post")

DEFAULTS = {
    "checks": {name: "shadow" for name in CHECKS},
    "bands": {"t1": None},
    "base_url": "http://192.168.0.159:8799/llm/ollama",
    "model": "tev1:4b",
    "timeout_s": 300,
    "hunch": {"model": "qwen2.5:3b", "url": None},
    "t2": {"command": None, "batch": 20},
    "envelope_max_chars": 3000,
    "ask_budget": 20,
    "duplicate_window_s": 3600,
    "review_mode": {"default": "pre", "agents": {}},
    "lease_s": 600,
    "max_age_s": 86400,
}


def _merge(base, over):
    """Return base with over laid on top. Dicts merge key by key."""
    out = copy.deepcopy(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def load() -> dict:
    """Return the screener block with defaults for every missing key. Writes nothing."""
    block = rookery_identity.load_config(persist=False).get("screener")
    if block is None:
        return copy.deepcopy(DEFAULTS)
    if not isinstance(block, dict):
        raise ValueError("screener: must be an object")
    return _merge(DEFAULTS, block)


def _pos_int(cfg_value, key):
    if isinstance(cfg_value, bool) or not isinstance(cfg_value, int) or cfg_value <= 0:
        raise ValueError(f"{key}: must be a positive whole number, got {cfg_value!r}")


def _mode(value, key, allowed):
    if value not in allowed:
        raise ValueError(f"{key}: must be one of {'|'.join(allowed)}, got {value!r}")


def _obj(value, key):
    if not isinstance(value, dict):
        raise ValueError(f"{key}: must be an object, got {value!r}")


def check(cfg) -> None:
    """Raise ValueError that names the key when a value is not valid."""
    for key in ("checks", "bands", "t2", "hunch", "review_mode"):
        _obj(cfg.get(key, {}), f"screener.{key}")
    checks = cfg.get("checks", {})
    for name, mode in checks.items():
        if name not in CHECKS:
            raise ValueError(f"screener.checks.{name}: not a check. Use one of {', '.join(CHECKS)}")
        _mode(mode, f"screener.checks.{name}", MODES)

    band = cfg.get("bands", {}).get("t1")
    if band is not None:
        if not isinstance(band, dict) or set(band) != {"accept", "review"}:
            raise ValueError("screener.bands.t1: must be null or {accept, review}")
        for part in ("accept", "review"):
            val = band[part]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val <= 1:
                raise ValueError(f"screener.bands.t1.{part}: must be a number from 0 to 1, got {val!r}")
        if band["review"] > band["accept"]:
            raise ValueError("screener.bands.t1.review: must not be above screener.bands.t1.accept")

    if checks.get("t1") == "live" and band is None:
        raise ValueError("screener.bands.t1: must be set when screener.checks.t1 is live")

    for key, prefix, val in (("model", "screener:", cfg.get("model")),
                             ("hunch.model", "hunch:vote6:", cfg.get("hunch", {}).get("model"))):
        if not screener_t0.check_decided_by(prefix + str(val)) or not isinstance(val, str):
            raise ValueError(f"screener.{key}: must be a model name (letters, digits and . _ : @ / -), got {val!r}")

    command = cfg.get("t2", {}).get("command")
    if command is not None and (not isinstance(command, list)
                                or not all(isinstance(a, str) for a in command)):
        raise ValueError("screener.t2.command: must be null or a list of text")

    for key, val in (("timeout_s", cfg.get("timeout_s")),
                     ("envelope_max_chars", cfg.get("envelope_max_chars")),
                     ("ask_budget", cfg.get("ask_budget")),
                     ("duplicate_window_s", cfg.get("duplicate_window_s")),
                     ("lease_s", cfg.get("lease_s")),
                     ("max_age_s", cfg.get("max_age_s")),
                     ("t2.batch", cfg.get("t2", {}).get("batch"))):
        _pos_int(val, f"screener.{key}")

    if cfg["lease_s"] < 2 * cfg["timeout_s"]:
        raise ValueError(f"screener.lease_s: must be at least twice screener.timeout_s ({cfg['timeout_s']}), "
                         f"got {cfg['lease_s']}")

    review = cfg.get("review_mode", {})
    _mode(review.get("default"), "screener.review_mode.default", REVIEW_MODES)
    agents = review.get("agents", {})
    _obj(agents, "screener.review_mode.agents")
    for agent, mode in agents.items():
        _mode(mode, f"screener.review_mode.agents.{agent}", REVIEW_MODES)


def set_mode(name, mode) -> None:
    """Set screener.checks.<name> in ~/.rookery/config.json. Keep every other key."""
    if name not in CHECKS:
        raise ValueError(f"screener.checks.{name}: not a check. Use one of {', '.join(CHECKS)}")
    _mode(mode, f"screener.checks.{name}", MODES)
    path = rookery_identity._config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:  # one writer at a time around the read-modify-write
        fcntl.flock(lock, fcntl.LOCK_EX)
        cfg = rookery_identity.load_config()  # creates the file from the default when missing
        _obj(cfg, "config")
        if "screener" in cfg:
            _obj(cfg["screener"], "screener")
        block = cfg.setdefault("screener", {})
        if not isinstance(block.get("checks"), dict):
            block["checks"] = {}
        block["checks"][name] = mode
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(cfg, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
