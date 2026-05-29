#!/usr/bin/env bash
# Terminal node (substrate A): a human/agent sits in a tmux pane; the watcher
# injects mail into it. Here a detached pane runs `cat`; we send mail and prove
# it appears in the pane via capture-pane. (Swap --injector wezterm/zellij to use
# a Rust multiplexer.)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not installed — install it, or run terminal_node.py with --injector wezterm/zellij"
  exit 0
fi

./cleanup.sh
tmux kill-session -t rooktest 2>/dev/null || true
tmux new-session -d -s rooktest -x 200 -y 50 'cat'   # a pane that echoes injected lines
python3 terminal_node.py --node-id deskbot --injector tmux --target rooktest --poll 0.5 > /tmp/term_node.log 2>&1 &
TN=$!
trap 'kill $TN 2>/dev/null || true; tmux kill-session -t rooktest 2>/dev/null || true' EXIT
sleep 1
echo

echo "## send mail to terminal node 'deskbot' (a human/agent sitting in the pane):"
python3 send_mail.py --to deskbot --from alice --body "can you review PR 42 when free?"
sleep 2
echo
echo "## captured from the tmux pane (the injected mail appears there):"
tmux capture-pane -t rooktest -p | grep -i ROOKERY || tmux capture-pane -t rooktest -p | tail -4
echo
echo "## node log:"; cat /tmp/term_node.log
echo "demo_terminal complete."
