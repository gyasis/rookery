#!/usr/bin/env bash
# A2A interop: an external, cross-vendor agent DISCOVERS Rookery via its Agent
# Card, sends a task with JSON-RPC message/send, then polls tasks/get for the
# result. A Rookery node (mock 'concierge') processes it. Free, curl-only client.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="http://127.0.0.1:$PORT"

./cleanup.sh
pkill -f "mailroom_server.py" 2>/dev/null || true
pkill -f "postmaster.py" 2>/dev/null || true
sleep 1
echo

echo "## start the A2A-enabled sidecar + a postmaster managing 'concierge' (mock)"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" --default-node concierge > /tmp/a2a_sidecar.log 2>&1 &
SV=$!
python3 postmaster.py --nodes concierge --engine mock --poll 0.5 > /tmp/a2a_pm.log 2>&1 &
PM=$!
trap 'kill $SV $PM 2>/dev/null || true' EXIT
sleep 2
echo

echo "## 1) DISCOVER — GET /.well-known/agent-card.json (concierge appears as a skill):"
curl -s "$URL/.well-known/agent-card.json" | python3 -m json.tool
echo

echo "## 2) message/send — an external A2A agent sends a task to skill 'concierge':"
RESP=$(curl -s "$URL/a2a" -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"req-1","method":"message/send",
  "params":{"message":{"role":"user","messageId":"m1",
    "parts":[{"kind":"text","text":"Hello Rookery, external A2A agent here — please handle this task."}],
    "metadata":{"recipient":"concierge","from":"ext-a2a-agent"}}}}')
echo "$RESP" | python3 -m json.tool
TASK=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['id'])")
echo "-> task id: $TASK"
echo
sleep 3

echo "## 3) tasks/get — poll for the result (concierge should have replied):"
curl -s "$URL/a2a" -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"req-2\",\"method\":\"tasks/get\",\"params\":{\"id\":\"$TASK\"}}" \
  | python3 -m json.tool
echo

echo "## mailroom view:"
sqlite3 -header -column rookery.db "SELECT id,sender,recipient,substr(body,1,40) body,status FROM inbox ORDER BY id;"
echo
echo "a2a_demo complete."
