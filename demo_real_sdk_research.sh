#!/usr/bin/env bash
# Warm SDK researcher WITH the DeepLake MCP tool -> Codex writer -> human.
# Same as demo_real_research.sh but the researcher is a warm claude-sdk node
# (no ~115s cold start; the DeepLake server is attached explicitly via --mcp).
# Paid; ~30-60s (warm SDK + DeepLake open) vs ~2.5min for the claude -p version.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo
TOPIC="LLM agents using tools to act on their environment"

echo "## postmaster: researcher=claude-sdk (WARM + DeepLake MCP), writer=codex (ephemeral)"
python3 postmaster.py --nodes researcher=claude-sdk,writer=codex \
  --mcp deeplakesearch \
  --allowed-tools "mcp__deeplakesearch__retrieve_context mcp__deeplakesearch__get_summary" \
  --idle-timeout 12 --poll 1 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human task -> researcher: use DeepLake, hand findings to the writer"
python3 send_mail.py --to researcher --from human --body \
"Use your DeepLake search tool (mcp__deeplakesearch__retrieve_context) to research \"$TOPIC\". Summarize the top findings in 3 short bullets grounded in what the tool returns. Then hand them to the writer EXACTLY as: MAILTO:writer:Findings on $TOPIC: <your 3 bullets>. Also instruct the writer to produce a 2-sentence executive summary and send it to human via MAILTO:human:<summary>."
echo
echo "## running (warm SDK researcher — watch turn timing vs the ~2.5min claude -p run)…"
for _ in $(seq 1 80); do
  sleep 3
  PENDING="$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE delivered=0")"
  RUNNING="$(pgrep -fc 'sdk_node.py|node_runner.py' || true)"
  if [ "$PENDING" = "0" ] && [ "${RUNNING:-0}" = "0" ]; then break; fi
done
echo
echo "## transcript:"
sqlite3 -header -column rookery.db 'SELECT id,sender,recipient,substr(body,1,80) AS body FROM inbox ORDER BY id;'
echo
echo "demo_real_sdk_research complete."
