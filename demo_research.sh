#!/usr/bin/env bash
# Research-queue demo (mock, $0): a persistent researcher "searches" each topic
# and hands findings to an ephemeral writer, working a 3-task queue end to end.
# Proves QUEUE + DISPATCH + HANDOFF + COMPLETION. (Real version: the researcher
# is a Claude node calling the DeepLake MCP — see demo_real_research.sh later.)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## postmaster: researcher=persistent (rehydrates across the queue), writer=ephemeral"
python3 postmaster.py --nodes researcher,writer --engine mock \
  --persistent researcher --idle-timeout 3 --poll 0.5 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## queue 3 research tasks -> researcher searches each, hands findings to writer"
for t in "quantum-computing" "vector-databases" "agent-protocols"; do
  python3 send_mail.py --to researcher --from human --body "RESEARCH:$t:writer"
done
echo
echo "## working the queue…"
sleep 7
echo

echo "## transcript:"
sqlite3 -header -column rookery.db \
  'SELECT id,sender,recipient,substr(body,1,50) AS body FROM inbox ORDER BY id;'
echo
DONE="$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE recipient='writer' AND body LIKE 'FINDINGS%'")"
echo "## tasks completed: ${DONE} of 3 topics researched + handed to the writer"
echo "demo_research (mock) complete."
