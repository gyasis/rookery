---
type: Quickstart
task: 002-mail-gate
updated: 2026-10-23
revision: 3
---

# Quickstart: every Rookery send goes through one gate

Run each step from the worktree root. Steps 1 to 4 use temporary databases and change nothing live.

## 1. Prove that opening a copy of the live database changes nothing (constitution I, FR-026, SC-007)

This feature adds no table, no column and no reason, so it needs no migration. The step proves it.

```bash
cp ~/Documents/code/rookery/rookery.db /tmp/rookery-copy.db
sqlite3 /tmp/rookery-copy.db "SELECT COUNT(*) FROM inbox"   > /tmp/count-before.txt
sqlite3 /tmp/rookery-copy.db ".schema"                      > /tmp/schema-before.txt
ROOKERY_DB=/tmp/rookery-copy.db python3 -c "import rookery as R; R.connect()"   # connect() runs init_db
sqlite3 /tmp/rookery-copy.db "SELECT COUNT(*) FROM inbox"   > /tmp/count-after.txt
sqlite3 /tmp/rookery-copy.db ".schema"                      > /tmp/schema-after.txt
diff /tmp/count-before.txt /tmp/count-after.txt && diff /tmp/schema-before.txt /tmp/schema-after.txt && echo same
ROOKERY_DB=/tmp/rookery-copy.db python3 -m unittest test_sessions_migration -v
```

Record both counts in the PR. Both `diff` commands print nothing.

## 2. Run the tests

```bash
python3 -m unittest test_turn_block test_init test_hooks test_identity test_outbox test_wake \
  test_send test_notices test_held_quarantine test_sessions_migration \
  test_gate_log test_gate test_gate_scan test_system_mail \
  test_paths_local test_paths_mcp test_paths_send_mail test_paths_mailroom test_paths_node_runner \
  test_mailroom_gate test_mailroom_start test_mailctl test_mcp_gate test_read_mail \
  test_reads test_mentions test_pane test_text_hints test_demo_starts test_mailroom_hardening -v
```

| Test file | Proves |
|---|---|
| `test_turn_block`, `test_send` (no change) | FR-002: the block path and `rookery send` keep their results |
| `test_gate_log` | FR-030: the gate log path, the `~/.rookery/logs` fallback, the stderr line on failure |
| `test_gate` | FR-001, FR-005 to FR-008, FR-012 to FR-014, FR-030, FR-031, FR-037: the order of checks, the `no-session` warning, the log, `ack`, `loop reset`, the text lint |
| `test_gate_scan` | FR-004, FR-021, SC-002: the AST scan, the `quarantine` and `system_send` scans, the import scan |
| `test_paths_local`, `test_paths_mcp`, `test_paths_send_mail`, `test_paths_mailroom`, `test_paths_node_runner` | FR-003, FR-011, FR-027, SC-001: 9 paths with 3 tries each (6 + 6 + 3 + 9 + 3 = 27 cases) |
| `test_system_mail` | FR-012: `postmaster` and `relay` mail through `system_send` with a session |
| `test_mailroom_gate` | FR-015, SC-005, SC-009: 401 with no token, `refused` answers, A2A outcomes, reads by node name |
| `test_mailroom_start` | FR-022 to FR-024: start rules for `mailroom_server.py` and `rookery serve` |
| `test_mailctl` | FR-029: `mailctl.py` refusal text and legacy label |
| `test_mcp_gate` | FR-003, FR-032: MCP send, `check_inbox` twice, `ack` |
| `test_read_mail` | FR-015, SC-009: `read_mail.py` reads |
| `test_reads` | FR-015, FR-016, SC-004, SC-010: session filter and legacy mail to the reader |
| `test_mentions` | FR-017, FR-018: the mention scan |
| `test_pane` | FR-019, FR-020, SC-006: pane notice, no ack, no `send_mail.py` hint |
| `test_text_hints` | FR-029, SC-002: no `send_mail.py` hint in `cli_up.py` and `plugin_herdr/cli.py` |

## 3. Check sends by hand

```bash
export ROOKERY_DB=$(mktemp -d)/t.db ROOKERY_ROOT=$(mktemp -d)
rookery session new --name demo
export ROOKERY_SESSION=demo
rookery identity new alice; rookery identity new bob
ROOKERY_NODE=alice python3 send_mail.py --to bob --body "hello" --from postmaster; echo "exit $?"   # exit 2, nothing stored
ROOKERY_NODE=alice python3 send_mail.py --to bob --body "hello"; echo "exit $?"                     # exit 0
ROOKERY_NODE=alice python3 send_mail.py --to bob --body "hello" --from carol; echo "exit $?"        # exit 0, sender carol
env -u ROOKERY_SESSION python3 send_mail.py --to bob --body "hi" --from old-node; echo "exit $?"    # exit 0, stored in legacy
tail -n 4 "$ROOKERY_ROOT/logs/gate.jsonl"      # 4 records: refused, sent, sent as carol (asked_sender carol, principal alice), sent with warning no-session
ROOKERY_NODE=bob rookery inbox                  # bob sees the legacy mail to its name (FR-015)
```

## 4. Check the mailroom start rules

```bash
python3 mailroom_server.py --port 18765; echo "exit $?"     # exit 2, prints the two choices
rookery serve --port 18765; echo "exit $?"                  # exit 2, same two choices
python3 mailroom_server.py --port 18765 --host 0.0.0.0 --token T; echo "exit $?"   # exit 2: needs --allow-public
export ROOKERY_TOKEN=T
python3 mailroom_server.py --port 18765 &                     # binds 127.0.0.1
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:18765/send -d '{"recipient":"bob","body":"x"}'   # 401
curl -s -w '\n%{http_code}\n' -X POST localhost:18765/send -H "Authorization: Bearer $ROOKERY_TOKEN" \
  -d '{"sender":"alice","recipient":"bob","body":"x"}'        # 200, outcome sent
```

## 5. Lint the prose (constitution VIII, FR-031)

```bash
for f in specs/002-mail-gate/*.md specs/002-mail-gate/contracts/*.md CHANGELOG.md README.md \
         docs/QUICKSTART.md docs/COOKBOOK.md docs/STORIES.md docs/NODE_BRIEFING.md; do
  ste-lint "$f" --max-score 2.0 --glossary ste-glossary.txt || echo "FAIL $f"
done
```

The bounce texts, refusal messages and notices live in code. `test_gate` writes each one to a file and
runs `ste-lint` on it.

## 6. Live deployment (the user decides when)

The live MCP service and mailroom run from `~/Documents/code/rookery` (research D18). After merge:

1. Save a copy of the live database: `cp rookery.db ~/.rookery/backups/rookery.db.pre-mail-gate-<date>`.
2. Move that checkout to master, then rebuild `~/.local/bin/rookery` with `bootstrap.build_pyz`.
3. Keep the shared mailroom token (`$ROOKERY_TOKEN`) in the unit environment. Callers change nothing: no
   per-agent tokens and no MCP header changes.
4. Restart `rookery-mcp.service` and the mailroom. Send one test mail from an agent with no session and
   check that it arrives.
