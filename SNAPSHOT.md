# Rookery Snapshot/Restore

Git-backed crash-recovery and migration layer for the rookery mailroom.

## Why this exists

Rookery already runs a durable SQLite mailroom that handles all inter-agent
messaging — pending→inflight→done claim/ack, heartbeat-based stale requeue,
dead-letter queue, per-principal bearer+ACL auth, TLS, Ed25519 signed cards,
and an HTTP sidecar + federation relay for cross-machine delivery. That bus
does not need replacing.

What rookery did **not** have was a crash-recovery and migration path for the
worktree + mailroom DB state itself. `rookery_snapshot.py` fills that gap using
the memory-bank pattern: externalize state to a git branch so it can be
restored on another machine after a crash or for migration.

This is the only worthwhile git slice for rookery. Git-as-a-message-bus was
evaluated and rejected (learning `05e21cb4`, conf 9) — it loses atomic claim
semantics, adds latency and rate limits, and collapses per-principal auth to
repo push rights.

## Usage

```bash
# Commit current worktree + mailroom DB state to the snapshot branch
python rookery_snapshot.py snapshot [--tag TAG] [--message MSG]

# Restore state from the snapshot branch (or a specific tag) into a dest dir
python rookery_snapshot.py restore [--tag TAG] [--dest DEST]

# List available snapshot tags, newest first
python rookery_snapshot.py list
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ROOKERY_DB` | `./rookery.db` | Path to the mailroom SQLite DB |
| `ROOKERY_SNAPSHOT_BRANCH` | `snapshot` | Branch to commit snapshots to |
| `ROOKERY_SNAPSHOT_REMOTE` | `origin` | Git remote for push/fetch |

## What gets snapshotted

- `rookery.db` (+ `-wal` / `-shm` sidecars) — the mailroom DB, force-added
- `memory-bank/` — shared and private memory-bank state
- `mailrooms.json` — mailroom configuration (if present)
- `node_identity.json` — node identity (if present)

## How it works

1. **snapshot** creates (or reuses) the snapshot branch off the current HEAD,
   stages the worktree state files, commits with a timestamped tag
   (`snapshot/YYYYMMDD-HHMMSS`), then checks out the original branch so the
   caller's working state is undisturbed.
2. **restore** checks out the snapshot branch (or a specific tag), copies the
   state files into the destination directory, then returns to the original
   branch.
3. **list** prints all `snapshot/*` tags, newest first.

## Design notes

- The DB is force-added (`git add -f`) because it is often gitignored to avoid
  merge noise in normal development. Snapshots need it, so the snapshot branch
  carries it explicitly.
- The caller's original branch is always restored after both snapshot and
  restore, so running the tool does not leave you stranded on the snapshot
  branch.
- This is a first slice: `snapshot()` / `restore()` / `list()` + this README.
  Federation-of-last-resort (push snapshots to a remote for cross-machine
  restore) is a follow-up.