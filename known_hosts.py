"""Rookery known_hosts — SSH-style peer trust store at ~/.rookery/known_hosts.
One line per peer; comment lines start with '#'.

Line format:
    <host_or_url> <pubkey_b64> <label>  # added <iso8601>
"""
import dataclasses
import datetime
import os
import pathlib

_DIR = pathlib.Path.home() / ".rookery"
_FILE = _DIR / "known_hosts"
_BAK = _DIR / "known_hosts.bak"


@dataclasses.dataclass
class Entry:
    host: str
    pubkey: str
    label: str
    added: str  # iso8601


def load() -> list[Entry]:
    """Read ~/.rookery/known_hosts; return [] if the file is missing."""
    if not _FILE.exists():
        return []
    entries = []
    for raw in _FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Drop trailing "# added <ts>" comment before parsing
        if " # " in line:
            line = line[: line.index(" # ")]
        parts = line.split()
        if len(parts) < 3:
            continue
        entries.append(Entry(host=parts[0], pubkey=parts[1], label=parts[2], added=""))
    return entries


def add(host: str, pubkey: str, label: str) -> Entry:
    """Append a new entry, writing atomically with a .bak of the old file."""
    os.makedirs(_DIR, mode=0o700, exist_ok=True)
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds")
    entry = Entry(host=host, pubkey=pubkey, label=label, added=ts)
    line = f"{host} {pubkey} {label}  # added {ts}\n"

    tmp = _DIR / f"known_hosts.tmp.{os.getpid()}"
    try:
        # Preserve existing file as .bak before overwriting
        if _FILE.exists():
            _BAK.write_bytes(_FILE.read_bytes())
            os.chmod(_BAK, 0o600)
            existing = _FILE.read_text()
        else:
            existing = ""

        tmp.write_text(existing + line)
        os.chmod(tmp, 0o600)
        os.rename(tmp, _FILE)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    return entry


def check(host: str, pubkey: str) -> str:
    """Return 'ok', 'changed', or 'unknown' for the given host/pubkey pair."""
    for entry in load():
        if entry.host == host:
            return "ok" if entry.pubkey == pubkey else "changed"
    return "unknown"
