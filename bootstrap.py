#!/usr/bin/env python3
"""Rookery bootstrap — builds a self-contained zipapp and generates the
one-liner installer script that peers run to get `rookery` onto PATH.

Two public functions:
  build_pyz(output_path)       -> str   build the zipapp; return output_path
  installer_script(public_url) -> str   return a Python installer script (as a
                                        string) suitable for `curl ... | python3 -`
"""

import os
import shutil
import stat
import tempfile
import zipapp

# ---------------------------------------------------------------------------
# Source files to bundle (relative to this file's directory).
# All are peer-side modules that a fresh machine needs.
# ---------------------------------------------------------------------------
_ROOKERY_DIR = os.path.dirname(os.path.abspath(__file__))

_PY_MODULES = [
    "rookery_cli.py",
    "cli_serve.py",
    "cli_up.py",
    "cli_approve.py",
    "cli_known_hosts.py",
    "identity.py",
    "known_hosts.py",
    "discovery.py",
    "config_manager.py",
    "mailctl.py",
    "rookery_mcp.py",
    "rookery.py",
    "security.py",
    "handshake.py",
    "notifier.py",
    # host-side modules — so the SAME installed app can `serve`, not just join.
    "mailroom_server.py",
    "mailroom_handshake.py",
    "mailroom_bootstrap.py",
    "bootstrap.py",
    "policy_hardened.py",
    "policy_example.py",
]

_DATA_FILES = [
    "schema.sql",
]

_MAIN_PY = """\
# NOTE: this file must stay parseable by Python 3.6+. The interpreter check
# below runs BEFORE importing any rookery module, because those modules use
# 3.10+ syntax (e.g. `str | None`) and would raise TypeError/SyntaxError at
# import time on an older interpreter. The shebang is `/usr/bin/env python3`
# for portability, but on many machines that is an older system/conda Python,
# so re-exec under a newer one when we find it.
import os
import sys

_MIN = (3, 10)

if sys.version_info < _MIN and not os.environ.get("ROOKERY_NO_REEXEC"):
    import shutil
    import subprocess

    _archive = os.path.dirname(os.path.abspath(__file__))
    # Probe: version AND whether `cryptography` (identity.py's only non-stdlib
    # dependency) is importable. A 3.10+ interpreter without it gets us past the
    # syntax barrier only to fail on `import identity`, so prefer one with both
    # and fall back to bare-version only if nothing else is available.
    # Single line on purpose: this source is itself embedded in a Python string
    # literal by bootstrap.py, so any backslash escape here needs double-escaping.
    _probe = ("import sys, importlib.util as u; "
              "print(sys.version_info[0], sys.version_info[1], "
              "1 if u.find_spec('cryptography') else 0)")
    _with_crypto = []
    _fallback = []
    # A rookery-managed venv, if one exists, wins over anything on PATH: it is
    # the only interpreter we can guarantee has cryptography, since PEP 668
    # blocks `pip install --user` against most system Pythons.
    _venv = os.path.expanduser("~/.local/share/rookery-venv/bin/python")
    for _cand in (_venv, "python3.14", "python3.13", "python3.12", "python3.11",
                  "python3.10", "python3"):
        _exe = _cand if os.path.isabs(_cand) else shutil.which(_cand)
        if not _exe or not os.path.exists(_exe):
            continue
        try:
            _out = subprocess.run(
                [_exe, "-c", _probe], capture_output=True, text=True, timeout=15,
            ).stdout.split()
            _major, _minor, _crypto = int(_out[0]), int(_out[1]), int(_out[2])
        except Exception:
            continue
        if (_major, _minor) < _MIN:
            continue
        (_with_crypto if _crypto else _fallback).append(_exe)

    for _exe in _with_crypto + _fallback:
        os.environ["ROOKERY_NO_REEXEC"] = "1"       # belt and braces against a loop
        os.execv(_exe, [_exe, _archive] + sys.argv[1:])

    sys.exit(
        "rookery needs Python %d.%d+, but this is %d.%d and no newer "
        "interpreter was found on PATH.\\n"
        "Create a managed environment (picked up automatically next run):\\n"
        "  python3.12 -m venv ~/.local/share/rookery-venv\\n"
        "  ~/.local/share/rookery-venv/bin/pip install cryptography\\n"
        % (_MIN[0], _MIN[1], sys.version_info[0], sys.version_info[1])
    )

import rookery_cli
rookery_cli.main()
"""


def build_pyz(output_path: str) -> str:
    """Stage peer modules into a temp dir, then zip them into a
    self-contained executable at *output_path*.  Returns *output_path*.
    """
    stage = tempfile.mkdtemp(prefix="rookery_pyz_")
    try:
        # Copy Python modules
        for fname in _PY_MODULES:
            src = os.path.join(_ROOKERY_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(stage, fname))

        # Copy data files
        for fname in _DATA_FILES:
            src = os.path.join(_ROOKERY_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(stage, fname))

        # Write __main__.py so `python3 rookery.pyz` dispatches to the CLI
        with open(os.path.join(stage, "__main__.py"), "w") as fh:
            fh.write(_MAIN_PY)

        # Build the zipapp
        zipapp.create_archive(
            stage,
            target=output_path,
            interpreter="/usr/bin/env python3",
        )

        # Make it executable
        os.chmod(
            output_path,
            stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
        )  # 0755

    finally:
        shutil.rmtree(stage, ignore_errors=True)

    return output_path


def installer_script(public_url: str) -> str:
    """Return a Python script (as a string) that downloads the rookery zipapp
    from *public_url*/bootstrap?pyz=1 and installs it at ~/.local/bin/rookery.

    The returned string is intended to be piped into `python3 -`:
        curl -sSL http://A:8765/bootstrap | python3 -
    """
    return f"""\
#!/usr/bin/env python3
# Rookery one-liner installer — generated by bootstrap.installer_script()
# Usage:  curl -sSL {public_url}/bootstrap | python3 -
import os
import stat
import urllib.request

DEST = os.path.expanduser("~/.local/bin/rookery")
PYZ_URL = "{public_url}/bootstrap?pyz=1"

os.makedirs(os.path.dirname(DEST), exist_ok=True)
print(f"Downloading rookery from {{PYZ_URL}} ...")
urllib.request.urlretrieve(PYZ_URL, DEST)
os.chmod(DEST, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
print(f"OK rookery installed at {{DEST}}  (run: rookery up <url>)")
"""
