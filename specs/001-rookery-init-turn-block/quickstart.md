---
type: Quickstart
task: 001-rookery-init-turn-block
updated: 2026-10-15
revision: 2 (sessions, identities, bindings)
---

# Quickstart: `rookery init` and the rookery turn block

## 1. Build and install

```bash
python3 bootstrap.py            # rebuilds ~/.local/bin/rookery (zipapp) with the new modules
rookery init --help             # proves the installed copy has the new subcommand
```

## 2. Prove the migration on a copy of the live database (constitution I)

```bash
cp ~/Documents/code/rookery/rookery.db /tmp/rookery-copy.db
ROOKERY_DB=/tmp/rookery-copy.db python3 test_sessions_migration.py
```

Pass = the new tables exist, every old row has `session_id='legacy'`, old data is byte-identical, a second
run changes nothing, and `rk peek --as <old node>` still lists that node's mail.

## 3. Install into a project, then start a Rookery session

```bash
cd ~/Documents/code/<project>
rookery init --json                     # CLAUDE.md section + 3 merged hooks
rookery init --json                     # run again: every action reads "unchanged"
rookery session new --name alpha --json # Rookery generates the session ID
```

Start each agent in a NEW pane: `ROOKERY_SESSION=alpha ROOKERY_NODE=researcher claude`. Or bind the
session to a herdr workspace once (`rookery session new --name alpha --bind herdr-workspace:<id>`); then
every new pane in that workspace joins `alpha`. A pane that was open before init does not join.

Start the postmaster once, from the Rookery repo: `python3 postmaster.py`. Without `--nodes` it runs
only the outbox scan and the idle wake (research D10).

## 4. Run the tests

```bash
python3 -m unittest test_turn_block test_init test_hooks test_identity test_outbox test_wake -v
python3 test_held_quarantine.py         # PR #14 tests: must still pass
```

| Test file | Proves |
|---|---|
| `test_turn_block.py` | R6: `no-block` → 0 mail, `valid` → 1 mail, `two-blocks` → last only, `malformed-*` → 1 `malformed` row + bounce. R7: `handoff-in-progress` → 0 mail + `HANDOFF.md`; `handoff-done` → 1 mail. Size, path, kind rules. `wrong-recipient` with `suggest`; `off-team`; `ROOKERY_URL` → `remote-unsupported`. |
| `test_init.py` | Idempotence (3 runs, byte-identical), merge into a copy of this repo's dev-kid `settings.json`, invalid JSON → exit 2 and no write. |
| `test_identity.py` | R8: two agent sessions with different bindings → two identities. Same pane in a new Rookery session → new ID, 0 old mail. No session → nothing. `--from` copies `HANDOFF.md` and sets `previous_id`, no mail. Binding move. |
| `test_hooks.py` | No session → 0 bytes from every hook. Prompt hook: 0 unread → 0 bytes. The Stop hook never blocks and writes `state/turn.json` idle. The prompt hook writes busy. Session start: who-am-I line, newest `HANDOFF.md`, `CLAUDE_ENV_FILE`. `rookery inbox` shows only own mail and changes nothing. `rookery ack` marks only own unread mail read. A failed block gives one notice line, once. |
| `test_outbox.py` | Exactly one send per file, files kept, restart does not resend. The scan never writes `HANDOFF.md`. |
| `test_wake.py` | Wake command from `config.json` runs once for each batch of new mail, as an argument list; a fixture script on `PATH` stands in for the terminal tool. A busy agent gets no push until its Stop. Without a binding there is no push, and the log reads `no-wake-binding`. `postmaster.py` runs with no `--nodes`. |

## 5. Lint the prose (constitution VIII, RQ-1.19)

```bash
for f in specs/001-rookery-init-turn-block/*.md specs/001-rookery-init-turn-block/contracts/*.md \
         rookery_section.md CHANGELOG.md; do
  ste-lint "$f" --max-score 2.0 --glossary ste-glossary.txt || echo "FAIL $f"
done
```

## 6. Live checks (cannot be fixtures)

- **RQ-1.15 / RQ-1.23 wake**: start an agent in a new herdr pane in session `alpha`. From another pane in
  `alpha`, send it mail with a block while the agent works on a long turn. Pass = no notice during that turn.
  The notice appears within one postmaster loop after the turn ends (mail, not a phone call).
- **R5 / RQ-1.18 compaction**: work in that pane until Claude Code compacts the context (or run
  `/compact`). Then ask the agent: "hand off TASK-T1 to tester". Pass = the next turn ends with a valid
  block, and `rookery peek` in the `tester` pane shows the mail. Record the transcript path in the register.
- **SC-010 opt-in**: open a plain `claude` session in the initialized project without `ROOKERY_SESSION`.
  Pass = no Rookery line at start, no notices, no state file, no new roster row.
- **R19**: no live Codex run in this phase. Fixtures prove `rookery turn`.
