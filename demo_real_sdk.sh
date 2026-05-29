#!/usr/bin/env bash
# Warm SDK node in the mesh: the postmaster spawns a long-lived Claude session
# (engine=claude-sdk). It handles 3 sequential tasks — watch turn #1 be cold-ish
# (~7s) and turns #2/#3 be warm (~2s), vs ~115s each with plain `claude -p`.
# Paid but cheap/fast (~25s of model time total).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## postmaster: assistant = claude-sdk (warm long-lived session, hooks off)"
python3 postmaster.py --nodes assistant=claude-sdk --idle-timeout 18 --poll 1 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## three quick tasks, spaced so each is its own turn on the SAME warm session:"
python3 send_mail.py --to assistant --from human --body "Reply: name a primary color. Send your answer to human via MAILTO:human:<answer>."
sleep 13
python3 send_mail.py --to assistant --from human --body "Reply: name another primary color. Send it via MAILTO:human:<answer>."
sleep 7
python3 send_mail.py --to assistant --from human --body "Reply: name one more. Send it via MAILTO:human:<answer>."

echo "## settling…"
for _ in $(seq 1 40); do
  PENDING="$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE delivered=0")"
  RUNNING="$(pgrep -fc sdk_node.py || true)"
  if [ "$PENDING" = "0" ] && [ "${RUNNING:-0}" = "0" ]; then break; fi
  sleep 2
done
echo
echo "## answers that landed in the mailroom:"
sqlite3 -header -column rookery.db "SELECT id,sender,recipient,substr(body,1,48) body FROM inbox WHERE recipient='human' ORDER BY id;"
echo
echo "demo_real_sdk complete (see the 'turn #N done in Xs' lines above: #1 cold-ish, #2/#3 warm)."
