# Implementation Plan: `rookery init` and the rookery turn block

**Branch**: `001-rookery-init-turn-block` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-rookery-init-turn-block/spec.md`
**Revision**: 3. It re-plans after the clarify session of 2026-10-15, constitution v1.2.1 and two analyze passes.

## Summary

An agent sends mail when its final message ends with a fenced `rookery` block. One stdlib module,
`turn_block.py`, finds the last block, checks it, and acts on it. A valid block goes through
`rookery.send()`. A malformed block, an unknown name and a name in another session each become a
quarantined row with a bounce. Each valid block rewrites the task's `HANDOFF.md`. While a task is
`in-progress`, Rookery holds back only the hand-off.

Rookery owns identity (constitution IX). A Rookery session is the unit that agents work in. Rookery
generates every session ID and agent ID. Bindings (`kind:value`) tie them to panes and other tools. Mail rows
address agent IDs, so names need to be unique only inside a session. Rookery is opt-in: with no current
Rookery session, every hook does nothing.

Three adapters share one send path. The Claude Code Stop hook and `rookery turn` call `dispatch_text()`.
The outbox scan in the postmaster calls `dispatch_block()`. Rookery is mail, not a phone call: it never blocks a
stop and pushes mail only to an idle agent. `rookery init` writes a managed section and merges three hooks beside dev-kid's hooks. A
postmaster step wakes agents through configured wake commands. Decisions: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.10+ (`str | None` in `rookery.py:25`); runs on 3.13.12 here.
**Primary Dependencies**: standard library + `sqlite3` only. No new package. Wake commands run outside
tools that the configuration names (research D19).
**Storage**: the existing mailroom SQLite database. Additive: tables `rookery_sessions`, `identities`,
`bindings`, and column `inbox.session_id` (research D18). Files under the Rookery root: `HANDOFF.md`,
`outbox/`, `state/`, `logs/`, `locks/`, `unresolved/` and `postmaster/` (data-model). Configuration: `~/.rookery/config.json`.
**Testing**: `unittest` scripts with a temporary database per test, as in `test_held_quarantine.py`.
Fixture turn texts are in `fixtures/turns/`. A copy of this repo's dev-kid `settings.json` is in
`fixtures/settings/`. The migration test also runs on a COPY of the live database.
**Target Platform**: Linux, Claude Code hooks; any terminal tool through a wake command.
**Project Type**: CLI + hooks + daemon steps, inside a flat Python repo.
**Performance Goals**: a hook finishes in under 1 s on a mailroom of 10,000 rows (the prompt hook
has a 30 s limit in Claude Code; docs).
**Constraints**: hooks use no model (constitution IV); a block is ≤ 4,096 bytes (research D12); a hook
never crashes the session (FR-022); local mailroom only (FR-035).
**Scale/Scope**: one user, a few Rookery sessions of about 5 agents each, one mailroom host.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gates from `.specify/memory/constitution.md` (v1.2.1):

- [x] **I. Mailroom is the record**: mail, sessions, identities and bindings live in SQLite. The schema
      change is additive, and its test runs on a COPY of the live database. The design deletes nothing: an ended
      session keeps its rows.
- [x] **II. Proven, not claimed**: each RQ-1.x has a test in the task list. Fixtures prove R6, R7 and R8.
      The live steps in [quickstart.md](quickstart.md) prove R5, RQ-1.15 and SC-010. The register moves
      R5, R6, R7 and R8 to `proven` in this PR. The one stand-in is a fixture tool on `PATH` in place
      of the terminal tool.
- [x] **III. Any harness**: the contract is the block plus CLI calls. The three adapters share one
      `dispatch_block()`. All mail goes through `rookery.send()`. Claude Code facts come from its own docs.
- [x] **IV. Code decides; models advise**: no model in this phase. `wrong-recipient` and `off-team` are
      code lookups. The size limits are input limits with a cited source (research D12).
- [x] **V. Simple and surgical**: four new modules, no new package. Two small changes to existing
      functions: `send()` gets an optional `session_id`, and `quarantine()` passes it to the bounce.
      Agent-ID addressing (research D17) keeps every existing reader unchanged.
- [x] **VI. Observable and frugal**: two rotated JSON-lines logs: `turn-block.jsonl` for the hooks and the CLI
      (with identity events and each inbox read), and `postmaster.jsonl` for the postmaster. `--json` on every new command. The outbox scan and the wake step run inside the existing
      postmaster.
- [x] **VII. Agents' world out of the human's**: the host repo gets only the managed section and the
      merged hook groups. Workspaces, state and logs are under the Rookery root. One exception (constitution
      1.2.1): carry-over writes `HANDOFF.md` into the successor identity's folder, once, at the link.
- [x] **VIII. Plain technical English**: all prose of this phase passes `ste-lint --max-score 2.0`.
- [x] **IX. Rookery native**: Rookery generates every session ID and agent ID. Herdr, tmux and harness
      session IDs are binding kinds and wake-command entries in `~/.rookery/config.json`, not code.
- [x] **Security**: block text is data. The parser never runs it. `read` paths and safe names cannot leave
      the root. Wake commands run as argument lists, never through a shell. Rookery scopes and logs reads;
      the spec states that this is not enforcement.

**Post-design re-check (after Phase 1)**: all gates pass.

## Project Structure

### Documentation (this feature)

```text
specs/001-rookery-init-turn-block/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions D1–D22
├── data-model.md        # Phase 1: sessions, identities, bindings, block, workspace
├── quickstart.md        # Phase 1: build, migration, tests, live checks
├── contracts/
│   ├── turn-block.schema.json
│   └── cli-and-hooks.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
turn_block.py              NEW  parse, validate, dispatch, HANDOFF.md writer, paths, JSON-lines log
rookery_identity.py        NEW  sessions, identities, bindings, resolve(), config load (research D18–D21)
cli_init.py                NEW  rookery init
cli_hook.py                NEW  rookery hook session-start|prompt|stop, rookery turn
cli_rookery_session.py     NEW  rookery session …, rookery identity …, rookery inbox, rookery peek
rookery_section.md         NEW  managed-section template ({project}, {root})
rookery_config.default.json NEW default binding sources and wake commands
rookery.py                 EDIT init_db (additive tables + column); send(session_id=None); quarantine passes it
rookery_cli.py             EDIT register init, hook, turn, session, identity, inbox, peek
postmaster.py              EDIT outbox-scan and wake steps in run(); --nodes optional
bootstrap.py               EDIT add the new modules and data files to the zipapp
CHANGELOG.md               NEW  0.7.0 entry for this phase
README.md                  EDIT short "rookery init and sessions" section
fixtures/turns/*.txt  fixtures/turns/README.md  fixtures/settings/devkid.json  fixtures/bin/herdr
test_turn_block.py test_init.py test_hooks.py test_identity.py test_outbox.py test_wake.py
test_sessions_migration.py   NEW
```

**Structure Decision**: Keep the flat layout. One `cli_*.py` module per subcommand group matches
`cli_up.py`. `rookery_identity.py` holds resolution, so every caller resolves the same way.

**Outside this repo, same PR**: `okf/risks/register.md` (R5, R6, R7, R8 → `proven` with evidence).

## Requirement Trace

| ID | Built in | Proof |
|---|---|---|
| RQ-1.1 | `rookery_section.md`, `turn_block.find_last_block` | `test_turn_block` valid fixture |
| RQ-1.2 | `turn_block.validate`, [schema](contracts/turn-block.schema.json) | `test_turn_block` schema cases |
| RQ-1.3 | `turn_block.validate` (research D12) | `test_turn_block` oversize and path cases |
| RQ-1.4 | `cli_hook.stop` → `turn_block.dispatch_text` | `test_turn_block` no-block, two-blocks (R6) |
| RQ-1.5 | `dispatch_block` → `send(hold=True)` + `quarantine("malformed")` | `test_turn_block` malformed fixtures (R6) |
| RQ-1.6 | `dispatch_block` (hand-off held while `in-progress`) | `test_turn_block` handoff fixtures (R7) |
| RQ-1.7 | `turn_block.write_handoff` | `test_turn_block` HANDOFF.md content |
| RQ-1.8 | `write_handoff` (path + `task:`), section text | `test_turn_block` search by task ID |
| RQ-1.9 | `postmaster` outbox scan → `dispatch_block` | `test_outbox` |
| RQ-1.10 | `cli_init.upsert_section` | `test_init` idempotence |
| RQ-1.11 | `cli_init.merge_hooks` | `test_init` dev-kid merge |
| RQ-1.12 | `cli_hook.session_start` | `test_hooks` session start |
| RQ-1.13 | `cli_hook.prompt` | `test_hooks` 0 unread → 0 bytes |
| RQ-1.14 | no stop gate; `cli_hook.stop` writes idle state (research D8) | `test_hooks`: unread mail → stop not blocked |
| RQ-1.15 | `postmaster` wake step, idle agents only; `--nodes` optional (research D10) | `test_wake` + live check in quickstart |
| RQ-1.16 | `cli_hook.turn` (`rookery turn`) | `test_turn_block` runs the same fixtures through `turn` |
| RQ-1.17 | `dispatch_block` → `rookery.send()` only | `test_turn_block` with `ROOKERY_HOLD=1` → `held` |
| RQ-1.18 | section in `CLAUDE.md` + SessionStart `compact` re-injection | live check in quickstart (R5) |
| RQ-1.19 | all prose files of this phase | `ste-lint` step in quickstart |
| RQ-1.20 | `rookery_identity` tables, `rookery identity …` | `test_identity`, `test_sessions_migration` |
| RQ-1.21 | `rookery_identity` bindings + `config.json` sources | `test_identity` binding cases |
| RQ-1.22 | `rookery_identity.resolve` | `test_identity` resolution order (R8) |
| RQ-1.23 | `postmaster` wake step + `config.json` wake commands | `test_wake` |
| RQ-1.24 | `rookery session …`, opt-in in `resolve` | `test_hooks` no session → 0 bytes; live SC-010 |
| RQ-1.25 | agent-ID addressing, `inbox.session_id`, `off-team` in `dispatch_block` | `test_identity`, `test_turn_block` off-team |
| RQ-1.26 | `rookery session new --from`, `identity link` | `test_identity` carry-over |
| RQ-1.27 | `dispatch_block` `ROOKERY_URL` branch | `test_turn_block` remote-unsupported |

## Complexity Tracking

No gate needs a justification.

| Item | Why the design needs it | Simpler choice and why not |
|---|---|---|
| Outbox claim folder `postmaster/outbox/…/sending/` | at-most-once delivery after a crash (FR-011) | a plain rename to `sent/` after send: a crash between the two steps resends |
| `state/turn.json` | the postmaster pushes only to an idle agent (FR-018) | push on each new mail: it types into a running turn, like a phone call |
| `<root>/postmaster/` folder | each folder has one writer (constitution VII) | postmaster files inside the agent's `outbox/` and `state/`: two writers |
| Agent IDs in `recipient` | names repeat across sessions (FR-025) | a session filter in every reader: one missed reader leaks mail across sessions |
