#!/usr/bin/env bash
# Mode B proof: agents stay ASLEEP; the postmaster wakes them on mail.
# Zero API spend (mock engine). Contrast with demo.sh (v1 self-polling nodes).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## start ONLY the postmaster — no persistent agents (they stay asleep)"
python3 postmaster.py --nodes architect,triage --engine mock --poll 0.5 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human sends ONE task to architect"
python3 send_mail.py --to architect --from human \
  --body $'Task: summarize repo X.\nMAILTO:triage:please send the test logs\nNEEDCRED:aws-prod'
echo
sleep 4

echo "## architect is now DURABLY PAUSED waiting on the credential — is any architect process alive?"
if pgrep -af "node-id architect" >/dev/null 2>&1; then
  echo "   architect process is momentarily awake (processing) — re-checking…"; sleep 2
fi
pgrep -af "node-id architect" >/dev/null 2>&1 \
  && echo "   architect process present" \
  || echo "   confirmed: NO architect process alive — asleep, \$0, state safe in the mailroom"
echo
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,substr(body,1,44) body,delivered FROM inbox ORDER BY id;'
echo
python3 mesh_approve.py
REQ="$(sqlite3 rookery.db "SELECT id FROM credential_requests WHERE status='pending' LIMIT 1")"
if [ -n "$REQ" ]; then
  echo "## approve the credential -> postmaster will mail the grant and wake architect"
  python3 mesh_approve.py --id "$REQ" --approve
fi
echo
sleep 4

echo "## final state — architect was woken by the grant, finished, and went back to sleep:"
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,substr(body,1,44) body,delivered FROM inbox ORDER BY id;'
sqlite3 -header -column rookery.db 'SELECT id,node_id,resource,status,token_ref FROM credential_requests;'
echo
echo "demo (postmaster / mode B) complete — agents stayed asleep between turns."
