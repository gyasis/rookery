#!/usr/bin/env bash
# Prove the warm SDK session kills cold-start: one-time open, then fast turns.
# Sends two messages on the SAME warm session and reports each turn's time.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
LOG=/tmp/sdk_node.log

./cleanup.sh
: > "$LOG"
python3 sdk_node.py --node-id partner --idle-timeout 25 --poll 1 > "$LOG" 2>&1 &
SDK=$!
trap 'kill $SDK 2>/dev/null || true' EXIT

wait_for() { for _ in $(seq 1 100); do grep -q "$1" "$LOG" && return 0; sleep 2; done; return 1; }

echo "## waiting for the warm session to open…"
wait_for "warm session ready" || { echo "session never opened"; tail -5 "$LOG"; exit 1; }

echo "## turn 1: send a message"
python3 send_mail.py --to partner --from human --body "Reply briefly: say hello."
wait_for "turn #1 done" || echo "(turn 1 not seen)"

echo "## turn 2: send another (same warm session)"
python3 send_mail.py --to partner --from human --body "Reply briefly: now say goodbye."
wait_for "turn #2 done" || echo "(turn 2 not seen)"

echo
echo "=== timings ==="
grep -E "warm session ready|turn #" "$LOG"
echo
echo "test_sdk_latency complete."
