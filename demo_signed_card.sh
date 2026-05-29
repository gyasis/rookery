#!/usr/bin/env bash
# A2A signed agent cards: the sidecar Ed25519-signs its agent card; consumers
# verify it. A tampered card fails verification. Free (no models).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="http://127.0.0.1:$PORT"

PID=$(ss -tlnpH 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "${PID:-}" ] && kill -9 "$PID" 2>/dev/null || true
./cleanup.sh
python3 gen_card_key.py
echo

echo "## sidecar serving a SIGNED agent card"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" --public-url "$URL" \
  --card-key rookery-card-key.pem > /tmp/sign_sidecar.log 2>&1 &
SV=$!
trap 'kill $SV 2>/dev/null || true' EXIT
sleep 1.5
echo

echo "## the card now carries an Ed25519 signature:"
curl -s "$URL/.well-known/agent-card.json" | python3 -c "import sys,json;s=json.load(sys.stdin)['signature'];print('  alg:',s['alg']);print('  publicKey:',s['publicKey'][:28]+'…');print('  value:',s['value'][:28]+'…')"
echo
echo "## verify the untampered card:"
python3 verify_card.py "$URL"
echo
echo "## verify a TAMPERED card (flip 'name') -> must be INVALID:"
curl -s "$URL/.well-known/agent-card.json" | python3 -c "
import sys, json, security
c = json.load(sys.stdin); c['name'] = 'Evil Mesh'
ok, info = security.verify_card(c)
print(f\"  tampered card signature: {'VALID' if ok else 'INVALID'}  ({info})\")"
echo
echo "demo_signed_card complete."
