#!/usr/bin/env bash
# Rookery `rookery up` — full LAN-style demo on one machine. Proves:
#   1. bootstrap installer downloads + installs the zipapp
#   2. discovery + handshake (with slug) + TOFU pin against signed card
#   3. auto-merge of ~/.claude.json (in a temp HOME so we don't touch the real one)
#   4. mail flows after enrollment
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=9889
URL="http://127.0.0.1:$PORT"

# Hardened policy + admin identity
ID="/tmp/rookery_node_identity_up.json"
ADMIN_TOK="adm-$(openssl rand -hex 12)"
cat > "$ID" <<JSON
{"principals": {"admin": {"token": "$ADMIN_TOK", "may_task": ["*"]}}}
JSON
export ROOKERY_SECURITY="policy_hardened:HardenedPolicy"
export ROOKERY_NODE_IDENTITY="$ID"

# Isolated HOME so ~/.claude.json + ~/.rookery/ are temp
export REAL_HOME="$HOME"
export HOME="/tmp/rookery_up_home_$$"
mkdir -p "$HOME"

# Cleanup
./cleanup.sh
for pid in $(pgrep -f 'mailroom_server.py --host' 2>/dev/null); do
  kill "$pid" 2>/dev/null || true
done
sleep 1
rm -f rookery-card-key.pem
echo

# Sign the card so the TOFU pin path engages
python3 gen_card_key.py >/dev/null
echo "## 0) start signed-card sidecar on $URL"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" \
  --public-url "$URL" --token "$ADMIN_TOK" \
  --card-key rookery-card-key.pem > /tmp/up_sidecar.log 2>&1 &
SV=$!
trap 'kill $SV 2>/dev/null || true; pgrep -f "mailroom_server.py --host" 2>/dev/null | xargs -r kill 2>/dev/null || true; export HOME="$REAL_HOME"; rm -rf /tmp/rookery_up_home_*' EXIT
sleep 1.5
echo

echo "## 1) bootstrap — install ~/.local/bin/rookery via the served installer"
curl -sSL "$URL/bootstrap" | python3 -
ls -la "$HOME/.local/bin/rookery"
echo

echo "## 2) auto-approver — drain the pending queue every 0.5s for the next 30s"
(
  END=$((SECONDS+30))
  while [ $SECONDS -lt $END ]; do
    R=$(curl -s -H "Authorization: Bearer $ADMIN_TOK" "$URL/pending" 2>/dev/null \
        | python3 -c "import sys,json
d=json.load(sys.stdin)
print(d['pending'][0]['request_id']) if d.get('pending') else None" 2>/dev/null)
    if [ -n "$R" ] && [ "$R" != "None" ]; then
      curl -s -X POST "$URL/approve" \
        -H "Authorization: Bearer $ADMIN_TOK" -H "Content-Type: application/json" \
        -d "{\"request_id\":\"$R\",\"decision\":\"approve\"}" > /dev/null
      echo "  [auto-approver] approved $R"
    fi
    sleep 0.5
  done
) &
AA=$!

echo "## 3) rookery up — one command, the whole UX"
"$HOME/.local/bin/rookery" up "$URL" --node-id claude-on-B 2>&1 | sed 's/^/   /'
echo

echo "## 4) verify ~/.claude.json got the entry"
python3 -c "
import json, os
p = os.path.expanduser('~/.claude.json')
d = json.load(open(p))
e = d['mcpServers']['rookery']
print(f'   command  : {e[\"command\"]}')
print(f'   args[0]  : {e[\"args\"][0]}')
print(f'   ROOKERY_NODE  : {e[\"env\"][\"ROOKERY_NODE\"]}')
print(f'   ROOKERY_URL   : {e[\"env\"][\"ROOKERY_URL\"]}')
print(f'   ROOKERY_TOKEN : {e[\"env\"][\"ROOKERY_TOKEN\"][:24]}...')
"
echo

echo "## 5) verify ~/.rookery/known_hosts pinned A's card pubkey"
cat "$HOME/.rookery/known_hosts" | sed 's/^/   /'
echo

echo "## 6) actual mail flow — admin sends, claude-on-B is in the DB"
curl -s -X POST "$URL/send" \
  -H "Authorization: Bearer $ADMIN_TOK" -H "Content-Type: application/json" \
  -d '{"sender":"admin","recipient":"claude-on-B","body":"hello via rookery up"}' | sed 's/^/   /'
echo

sqlite3 -header -column "$DIR/rookery.db" \
  "SELECT id,sender,recipient,substr(body,1,38) body,status FROM inbox ORDER BY id;"

kill $AA 2>/dev/null || true
echo
echo "demo_up complete."
