#!/usr/bin/env bash
# REAL DeepLake slice: a Claude researcher actually calls the DeepLake MCP tool,
# then hands findings to a Codex writer. Costs API/usage; ~3-5 min.
# Requires: claude (logged in, deeplake MCP in ~/.claude.json) + codex (logged in).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo
TOPIC="LLM agents using tools to act on their environment"

echo "## postmaster: researcher=claude (DeepLake tool allowed), writer=codex (ephemeral)"
python3 postmaster.py --nodes researcher=claude,writer=codex \
  --allowed-tools "mcp__deeplakesearch__retrieve_context mcp__deeplakesearch__get_summary" \
  --poll 1 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human task -> researcher: use DeepLake, hand findings to the writer"
python3 send_mail.py --to researcher --from human --body \
"Use your DeepLake search tool (mcp__deeplakesearch__retrieve_context) to research \"$TOPIC\". Summarize the top findings in 3 short bullets grounded in what the tool returns. Then hand them to the writer with a line EXACTLY in this form: MAILTO:writer:Findings on $TOPIC: <your 3 bullets>. Also instruct the writer to produce a 2-sentence executive summary and send it to human via MAILTO:human:<summary>."
echo
echo "## running real models (claude ~2min incl. DeepLake, then codex ~15s)…"
for i in $(seq 1 110); do
  sleep 3
  PENDING="$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE delivered=0")"
  RUNNING="$(pgrep -fc node_runner.py || true)"
  if [ "$PENDING" = "0" ] && [ "${RUNNING:-0}" = "0" ]; then break; fi
done
echo
echo "## transcript (full bodies):"
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,body FROM inbox ORDER BY id;'
echo
echo "demo_real_research complete."
