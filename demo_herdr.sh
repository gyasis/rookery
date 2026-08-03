#!/usr/bin/env bash
# herdr as the CONSOLE over a rookery mesh — the bundled `plugin_herdr` package
# (W1-W5 of docs/HERDR_INTEGRATION.md).
#
# Rookery core knows nothing about herdr: it exposes injectors, alert sinks and
# subcommands (plugins.py), and the plugin attaches to them. Delete
# plugin_herdr/ and everything below degrades to a plain mesh with no other
# change — which is what `ROOKERY_PLUGINS= ./demo_herdr.sh` demonstrates.
#
# The point: a credential phone-home is "an agent stuck waiting on a human",
# which today is visible only if you happen to be tailing a log. Here it becomes
# a toast that finds you, a `blocked` row in the sidebar, and a pane you can jump
# to — without giving up the mailroom as the durable system-of-record.
#
# Zero API spend (mock engine). Nothing is injected into any live agent: the
# human->agent path is demonstrated by `rookery herdr focus`, not by prompting.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export ROOKERY_DB="$DIR/rookery.db"

# This box's `python3` may be a 3.9 miniconda; rookery needs 3.10+ and
# cryptography. Prefer the provisioned venv when it exists (see bootstrap.py).
if [ -z "${PY:-}" ]; then
  if [ -x "$HOME/.local/share/rookery-venv/bin/python" ]; then
    PY="$HOME/.local/share/rookery-venv/bin/python"
  else
    PY=python3
  fi
fi

if ! command -v herdr >/dev/null 2>&1; then
  echo "herdr not installed (herdr.dev) — the integration is OPTIONAL, so the"
  echo "rest of the suite is unaffected. Skipping."
  exit 0
fi
if [ "${HERDR_ENV:-}" != "1" ]; then
  echo "not inside a herdr pane (HERDR_ENV unset) — herdr-aware code no-ops here"
  echo "by design (W5). Run this from a herdr pane, or export ROOKERY_HERDR=force."
  exit 0
fi
if ! $PY -c "import sys, plugins; plugins.load(); sys.exit(0 if 'herdr' in plugins.injectors() else 1)"; then
  echo "the herdr plugin is not attached — plugin_herdr/ removed, or disabled via"
  echo "ROOKERY_PLUGINS. Core is entirely unaffected by that, which is the point."
  echo "Skipping."
  exit 0
fi

./cleanup.sh
echo

echo "## 1. bind herdr's NAMED panes to mesh nodes (W3)"
echo "##    identity comes from the pane, so it survives restarting what is IN the pane"
$PY rookery_cli.py herdr bind --sync-status || true
echo

echo "## 2. the console and the mailroom, side by side (W3/W4)"
$PY rookery_cli.py herdr status
echo

echo "## 3. start the postmaster with alert sinks armed (W2)"
echo "##    core just calls plugins.notify('needcred', ...); the herdr plugin is"
echo "##    what turns that into a toast. No plugin attached -> 0 sinks, no change."
$PY postmaster.py --nodes architect --engine mock --notify needcred --poll 0.5 &
PM=$!
trap 'kill $PM 2>/dev/null || true' EXIT
sleep 1
echo

echo "## 4. a task that makes the agent phone home for a credential"
$PY send_mail.py --to architect --from human \
  --body $'Task: ship the release notes.\nNEEDCRED:aws-prod'
sleep 4
echo
echo "##    ^ a herdr toast just fired: 'NEEDCRED — architect is blocked'."
echo "##    The approval queue stopped being something you poll."
echo

# The allocation rule in practice: 'architect' is ephemeral, so it has no pane
# and the toast is all you get. An ATTENDED node is bound to one, so the same
# surface can also hand you a jump. Requested directly — this node is not in
# --nodes, so nothing wakes it and no live agent is disturbed.
ATTENDED="$(sqlite3 rookery.db "SELECT node_id FROM nodes WHERE address IS NOT NULL LIMIT 1")"
if [ -n "$ATTENDED" ]; then
  echo "## 4b. same phone-home from an ATTENDED node ('$ATTENDED', bound to a pane)"
  $PY -c "import sys,rookery as R; c=R.connect(); print('  request #%d' % R.request_credential(c, sys.argv[1], 'vault:prod/db'))" "$ATTENDED"
  echo
fi

echo "## 5. the approval surface — blocked node, its pane, and the exact command (W4)"
$PY rookery_cli.py herdr status
echo

REQ="$(sqlite3 rookery.db "SELECT id FROM credential_requests WHERE status='pending' LIMIT 1")"
if [ -n "$REQ" ]; then
  echo "## 6. approve -> postmaster mails the grant, which wakes the node"
  $PY mesh_approve.py --id "$REQ" --approve
  sleep 4
fi
echo

echo "## 7. final state — the mailroom is still the system-of-record:"
sqlite3 -header -column rookery.db \
  'SELECT id,sender,recipient,substr(body,1,44) body,status FROM inbox ORDER BY id;'
sqlite3 -header -column rookery.db \
  'SELECT id,node_id,resource,status,token_ref FROM credential_requests;'
echo
echo "## human->agent steering stays a pane action; try it by hand:"
echo "##     rookery herdr focus <node>        jump to whoever is blocked"
echo "## agent->agent stays MAIL, at any distance. Observation is read-only:"
echo "##     herdr agent read <pane> --lines 40"
echo
echo "demo_herdr complete — console over the mesh, mailroom still the record."
