#!/usr/bin/env bash
# A2A with AUTH + STREAMING: bearer token required on /a2a; message/stream over
# Server-Sent Events. The agent card stays public (and advertises the scheme).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="http://127.0.0.1:$PORT"
export ROOKERY_TOKEN="rookery-demo-token-$(date +%s)"

./cleanup.sh
pkill -f 'python3 .*mailroom_server.py' 2>/dev/null || true
pkill -f 'python3 .*postmaster.py' 2>/dev/null || true
sleep 1
echo

echo "## sidecar WITH --token + postmaster managing 'concierge' (mock)"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" \
  --default-node concierge --token "$ROOKERY_TOKEN" > /tmp/a2a_sec_sidecar.log 2>&1 &
SV=$!
python3 postmaster.py --nodes concierge --engine mock --poll 0.5 > /tmp/a2a_sec_pm.log 2>&1 &
PM=$!
trap 'kill $SV $PM 2>/dev/null || true' EXIT
sleep 2
echo

echo "## 1) agent card is PUBLIC (no token) and advertises the security scheme:"
curl -s "$URL/.well-known/agent-card.json" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('  securitySchemes:',d.get('securitySchemes'));print('  security:',d.get('security'));print('  capabilities:',d.get('capabilities'))"
echo

echo "## 2) /a2a WITHOUT a token -> rejected:"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" "$URL/a2a" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"x","method":"tasks/get","params":{"id":"a2a-1"}}'
echo

echo "## 3) message/stream WITH the token (live SSE: submitted -> artifact -> completed):"
curl -sN "$URL/a2a" -H "Authorization: Bearer $ROOKERY_TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"s1","method":"message/stream","params":{"message":{"role":"user","messageId":"m1","parts":[{"kind":"text","text":"stream me a reply please"}],"metadata":{"recipient":"concierge","from":"ext-agent"}}}}'
echo
echo "demo_a2a_secure complete."
