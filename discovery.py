#!/usr/bin/env python3
"""Rookery peer discovery via DNS-SD shell-out.

Avoids the `zeroconf` Python package — shells out to avahi-browse (Linux)
or dns-sd (macOS) instead.  Returns [] gracefully if neither is installed.
Also provides a UDP-broadcast fallback (announce_loop / listen) for
environments where mDNS / avahi / dns-sd are unavailable or blocked.
"""

import json
import shutil
import socket
import subprocess
import sys
import threading
import time


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
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        stdout = r.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    return [e for e in (_parse_avahi_line(line) for line in stdout.splitlines()) if e]


def _discover_dns_sd(timeout):
    # Step 1: enumerate instance names
    out = _run(["dns-sd", "-B", "_rookery._tcp", "local."], timeout)
    names = [
        p[-1]
        for p in (line.split() for line in out.splitlines())
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
                    entries.append(
                        {"name": name, "host": host.rstrip("."), "port": int(port_str)}
                    )
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
    elif sys.platform == "win32" and shutil.which("dns-sd.exe"):
        # Bonjour Print Services on Windows: same protocol as macOS dns-sd.
        results = _discover_dns_sd(timeout)  # reuse the macOS parser
    else:
        _warn(
            "neither avahi-browse nor dns-sd found; LAN mDNS discovery unavailable. "
            "Windows: install Bonjour Print Services (apple.com, free) to enable. "
            "UDP broadcast fallback still works via discovery.listen()."
        )
        return []

    # Deduplicate by (name, host, port)
    seen, out = set(), []
    for e in results:
        key = (e["name"], e["host"], e["port"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


_BROADCAST_ADDR = "255.255.255.255"
_BROADCAST_PORT = 8888


def announce_loop(
    port: int = 8765,
    name: str = "mailroom",
    pubkey_b64: str | None = None,
    interval: float = 5.0,
    stop_event: threading.Event | None = None,
) -> None:
    """Broadcast a JSON presence datagram every *interval* seconds.

    Sends ``{"name": name, "port": port, "pubkey_b64": pubkey_b64}`` to
    ``255.255.255.255:8888`` via UDP broadcast.  Runs until *stop_event*
    is set (or forever if *stop_event* is None).  Never raises — socket
    errors are logged to stderr and the loop retries after *interval*.
    """
    payload = json.dumps(
        {"name": name, "port": port, "pubkey_b64": pubkey_b64}
    ).encode()
    while stop_event is None or not stop_event.is_set():
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(payload, (_BROADCAST_ADDR, _BROADCAST_PORT))
        except OSError as exc:
            _warn(f"announce_loop broadcast error: {exc}")
        finally:
            if sock is not None:
                sock.close()
        # Sleep in short increments so stop_event is noticed promptly.
        deadline = time.monotonic() + interval
        while (
            stop_event is None or not stop_event.is_set()
        ) and time.monotonic() < deadline:
            time.sleep(0.05)


def listen(timeout: float = 2.0) -> list:
    """Listen for UDP broadcast announcements for up to *timeout* seconds.

    Works on Windows (UDP broadcast is platform-portable in stdlib ``socket``).

    Binds to ``("", 8888)`` with ``SO_REUSEADDR`` (and ``SO_REUSEPORT``
    where supported) so announce + listen can coexist on the same host.

    Returns a deduplicated list of dicts::

        [{"name": str, "host": str, "port": int, "pubkey_b64": str|None}]

    Stray (non-JSON or wrong-shape) datagrams are silently skipped.
    The socket is always closed on exit.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass  # SO_REUSEPORT not supported on this platform
        sock.bind(("", _BROADCAST_PORT))
        sock.settimeout(min(timeout, 0.2))  # short reads so we can honour total timeout

        seen: set = set()
        results: list = []
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                msg = json.loads(data.decode())
                entry = {
                    "name": str(msg["name"]),
                    "host": addr[0],
                    "port": int(msg["port"]),
                    "pubkey_b64": msg.get("pubkey_b64"),
                }
            except (KeyError, ValueError, UnicodeDecodeError):
                continue  # stray datagram — skip silently

            key = (entry["name"], entry["host"], entry["port"])
            if key not in seen:
                seen.add(key)
                results.append(entry)

        return results
    finally:
        sock.close()


if __name__ == "__main__":
    print(discover_mailrooms())
