#!/usr/bin/env bash
# Multi-host transport proof (loopback): a node that talks ONLY over HTTP to the
# mailroom sidecar processes mail injected via the local DB. Same path a node on
# the Mac Studio would take — just a different --url. Free, no models.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"
PORT=8765

./cleanup.sh
echo

echo "## starting the mailroom sidecar on 127.0.0.1:$PORT"
python3 mailroom_server.py --host 127.0.0.1 --port "$PORT" > /tmp/rk_sidecar.log 2>&1 &
SV=$!
trap 'kill $SV ${LP:-} 2>/dev/null || true' EXIT
sleep 1
curl -s "http://127.0.0.1:$PORT/health"; echo
echo

echo "## starting a REMOTE node over HTTP (stands in for the Mac): remote1"
python3 mailctl.py loop --url "http://127.0.0.1:$PORT" --node remote1 --poll 0.5 > /tmp/rk_remote.log 2>&1 &
LP=$!
sleep 1
echo

echo "## local send (DIRECT to the DB) addressed to remote1 — must arrive over HTTP"
python3 send_mail.py --to remote1 --from human --body "hello across the wire" >/dev/null
sleep 3
echo

echo "## mailroom state (remote1 should have a 'done' inbound + an ACK back to human):"
sqlite3 -header -column rookery.db "SELECT id,sender,recipient,substr(body,1,42) body,status FROM inbox ORDER BY id;"
echo
echo "## remote node log (it only ever spoke HTTP):"
cat /tmp/rk_remote.log
echo
echo "demo_multihost (loopback) complete."
echo "## For a real LAN run: copy mailctl.py to the peer and run:"
echo "   python3 mailctl.py loop --url http://<linux-host>:$PORT --node macbot"
echo "   (replace <linux-host> with the DB host's LAN IP; sidecar must bind --host 0.0.0.0)"
