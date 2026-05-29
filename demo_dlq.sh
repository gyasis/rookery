#!/usr/bin/env bash
# Relay dead-letter queue: undeliverable mail (unknown mailroom, or known-but-
# unreachable after retries) goes to the DLQ; the sender is alerted; it can be
# requeued. Free (no models).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
DIRECTORY=/tmp/rookery_dlq_dir.json
cat > "$DIRECTORY" <<JSON
{ "home": {"url": "http://127.0.0.1:8765"},
  "mac":  {"url": "http://127.0.0.1:9999"} }
JSON
export ROOKERY_MAILROOMS="$DIRECTORY" ROOKERY_MAILROOM=home

./cleanup.sh
python3 relay.py --poll 0.5 > /tmp/dlq_relay.log 2>&1 &
RL=$!
trap 'kill $RL 2>/dev/null || true' EXIT
sleep 1
echo

echo "## alice -> bob@nowhere   (UNKNOWN mailroom -> immediate dead-letter)"
python3 send_mail.py --to bob@nowhere --from alice --body "are you there?"
echo "## alice -> carol@mac      (KNOWN but unreachable :9999 -> retries, then dead-letter)"
python3 send_mail.py --to carol@mac --from alice --body "ping"
sleep 4
echo

echo "## DLQ contents (relay --list-dlq):"
python3 relay.py --list-dlq
echo
echo "## alice's dead-letter alerts:"
sqlite3 -header -column rookery.db "SELECT sender,substr(body,1,62) AS body FROM inbox WHERE recipient='alice';"
echo
echo "## requeue the first dead letter (status -> pending):"
DID=$(python3 relay.py --list-dlq | grep -oP '^#\K[0-9]+' | head -1 || true)
[ -n "${DID:-}" ] && python3 relay.py --requeue-dlq "$DID"
echo
echo "## relay log:"; grep -iE "DEAD-LETTER|attempt|relayed" /tmp/dlq_relay.log | head
echo "demo_dlq complete."
