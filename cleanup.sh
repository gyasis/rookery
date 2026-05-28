#!/usr/bin/env bash
# Reset the mailroom between runs.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${ROOKERY_DB:-$DIR/rookery.db}"
rm -f "$DB" "$DB-wal" "$DB-shm"
echo "mailroom reset ($DB)"
