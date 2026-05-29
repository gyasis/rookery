#!/usr/bin/env bash
# Hardened security policy: PER-NODE identity (each principal has its own bearer
# token) + per-target authorization (an ACL of which nodes each may task).
# Swapped in via ROOKERY_SECURITY — the mesh is otherwise unchanged. Free (mock).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="http://127.0.0.1:$PORT"

# Per-node identity registry (real secrets; lives in /tmp, never committed).
ID="/tmp/rookery_node_identity.json"
TA="arch-$(openssl rand -hex 12)"     # architect token
TG="guest-$(openssl rand -hex 12)"    # guest token
cat > "$ID" <<JSON
{"principals": {
  "architect": {"token": "$TA", "may_task": ["researcher", "reviewer"]},
  "guest":     {"token": "$TG", "may_task": ["researcher"]}
}}
JSON
export ROOKERY_SECURITY="policy_hardened:HardenedPolicy"
export ROOKERY_NODE_IDENTITY="$ID"

./cleanup.sh
# kill leftovers by SPECIFIC argv (won't self-match the wrapper shell)
for pid in $(pgrep -f 'mailroom_server.py --host' 2>/dev/null) $(pgrep -f 'postmaster.py --nodes' 2>/dev/null); do
  kill "$pid" 2>/dev/null || true
done
sleep 1
echo

echo "## hardened sidecar + postmaster managing researcher,reviewer (mock)"
echo "   architect may task: researcher, reviewer   |   guest may task: researcher only"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" \
  --default-node researcher > /tmp/hardened_sidecar.log 2>&1 &
SV=$!
python3 postmaster.py --nodes researcher,reviewer --engine mock --poll 0.5 > /tmp/hardened_pm.log 2>&1 &
PM=$!
trap 'kill $SV $PM 2>/dev/null || true' EXIT
sleep 2
echo

send() {  # send() <token> <recipient> -> prints HTTP + JSON-RPC result/error
  curl -s "$URL/a2a" -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":\"r\",\"method\":\"message/send\",\"params\":{\"message\":{\"role\":\"user\",\"messageId\":\"m\",\"parts\":[{\"kind\":\"text\",\"text\":\"please look into X\"}],\"metadata\":{\"recipient\":\"$2\",\"from\":\"ext\"}}}}" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('   ', 'OK ->'+d['result']['status']['state'] if 'result' in d else 'DENIED -> '+d['error']['message'])"
}

echo "## 1) card is public and advertises bearerAuth:"
curl -s "$URL/.well-known/agent-card.json" | python3 -c "import sys,json;print('   ', json.load(sys.stdin).get('securitySchemes'))"
echo
echo "## 2) NO token -> 401 (unknown caller has no identity):"
curl -s -o /dev/null -w "    HTTP %{http_code}\n" "$URL/a2a" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"x","method":"tasks/get","params":{"id":"a2a-1"}}'
echo
echo "## 3) GUEST token -> task researcher (within ACL):"; send "$TG" researcher
echo "## 4) GUEST token -> task reviewer (OUTSIDE ACL -> denied, principal named):"; send "$TG" reviewer
echo "## 5) ARCHITECT token -> task reviewer (within ACL):"; send "$TA" reviewer
echo
echo "demo_hardened complete."
