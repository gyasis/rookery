#!/usr/bin/env bash
# The literal "video pause button": OS-freeze a node by pid (kill -STOP).
# Frozen = 0 CPU, RAM held, resumes exactly where it left off on resume.sh.
# (For a durable pause that survives reboot + costs $0, use the NEEDCRED flow
#  instead — the node's state lives in the mailroom DB. See README.)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${ROOKERY_DB:-$DIR/rookery.db}"
NODE="${1:?usage: pause.sh <node_id>}"
PID="$(sqlite3 "$DB" "SELECT pid FROM nodes WHERE node_id='$NODE'")"
[ -n "$PID" ] || { echo "no pid for node '$NODE'"; exit 1; }
kill -STOP "$PID"
echo "paused '$NODE' (pid $PID) — frozen. resume.sh $NODE to continue."
