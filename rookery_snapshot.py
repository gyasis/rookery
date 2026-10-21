#!/usr/bin/env python3
"""rookery_snapshot.py — git-backed snapshot/restore for the rookery mailroom.

Memory-bank pattern (learning 05e21cb4, conf 9): externalize rookery worktree
+ mailroom state to a git branch so it can be restored on another machine
after a crash or for migration.

NOT a message bus. Rookery already runs a durable SQLite mailroom
(pending -> inflight -> done claims, heartbeat requeue, DLQ, per-principal
bearer+ACL auth, TLS, Ed25519 signed cards, HTTP sidecar + federation relay).
Git here is ONLY for crash-recovery snapshots of worktree + DB state.

Usage:
    python rookery_snapshot.py snapshot [--tag TAG] [--message MSG]
    python rookery_snapshot.py restore  [--tag TAG] [--dest DEST]
    python rookery_snapshot.py list

Env:
    ROOKERY_DB                path to mailroom SQLite DB (default: ./rookery.db)
    ROOKERY_SNAPSHOT_BRANCH   branch name (default: snapshot)
    ROOKERY_SNAPSHOT_REMOTE   git remote (default: origin)
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ROOKERY_DB", _HERE / "rookery.db"))
BRANCH = os.environ.get("ROOKERY_SNAPSHOT_BRANCH", "snapshot")
REMOTE = os.environ.get("ROOKERY_SNAPSHOT_REMOTE", "origin")

# Worktree state worth snapshotting (DB sidecars + memory-bank + node config).
SNAPSHOT_PATHS = [
    "rookery.db-wal",
    "rookery.db-shm",
    "memory-bank",
    "mailrooms.json",
    "node_identity.json",
]


def _git(args, cwd=_HERE, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _branch_exists(branch, cwd=_HERE):
    return _git(["rev-parse", "--verify", "--quiet", branch],
                cwd=cwd, check=False).returncode == 0


def _current_branch():
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return r.stdout.strip() or "main"


def _ensure_snapshot_branch():
    """Create the snapshot branch off the current HEAD if it doesn't exist,
    then check it out. Caller's original branch is restored afterwards."""
    if not _branch_exists(BRANCH):
        base = _current_branch()
        _git(["branch", BRANCH, base])
    _git(["checkout", BRANCH])


def _stage_state():
    """Stage worktree state files. The mailroom DB itself is force-added
    (it is often gitignored to avoid merge noise) so the snapshot carries it."""
    staged = 0
    for rel in SNAPSHOT_PATHS:
        p = _HERE / rel
        if p.exists():
            _git(["add", rel], check=False)
            staged += 1
    if DB_PATH.exists():
        try:
            rel_db = DB_PATH.relative_to(_HERE)
        except ValueError:
            rel_db = DB_PATH.name
            shutil.copy2(DB_PATH, _HERE / rel_db)
        _git(["add", "-f", str(rel_db)], check=False)
        staged += 1
    return staged


def snapshot(tag=None, message=None):
    """Commit rookery worktree + mailroom DB state to the snapshot branch."""
    tag = tag or time.strftime("%Y%m%d-%H%M%S")
    msg = message or f"rookery snapshot {tag}"
    orig = _current_branch()
    _ensure_snapshot_branch()
    staged = _stage_state()
    if staged == 0:
        print("nothing to snapshot", file=sys.stderr)
        _git(["checkout", orig], check=False)
        return 1
    r = _git(["status", "--porcelain"], check=False)
    if not r.stdout.strip():
        print(f"snapshot {tag}: no changes since last snapshot")
    else:
        _git(["commit", "-m", msg])
    _git(["tag", "-f", f"snapshot/{tag}"], check=False)
    _git(["checkout", orig], check=False)
    print(f"snapshot {tag} committed on branch {BRANCH}")
    return 0


def restore(tag=None, dest=None):
    """Restore worktree + mailroom DB state from the snapshot branch/tag."""
    ref = f"snapshot/{tag}" if tag else BRANCH
    if not _branch_exists(BRANCH):
        print(f"no snapshot branch {BRANCH} found", file=sys.stderr)
        return 1
    orig = _current_branch()
    _git(["checkout", BRANCH])
    if tag:
        _git(["checkout", ref], check=False)
    target = Path(dest) if dest else _HERE
    target.mkdir(parents=True, exist_ok=True)
    restored = 0
    for rel in SNAPSHOT_PATHS:
        p = _HERE / rel
        if p.exists():
            dest_p = target / rel
            if p.is_dir():
                shutil.copytree(p, dest_p, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dest_p)
            restored += 1
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, target / DB_PATH.name)
        restored += 1
    _git(["checkout", orig], check=False)
    print(f"restored {restored} item(s) from {ref} into {target}")
    return 0


def list_snapshots():
    """List snapshot tags on the snapshot branch, newest first."""
    r = _git(["tag", "-l", "snapshot/*"], check=False)
    tags = [t for t in r.stdout.splitlines() if t.strip()]
    if not tags:
        print("(no snapshots)")
        return 0
    for t in sorted(tags, reverse=True):
        print(t)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="git-backed snapshot/restore for the rookery mailroom"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot")
    sp.add_argument("--tag")
    sp.add_argument("--message", "-m")
    rp = sub.add_parser("restore")
    rp.add_argument("--tag")
    rp.add_argument("--dest")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        sys.exit(snapshot(args.tag, args.message))
    if args.cmd == "restore":
        sys.exit(restore(args.tag, args.dest))
    if args.cmd == "list":
        sys.exit(list_snapshots())


if __name__ == "__main__":
    main()