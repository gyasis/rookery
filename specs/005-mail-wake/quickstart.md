# Quickstart: Event-driven mail wake

Use a scratch herdr workspace, a scratch project and a scratch database. Never use the live database. Use
`rookery up --no-launch` and fake panes, so that no agent starts and no model runs.

## 1. A scratch project

```bash
S=~/Documents/code/.worktrees/livecheck-005
mkdir -p $S && cd $S && git init -q    # `rookery up` needs a git repository
export HOME=$S/home ROOKERY_DB=$S/rookery.db ROOKERY_ROOT=$S/root
cat > team.json <<'EOF'
{"agents": [{"name": "alice", "role": "boss", "harness": "claude"}, {"name": "bob", "role": "reader", "harness": "claude"}]}
EOF
```

## 2. Start a session with no name

```bash
rookery up team.json --no-launch --json
```

Expect `"name_made": true`, a three-word `"session_name"` and `"sweep": "started"`. Run the same command with
`--session NAME`. Expect the same session and `"sweep": "running"`. Without `--json`, the text output has the
lines `Session: NAME (name made)` and `Sweep: started`.

## 3. Event wake for an idle agent

Bind bob to a fake pane command that records its arguments: the fake `herdr` in `fixtures/bin` writes them to
`$HERDR_RECORD` (`test_wake.WakeBase` sets this up). Write bob's state as `idle`. Then send an ask as alice:

```bash
ROOKERY_SESSION=NAME ROOKERY_NODE=alice rookery send bob "Please review the plan." --kind ask
```

Expect, within 5 seconds: one line in the record of the fake pane, and `pushed` in the log of bob. Send a mail of
kind `status`. Expect no new line.

## 4. One wake per agent

Send 10 asks to bob within 1 second. Expect at most one notice for each new mail, and `wake-busy` lines in the
log.

## 5. The sweep

Kill a wake before it types (or write bob's state `idle` after the mail). Run one sweep round with an injected
clock (`postmaster.py --session NAME` in the test harness). Expect one notice.

## 6. Remote check

Start a local mailroom on `127.0.0.1` in the scratch folder. Set `ROOKERY_URL`, `ROOKERY_TOKEN` and
`ROOKERY_NODE`. Run `rookery check --json` for one check: expect `pushed` and one notice when an ask waits, else
`nothing`. Run `rookery check --follow --json`: expect `follower-started`, then `follower-running` on a second
run. For the bound, run `rookery_remote.run_follower` in the test harness with an injected clock and sleep.
Expect at most 5 checks per trigger and one notice.

## 7. Cost

Count model calls with the fake screener backend. With screening off (the default), expect 0 calls for all steps.
