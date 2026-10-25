# Quickstart: the screener chain and the broker gates

Run every step from the worktree root. Steps 1 to 4 use no model and no live database. Step 5 is the one live
check on the model host. It stays UNTESTED until it runs.

## 1. The schema change on a copy of the live database (FR-035)

```bash
cp_db=$(mktemp -d ~/Documents/code/.worktrees/dbcopy.XXXX)/rookery.db
python3 - "$cp_db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect("file:" + __import__("os").path.expanduser("~/Documents/code/rookery/rookery.db") + "?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()
EOF
ROOKERY_DB="$cp_db" python3 -m unittest test_screen_schema.ScreenSchemaLiveCopy -v
```

Expected: the test passes. The row count of `inbox` is the same before and after, and every new table and
column exists. The test opens the live file read-only and never changes it.

## 2. The tests

```bash
python3 -m unittest test_screen_schema test_screen_backend test_screen_t0 test_screen_run \
  test_screen_calls test_screen_cli test_threads test_escalated test_session_names -v
python3 -m unittest test_turn_block test_send test_hooks test_outbox test_wake test_identity test_init \
  test_gate test_gate_log test_gate_scan test_held_quarantine test_views -v
```

Expected: all pass. `test_screen_backend.Contract` skips its PromptChain half, with a message, when
`import promptchain` fails.

## 3. A scratch shadow run with the fake backend

```bash
export HOME=$(mktemp -d) ROOKERY_DB=$HOME/r.db ROOKERY_ROOT=$HOME/root
export ROOKERY_SCREENER_FAKE=$PWD/fixtures/screener/fake_t1.json
python3 rookery_cli.py up fixtures/screener/team.json --json
python3 rookery_cli.py send --as researcher --to counsel "Is clause 4 in scope?"
python3 rookery_cli.py screen run --once
python3 rookery_cli.py screen status --json
python3 rookery_cli.py screen review --json
```

Expected:
- The status shows backlog 0 and every check in `shadow`.
- The review lists one `t1` decision with `screener:tev1:4b`.
- The inbox status of the mail is still `pending` (FR-014).
- `python3 rookery_cli.py session new` with no name prints a name of three words (FR-037). `rookery up` still
  needs the `session` key of the team file.

## 4. The live switch rule (SC-005)

```bash
python3 -m unittest test_screen_calls.LiveSwitch -v
```

Expected: `rookery screen mode t1 live` refuses at 149 compared decisions and names 1 missing. It accepts at 150
with the bands set.

## 5. The live check on the model host (SC-013), UNTESTED until run

Run it in an isolated herdr workspace, with a scratch `HOME`, `ROOKERY_DB` and `ROOKERY_ROOT`. Never use the
live database.

```bash
unset ROOKERY_SCREENER_FAKE
curl -s -m 5 http://192.168.0.159:8799/llm/ollama/api/show -d '{"model":"tev1:4b"}' | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d.get("capabilities"))'      # expect ['decision']
python3 rookery_cli.py send --as researcher --to counsel "Is clause 4 in scope?"
python3 rookery_cli.py screen run --once
sqlite3 "$ROOKERY_DB" "SELECT label, confidence, decided_by, backend_impl, prompt_tokens, reasoning_tokens
  FROM screen_decisions WHERE stage='t1'"
```

Expected: one row, a label from the four, `screener:tev1:4b`, `reasoning_tokens` 0 or NULL. Run it once under
miniconda (`backend_impl = promptchain`) and once with `/usr/bin/python3 rookery_cli.py ...`
(`backend_impl = stdlib`). Record both results in this file.

## 6. Deploy (after the merge)

The user decides when. The zipapp and `SKILL.md` were not rebuilt after 003 (the user's choice). This feature
adds `cli_screen.py`, `screener.py`, `screener_t0.py`, `screener_backend.py`, `screener_config.py` and
`rookery_names.py` to `bootstrap._PY_MODULES`. Save a copy of the live database first.
