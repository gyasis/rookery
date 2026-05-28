#!/usr/bin/env bash
# Press play: un-freeze a node OS-paused by pause.sh (kill -CONT).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${ROOKERY_DB:-$DIR/rookery.db}"
NODE="${1:?usage: resume.sh <node_id>}"
PID="$(sqlite3 "$DB" "SELECT pid FROM nodes WHERE node_id='$NODE'")"
[ -n "$PID" ] || { echo "no pid for node '$NODE'"; exit 1; }
kill -CONT "$PID"
echo "resumed '$NODE' (pid $PID) — continues as if nothing happened."
