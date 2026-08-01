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
# NOTE: this file must stay parseable by Python 3.6+. The runtime check below
# runs BEFORE importing any rookery module, because those modules use 3.10+
# syntax (e.g. `str | None`) and would raise SyntaxError/TypeError at import
# time on an older interpreter. The shebang is `/usr/bin/env python3` for
# portability -- peers download this exact file -- but on many machines that
# is an older system/conda Python, so re-exec under a suitable one.
#
# "Suitable" means BOTH 3.10+ AND `cryptography` (identity.py's only non-stdlib
# dependency). Checking only the version trades a SyntaxError for a
# ModuleNotFoundError. If nothing on the box satisfies both, provision a
# managed venv and use that -- PEP 668 blocks `pip install --user` on most
# system Pythons, so a venv is the only reliable target.
import os
import sys

_MIN = (3, 10)
_VENV = os.path.expanduser("~/.local/share/rookery-venv")
_VENV_PY = os.path.join(_VENV, "bin", "python")


def _runtime_ok():
    if sys.version_info < _MIN:
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("cryptography") is not None
    except Exception:
        return False


if not _runtime_ok() and not os.environ.get("ROOKERY_NO_REEXEC"):
    import shutil
    import subprocess

    _archive = os.path.dirname(os.path.abspath(__file__))
    # Single line on purpose: this source is embedded in a Python string literal
    # by bootstrap.py, so any backslash escape here needs double-escaping.
    _probe = ("import sys, importlib.util as u; "
              "print(sys.version_info[0], sys.version_info[1], "
              "1 if u.find_spec('cryptography') else 0)")

    def _inspect(exe):
        try:
            out = subprocess.run(
                [exe, "-c", _probe], capture_output=True, text=True, timeout=15,
            ).stdout.split()
            return (int(out[0]), int(out[1])), bool(int(out[2]))
        except Exception:
            return None, False

    def _reexec(exe):
        os.environ["ROOKERY_NO_REEXEC"] = "1"   # belt and braces against a loop
        os.execv(exe, [exe, _archive] + sys.argv[1:])

    _usable = []
    for _cand in (_VENV_PY, "python3.14", "python3.13", "python3.12",
                  "python3.11", "python3.10", "python3"):
        _exe = _cand if os.path.isabs(_cand) else shutil.which(_cand)
        if not _exe or not os.path.exists(_exe):
            continue
        _ver, _has_crypto = _inspect(_exe)
        if _ver is None or _ver < _MIN:
            continue
        if _has_crypto:
            _reexec(_exe)                        # ready to go
        _usable.append(_exe)

    # Nothing had cryptography. Build the managed venv from the best 3.10+
    # interpreter we did find, then hand off to it.
    if _usable:
        _builder = _usable[0]
        sys.stderr.write("rookery: provisioning runtime at %s ...\\n" % _VENV)
        try:
            if not os.path.exists(_VENV_PY):
                subprocess.run([_builder, "-m", "venv", _VENV], check=True, timeout=180)
            subprocess.run(
                [_VENV_PY, "-m", "pip", "install", "--quiet", "--upgrade",
                 "pip", "cryptography"],
                check=True, timeout=600,
            )
        except Exception as exc:
            sys.exit("rookery: could not provision %s (%s)" % (_VENV, exc))
        _ver, _has_crypto = _inspect(_VENV_PY)
        if _has_crypto:
            sys.stderr.write("rookery: runtime ready\\n")
            _reexec(_VENV_PY)
        sys.exit("rookery: provisioned %s but cryptography is still missing" % _VENV)

    sys.exit(
        "rookery needs Python %d.%d+, but this is %d.%d and no newer "
        "interpreter was found on PATH. Install one, e.g. "
        "`brew install python@3.12`, then re-run -- the runtime is "
        "provisioned automatically." % (_MIN[0], _MIN[1],
                                        sys.version_info[0], sys.version_info[1])
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
