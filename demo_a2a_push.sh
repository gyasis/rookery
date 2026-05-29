#!/usr/bin/env bash
# A2A push notifications: instead of polling/streaming, the client gives a
# webhook; the sidecar POSTs the completed Task there when the node replies.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
SINK=9099
URL="http://127.0.0.1:$PORT"

for p in "$PORT" "$SINK"; do
  PID=$(ss -tlnpH 2>/dev/null | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "${PID:-}" ] && kill -9 "$PID" 2>/dev/null || true
done
./cleanup.sh
sleep 1
echo

echo "## start webhook sink (:$SINK), sidecar (:$PORT), postmaster managing 'concierge'"
python3 webhook_sink.py "$SINK" > /tmp/push_sink.log 2>&1 & WS=$!
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" --default-node concierge > /tmp/push_sidecar.log 2>&1 & SV=$!
python3 postmaster.py --nodes concierge --engine mock --poll 0.5 > /tmp/push_pm.log 2>&1 & PM=$!
trap 'kill $WS $SV $PM 2>/dev/null || true' EXIT
sleep 2
echo

echo "## message/send WITH a pushNotificationConfig (webhook) — returns submitted immediately:"
curl -s "$URL/a2a" -H 'Content-Type: application/json' -d "{
  \"jsonrpc\":\"2.0\",\"id\":\"p1\",\"method\":\"message/send\",
  \"params\":{
    \"message\":{\"role\":\"user\",\"messageId\":\"m1\",
      \"parts\":[{\"kind\":\"text\",\"text\":\"do a thing and push me the result\"}],
      \"metadata\":{\"recipient\":\"concierge\",\"from\":\"ext-agent\"}},
    \"configuration\":{\"pushNotificationConfig\":{\"url\":\"http://127.0.0.1:$SINK\"}}
  }}" | python3 -m json.tool
echo
sleep 3

echo "## the webhook received the completed Task (push, no polling):"
cat /tmp/push_sink.log || echo "(nothing received)"
echo
echo "demo_a2a_push complete."
