#!/usr/bin/env bash
# End-to-end proof of the unprompted agent->agent loop + the phone-home pause.
# Uses the mock engine: ZERO API spend, fully deterministic.
#
# Flow:
#   human -> architect : "Task ... MAILTO:triage:please send logs / NEEDCRED:aws-prod"
#   architect wakes UNPROMPTED -> mails triage, then PAUSES on the credential
#   triage wakes UNPROMPTED -> acks architect
#   you approve the credential -> architect RESUMES
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## starting two headless nodes (architect, triage) — mock engine"
python3 node_runner.py --node-id architect --engine mock --poll 0.5 &
A=$!
python3 node_runner.py --node-id triage --engine mock --poll 0.5 &
T=$!
trap 'kill $A $T 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human sends ONE task to architect (with a directive to ask triage + a credential need)"
python3 send_mail.py --to architect --from human \
  --body $'Task: summarize repo X.\nMAILTO:triage:please send the test logs\nNEEDCRED:aws-prod'
echo
sleep 4

echo "## mailroom after the unprompted hops (architect woke, mailed triage, triage acked, architect paused):"
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,substr(body,1,46) body,delivered FROM inbox ORDER BY id;'
echo
echo "## nodes (architect should be 'waiting' on the credential):"
sqlite3 -header -column rookery.db 'SELECT node_id,status FROM nodes;'
echo
echo "## pending credential requests:"
python3 mesh_approve.py
echo

REQ="$(sqlite3 rookery.db "SELECT id FROM credential_requests WHERE status='pending' LIMIT 1")"
if [ -n "$REQ" ]; then
  echo "## approving credential #$REQ (mints a JIT token pointer, not the secret)…"
  python3 mesh_approve.py --id "$REQ" --approve
fi
echo
sleep 3

echo "## final state — architect resumed after approval:"
sqlite3 -header -column rookery.db 'SELECT node_id,status FROM nodes;'
sqlite3 -header -column rookery.db 'SELECT id,node_id,resource,status,token_ref FROM credential_requests;'
echo
echo "demo complete."
