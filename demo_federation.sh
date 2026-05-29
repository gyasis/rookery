#!/usr/bin/env bash
# Federation: TWO mailrooms (home + mac) on localhost, each with its own DB,
# sidecar, and relay. A message from alice@home to bob@mac is relayed across,
# bob replies, and the reply is relayed back to alice@home. Free (mock).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
HOME_DB=/tmp/rookery_home.db
MAC_DB=/tmp/rookery_mac.db
DIRECTORY=/tmp/rookery_mailrooms.json

# free ports
for p in 8765 8766; do
  PID=$(ss -tlnpH 2>/dev/null | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "${PID:-}" ] && kill -9 "$PID" 2>/dev/null || true
done
rm -f "$HOME_DB"* "$MAC_DB"*
cat > "$DIRECTORY" <<JSON
{ "home": {"url": "http://127.0.0.1:8765"},
  "mac":  {"url": "http://127.0.0.1:8766"} }
JSON
export ROOKERY_MAILROOMS="$DIRECTORY"
echo "## directory:"; cat "$DIRECTORY"; echo

echo "## bring up mailroom HOME (:8765) and MAC (:8766) — sidecar + relay each; MAC runs node 'bob'"
ROOKERY_DB="$HOME_DB" python3 mailroom_server.py --host 127.0.0.1 --port 8765 > /tmp/fed_sh.log 2>&1 & SH=$!
ROOKERY_DB="$MAC_DB"  python3 mailroom_server.py --host 127.0.0.1 --port 8766 > /tmp/fed_sm.log 2>&1 & SM=$!
ROOKERY_DB="$HOME_DB" ROOKERY_MAILROOM=home python3 relay.py --poll 0.5 > /tmp/fed_rh.log 2>&1 & RH=$!
ROOKERY_DB="$MAC_DB"  ROOKERY_MAILROOM=mac  python3 relay.py --poll 0.5 > /tmp/fed_rm.log 2>&1 & RM=$!
ROOKERY_DB="$MAC_DB"  python3 postmaster.py --nodes bob --engine mock --poll 0.5 > /tmp/fed_pm.log 2>&1 & PM=$!
trap 'kill $SH $SM $RH $RM $PM 2>/dev/null || true' EXIT
sleep 2
echo

echo "## from HOME: alice -> bob@mac  (crosses mailrooms)"
ROOKERY_DB="$HOME_DB" python3 send_mail.py --to bob@mac --from alice --body "hello bob, alice@home here — across mailrooms"
sleep 5
echo

echo "## MAC mailroom (bob got it from alice@home, replied):"
sqlite3 -header -column "$MAC_DB" "SELECT id,sender,recipient,substr(body,1,40) body,status FROM inbox ORDER BY id;"
echo
echo "## HOME mailroom (alice got bob@mac's reply, relayed back):"
sqlite3 -header -column "$HOME_DB" "SELECT id,sender,recipient,substr(body,1,40) body,status FROM inbox ORDER BY id;"
echo
echo "## relay logs:"; grep relayed /tmp/fed_rh.log /tmp/fed_rm.log || true
echo "demo_federation complete."
