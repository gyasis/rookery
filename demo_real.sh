#!/usr/bin/env bash
# THE demo: a Claude node and an OpenAI Codex node collaborating via the mesh.
# Uses REAL models -> costs API/subscription usage and takes ~30-120s.
# Requires: `claude` (logged in) and `codex` (run `codex login` first).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## postmaster: architect=claude (persistent partner), reviewer=codex (ephemeral)"
python3 postmaster.py --nodes architect=claude,reviewer=codex \
  --persistent architect --idle-timeout 10 --poll 1 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human tasks the CLAUDE architect, telling it to consult the CODEX reviewer"
python3 send_mail.py --to architect --from human --body \
"Write a one-line Python function is_prime(n). Then send it to the reviewer for critique using a line exactly: MAILTO:reviewer:<the function> please review."
echo
echo "## running real models — settling (up to ~4 min)…"
for i in $(seq 1 80); do
  sleep 3
  PENDING="$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE delivered=0")"
  RUNNING="$(pgrep -fc 'node_runner.py' || true)"
  if [ "$PENDING" = "0" ] && [ "${RUNNING:-0}" = "0" ]; then break; fi
done
echo
echo "## conversation transcript (the Claude<->Codex exchange):"
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,substr(body,1,80) AS body FROM inbox ORDER BY id;'
echo
echo "demo_real complete."
