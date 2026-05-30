#!/usr/bin/env bash
# Invite / handshake bootstrap: A mints a per-node invite (URL + fresh token +
# pinned card pubkey + TTL); B consumes it and joins the mesh — no hand-edits.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="http://127.0.0.1:$PORT"

# Hardened policy with an "admin" principal authorised to mint invites.
ID="/tmp/rookery_node_identity_invite.json"
ADMIN_TOK="adm-$(openssl rand -hex 12)"
cat > "$ID" <<JSON
{"principals": {
  "admin": {"token": "$ADMIN_TOK", "may_task": ["*"]}
}}
JSON
export ROOKERY_SECURITY="policy_hardened:HardenedPolicy"
export ROOKERY_NODE_IDENTITY="$ID"

./cleanup.sh
for pid in $(pgrep -f 'mailroom_server.py --host' 2>/dev/null) $(pgrep -f 'mailctl.py loop' 2>/dev/null); do
  kill "$pid" 2>/dev/null || true
done
sleep 1
echo

# 1. Sidecar signs its agent card so the invite can pin its public key.
python3 gen_card_key.py >/dev/null
echo "## sidecar — hardened policy + signed agent card"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" \
  --card-key rookery-card-key.pem > /tmp/inv_sidecar.log 2>&1 &
SV=$!
trap 'kill $SV 2>/dev/null || true; for p in $(pgrep -f "mailctl.py loop" 2>/dev/null); do kill "$p" 2>/dev/null || true; done' EXIT
sleep 1.5
echo

# 2. A mints an invite for a brand-new node "claude-on-B".
echo "## A mints an invite for 'claude-on-B' (admin token authenticates the request)"
curl -s -X POST "$URL/invite" \
  -H "Authorization: Bearer $ADMIN_TOK" \
  -H "Content-Type: application/json" \
  -d '{"node_id":"claude-on-B","may_task":["*"],"ttl_minutes":30}' > /tmp/invite.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/invite.json'))
print(f"   url        : {d['url']}")
print(f"   node_id    : {d['node_id']}")
print(f"   token      : {d['token'][:24]}...   (fresh, per-node)")
print(f"   card_pubkey: {(d['card_pubkey'] or '')[:24]}...   (pinned from A's signed card)")
print(f"   expires_at : {d['expires_at']}")
PY
echo

# 3. B verifies the invite — pins the served card's pubkey to the one in the invite.
echo "## B verifies the invite (pin check + Ed25519 signature)"
python3 rookery_join.py /tmp/invite.json --mode verify
echo

# 4. B prints the ~/.claude.json snippet (what you'd paste into the Claude Code config).
echo "## B prints the MCP snippet for ~/.claude.json:"
python3 rookery_join.py /tmp/invite.json --mode mcp | sed 's/^/   /'
echo

# 5. Negative: a tampered invite (flipped pubkey) must FAIL verification.
echo "## B refuses a tampered invite (pubkey flipped)"
python3 - <<'PY'
import json, base64
inv = json.load(open('/tmp/invite.json'))
pk = inv['card_pubkey']
inv['card_pubkey'] = base64.b64encode(b'\x00' * 32).decode()
json.dump(inv, open('/tmp/invite_bad.json', 'w'))
PY
python3 rookery_join.py /tmp/invite_bad.json --mode verify || echo "   (exit 1 as expected)"
echo

# 6. B joins as a live mailctl loop using the invite. Background.
echo "## B starts a mailctl loop using the invite (background)"
python3 rookery_join.py /tmp/invite.json --mode loop > /tmp/inv_loop.log 2>&1 &
sleep 2

# 7. A sends mail to claude-on-B over the sidecar. Loop should pull it.
echo "## A sends mail to claude-on-B"
curl -s -X POST "$URL/send" \
  -H "Authorization: Bearer $ADMIN_TOK" \
  -H "Content-Type: application/json" \
  -d '{"sender":"admin","recipient":"claude-on-B","body":"hello via invite handshake"}' | sed 's/^/   /'
echo; echo
sleep 2

echo "## inbox state (the message should be done/inflight after the loop drained it):"
sqlite3 -header -column rookery.db \
  "SELECT id,sender,recipient,substr(body,1,42) body,status FROM inbox ORDER BY id;"

echo
echo "demo_invite complete."
