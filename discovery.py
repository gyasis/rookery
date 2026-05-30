#!/usr/bin/env python3
"""Rookery peer discovery via DNS-SD shell-out.

Avoids the `zeroconf` Python package — shells out to avahi-browse (Linux)
or dns-sd (macOS) instead.  Returns [] gracefully if neither is installed.
"""
import shutil
import subprocess
import sys


def _warn(msg):
    print(f"[discovery] {msg}", file=sys.stderr)


def _parse_avahi_line(line):
    """Parse avahi-browse -trp resolved line starting with '='."""
    if not line.startswith("="):
        return None
    parts = line.split(";")
    if len(parts) < 9:
        return None
    name, hostname, address = parts[3], parts[6], parts[7]
    host = address or hostname
    try:
        port = int(parts[8])
    except ValueError:
        return None
    return {"name": name, "host": host, "port": port} if host and name else None


def _run(cmd, timeout):
    """Run cmd, catch TimeoutExpired and return partial stdout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired as e:
        return (e.stdout or b"").decode(errors="replace")
    except subprocess.SubprocessError:
        return ""


def _discover_avahi(timeout):
    try:
        r = subprocess.run(
            ["avahi-browse", "-trp", "_rookery._tcp"],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        stdout = r.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    return [e for e in (_parse_avahi_line(l) for l in stdout.splitlines()) if e]


def _discover_dns_sd(timeout):
    # Step 1: enumerate instance names
    out = _run(["dns-sd", "-B", "_rookery._tcp", "local."], timeout)
    names = [
        p[-1] for p in (l.split() for l in out.splitlines())
        if len(p) >= 7 and p[1] == "Add"
    ]
    # Step 2: resolve each name
    entries = []
    for name in names:
        out2 = _run(["dns-sd", "-L", name, "_rookery._tcp", "local."], timeout)
        for line in out2.splitlines():
            if "can be reached at" in line:
                try:
                    hostport = line.split("can be reached at")[1].strip().rstrip(".")
                    host, port_str = hostport.rsplit(":", 1)
                    entries.append({"name": name, "host": host.rstrip("."), "port": int(port_str)})
                    break
                except (ValueError, IndexError):
                    pass
    return entries


def discover_mailrooms(timeout: float = 2.0) -> list:
    """Discover Rookery mailrooms advertising _rookery._tcp on the LAN.

    Returns list of {"name": str, "host": str, "port": int} dicts.
    Returns [] if none found or no DNS-SD browser binary is installed.
    """
    if shutil.which("avahi-browse"):
        results = _discover_avahi(timeout)
    elif shutil.which("dns-sd"):
        results = _discover_dns_sd(timeout)
    else:
        _warn("neither avahi-browse nor dns-sd found; peer discovery unavailable")
        return []

    # Deduplicate by (name, host, port)
    seen, out = set(), []
    for e in results:
        key = (e["name"], e["host"], e["port"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


if __name__ == "__main__":
    print(discover_mailrooms())
