"""Idempotent merger for ~/.claude.json mcpServers block.

Writes are atomic (tmp + rename). Backups are written ONLY when the content
actually changes. Re-running with identical args is a no-op (no disk I/O).
"""

import json
import os
import time
from pathlib import Path


def merge_mcp_entry(
    name: str,
    command: str,
    args: list[str],
    env: dict[str, str],
    path: str | None = None,
) -> dict:
    """Merge a single mcpServers entry into a claude.json file.

    Args:
        name:    Key name under mcpServers.
        command: Executable command string.
        args:    List of command arguments.
        env:     Environment variables dict.
        path:    Target file path; defaults to ~/.claude.json.

    Returns:
        {"written": bool, "backup": str | None, "path": str}

    Raises:
        ValueError: If the target file exists but contains invalid JSON.
    """
    resolved = Path(path).expanduser() if path else Path.home() / ".claude.json"
    path_str = str(resolved)

    # Step 1: check existence before any reads
    existed = resolved.exists()

    # Step 2: read or start fresh
    if existed:
        raw = resolved.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"refusing to overwrite corrupt JSON at {path_str}")
    else:
        raw = None
        data = {}

    # Step 3: ensure mcpServers key
    data.setdefault("mcpServers", {})

    # Step 4: construct canonical entry
    entry = {"command": command, "args": list(args), "env": dict(env)}

    # Step 5: idempotency check — no disk activity if equal
    if data["mcpServers"].get(name) == entry:
        return {"written": False, "backup": None, "path": path_str}

    # Step 6: write backup of prior content (only when file existed)
    bak_path = None
    if existed:
        bak_path = f"{path_str}.bak.{int(time.time())}"
        bak = Path(bak_path)
        bak.write_text(raw, encoding="utf-8")
        bak.chmod(0o600)

    # Step 7: update entry
    data["mcpServers"][name] = entry
    serialized = json.dumps(data, indent=2, sort_keys=False) + "\n"

    # Step 8: atomic write via tmp + rename
    tmp_path = f"{path_str}.tmp.{os.getpid()}"
    tmp = Path(tmp_path)
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp_path, path_str)

    # Step 9: set 0600 only if we created the file
    if not existed:
        resolved.chmod(0o600)

    # Step 10: return result
    return {"written": True, "backup": bak_path, "path": path_str}
