#!/usr/bin/env bash
# TLS sidecar: a self-signed cert + a bearer token = auth AND encryption, the
# combo you want for internet federation (or use a tunnel instead — see README).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765
URL="https://127.0.0.1:$PORT"
export ROOKERY_TOKEN="tls-demo-$(date +%s)"

command -v openssl >/dev/null 2>&1 || { echo "openssl not installed — skipping"; exit 0; }
PID=$(ss -tlnpH 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "${PID:-}" ] && kill -9 "$PID" 2>/dev/null || true
./cleanup.sh
./gen_cert.sh 127.0.0.1 >/dev/null
echo

echo "## start HTTPS sidecar with TLS + token"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" \
  --tls-cert rookery-cert.pem --tls-key rookery-key.pem --token "$ROOKERY_TOKEN" \
  > /tmp/tls_sidecar.log 2>&1 &
SV=$!
trap 'kill $SV 2>/dev/null || true' EXIT
sleep 2
echo "## startup:"; grep -i "mailroom" /tmp/tls_sidecar.log
echo

echo "## health over HTTPS (curl -k, self-signed) — public:"
curl -sk "$URL/health"; echo
echo "## /a2a over HTTPS WITHOUT token -> rejected:"
curl -sk -o /dev/null -w "  HTTP %{http_code}\n" "$URL/a2a" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"x","method":"tasks/get","params":{"id":"a2a-1"}}'
echo "## mailctl send over HTTPS WITH token (ROOKERY_INSECURE_TLS for the self-signed cert):"
ROOKERY_INSECURE_TLS=1 python3 mailctl.py send --url "$URL" --to bob --from alice \
  --body "encrypted hello" --token "$ROOKERY_TOKEN"
echo
echo "## For the public internet: a CA-signed cert (Let's Encrypt), or a tunnel —"
echo "   tailscale (sidecar on the tailnet) · ssh -L 8765:localhost:8765 user@host · cloudflared."
echo "demo_tls complete."
