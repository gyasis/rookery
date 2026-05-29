#!/usr/bin/env bash
# Multi-vendor PARALLEL team: a Claude architect fans ONE task out to a Gemini
# researcher + a Codex reviewer + a 2nd Claude coder, who work CONCURRENTLY
# (the postmaster wakes them in parallel) and reply. Paid: claude x2 + codex + gemini.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

./cleanup.sh
echo

echo "## team roster: architect=claude-sdk · coder=claude-sdk · reviewer=codex · researcher=gemini"
python3 postmaster.py \
  --nodes architect=claude-sdk,coder=claude-sdk,reviewer=codex,researcher=gemini \
  --idle-timeout 10 --poll 1 > /tmp/team_pm.log 2>&1 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## human asks the Claude architect to FAN OUT in parallel:"
python3 send_mail.py --to architect --from human --body \
"You are coordinating a build. Fan the work out IN PARALLEL by emitting EXACTLY these three lines and nothing else:
MAILTO:researcher:Research short-code generation + collision avoidance for a URL shortener (2 bullets). Reply to architect via MAILTO:architect:<result>.
MAILTO:reviewer:List 3 security considerations for a public URL shortener. Reply to architect via MAILTO:architect:<result>.
MAILTO:coder:Draft a short Python generate_short_code(n=7). Reply to architect via MAILTO:architect:<result>."
echo
echo "## running (architect warms, then researcher+reviewer+coder run concurrently)…"
for _ in $(seq 1 90); do
  sleep 3
  PENDING=$(sqlite3 rookery.db "SELECT COUNT(*) FROM inbox WHERE status='pending'")
  RUNNING=$(pgrep -fc 'sdk_node.py|node_runner.py' || true)
  if [ "$PENDING" = "0" ] && [ "${RUNNING:-0}" = "0" ]; then break; fi
done
echo

echo "## transcript:"
sqlite3 -header -column rookery.db "SELECT id,sender,recipient,substr(body,1,56) AS body FROM inbox ORDER BY id;"
echo
echo "## specialists that reported back to the architect:"
sqlite3 rookery.db "SELECT DISTINCT sender FROM inbox WHERE recipient='architect' AND sender!='human';"
echo
echo "## node wake times (overlap = parallel):"
grep -E "woke on|warm session ready|online" /tmp/team_pm.log | grep -vi "(postmaster)" | head -20
echo "demo_real_team complete."
