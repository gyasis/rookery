# Implementation Plan: every Rookery send goes through one gate

**Branch**: `002-mail-gate` | **Date**: 2026-10-22 | **Updated**: 2026-10-23 (revision 2) | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-mail-gate/spec.md` (5 clarifications of 2026-10-22, one of 2026-10-23)

## Summary

Today only the block path checks mail. About ten other send paths call the bare insert. They take the
sender from the caller and stamp the `legacy` session. This feature adds one module, `mail_gate.py`.
Every send path calls it.

Local identity is bookkeeping, not security (research D24). The user owns every agent on the machine, so
the gate does not police local names. Local paths resolve a principal as spec 001 does. HTTP paths have no
principal. The sender is the asked sender when given, else the principal's agent ID, else the raw name of the
path. The gate refuses only a system name and an invalid name. It records the asked sender and the
principal in the log. When the sender has a session, the gate runs the block path checks (recipient in the
session, `off-team`, `wrong-recipient`, screening, the loop breaker). A sender with no session is
delivered, `pending` in `legacy`, with a `no-session` warning in the log.

The insert needs a session ID, and a test fails on any new direct caller. Reads get a session filter
that also shows `legacy` mail to the reader's name. No read marks mail read: MCP gets an `ack` tool. The
mailroom binds `127.0.0.1` and refuses to start with no token. It keeps its one shared token, and
`check_start` holds the start rules for both entry points. Mention notices and pane notices carry no body.
Decisions D1 to D24: [research.md](research.md). There is no schema change.

## Technical Context

**Language/Version**: Python 3.10+ (`str | None` in `rookery.py:45`). It runs on 3.13 here.
**Primary Dependencies**: standard library and `sqlite3`: `ast` for the caller scan. `mcp` is already a dependency of `rookery_mcp.py`. No new package.
**Storage**: the existing mailroom SQLite database. No schema change ([data-model.md](data-model.md)). New log `<ROOKERY_ROOT>/logs/gate.jsonl`, else `~/.rookery/logs/gate.jsonl`.
**Testing**: `unittest` scripts with a temporary database per test, based on `test_hooks.Base`. The mailroom
tests start `mailroom_server.py` on a free local port. The MCP tests call the MCP functions directly.
The migration test also runs on a COPY of the live database.
**Target Platform**: Linux. The same user runs all agents (spec, Assumptions: local identity is
bookkeeping, D24).
**Project Type**: CLI, MCP server, HTTP server and daemon steps in a flat Python repo.
**Performance Goals**: no new target. The gate uses keyed lookups only (research D19). The spec 001
hook target stays: under 1 s on 10,000 rows (`test_hooks.HookTiming`).
**Constraints**: no model in the gate (FR-025). The gate never falls back to an ungated send (FR-007).
Spec 001 tests and `test_send.py` pass with no change (FR-002).
**Scale/Scope**: one user, a few sessions of about 5 agents, one mailroom, one shared HTTP token.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gates from `.specify/memory/constitution.md` (v1.2.1):

- [x] **I. Mailroom is the record**: SQLite stays the record. The change adds no table, no column and no
      reason. Refused sends store nothing, because they are not mail. The gate log records them. Quickstart 1
      opens a COPY of the live database and checks that no row count or schema changes.
- [x] **II. Proven, not claimed**: each FR has a test in the trace below. SC-001 is a 27-case matrix in
      the five `test_paths_*` files. No RQ or R entry moves in this feature (spec: no RQ IDs of its own). The live
      deployment is a quickstart step, labelled as the user's decision.
- [x] **III. Any harness**: the contract stays the block and the CLI. MCP and HTTP get the same gate, so
      a harness with no hooks gets the same checks.
- [x] **IV. Code decides; models advise**: no model. The checks are lookups and counters.
      `decided_by` is `gate` for new decisions and stays `turn-block-parser` on the block path.
- [x] **V. Simple and surgical**: one new module. The block checks move; they are not rewritten. No new
      package. `loop reset` has FR-037. The Complexity Tracking table justifies the items that go past the
      FR text. The revision of 2026-10-23 removed the token table, the token CLI, scopes, the route table,
      the relay token and the unmigrated report.
- [x] **VI. Observable and frugal**: one rotated log for every gate decision (FR-030). `--json` on each
      new command. Each refusal text names the fix. No new daemon.
- [x] **VII. Agents' world out of the human's**: no change to the host repo. The log is under the Rookery root.
- [x] **VIII. Plain technical English**: the plan files pass `ste-lint`. Code texts (bounce, refusal,
      notice) get a lint test (FR-031).
- [x] **IX. Rookery native**: the gate uses Rookery sessions and identities. It adds no outside ID.
- [x] **Development Workflow**: `spec-pane cover` checks that every `RQ-<phase>.N` ID of the requirements file
      has a citation (`~/.local/bin/spec-pane:96-120`). This feature has no RQ IDs and extends spec 001's. So
      `cover` for phase 1 would fail by design. This plan records the waiver, and so does
      `checklists/requirements.md:40`. The real fix is to add RQ IDs for this feature to the external
      requirements file. That is the user's decision.
- [x] **Security**: the mailroom refuses outside calls by default. It binds `127.0.0.1` and needs its
      shared token. Mail stays data: notices carry no body. No destructive action: nothing deletes a row.
      Local identity is bookkeeping (D24): the log shows a mismatch, and the mailroom is the one real
      boundary.

**Post-design re-check (after Phase 1)**: all gates pass. One gap stays open by decision (D24): Rookery
does not enforce local names. An agent can still set `ROOKERY_NODE` or `ROOKERY_SESSION` (RQ-2.9, a later phase).

## Project Structure

### Documentation (this feature)

```text
specs/002-mail-gate/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions D1–D24
├── data-model.md        # Phase 1: gate input and decision, order of checks, log record (no schema change)
├── quickstart.md        # Phase 1: copy check, tests, hand checks, live deployment
├── contracts/gate.md    # Phase 1: Python API, CLI, MCP tools, HTTP, notices, log warning
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
mail_gate.py             NEW  submit, system_send, quarantine_malformed, principal_from_env, unread, unread_legacy, unread_by_name, ack
rookery.py               EDIT send(session_id has no default)
turn_block.py            EDIT _dispatch_block calls submit() with pre_store; malformed calls quarantine_malformed; unread() calls mail_gate.unread; log stream gate
cli_hook.py              EDIT none expected (rookery send already uses dispatch_block)
cli_rookery_session.py   EDIT cmd_ack calls mail_gate.ack; loop reset
rookery_identity.py      EDIT new_identity refuses system sender names
rookery_cli.py           EDIT serve --host default 127.0.0.1, --allow-public, --allow-no-auth (keeps --token)
cli_serve.py             EDIT cmd_serve calls mailroom_server.check_start
mailctl.py               EDIT HTTP error text; legacy label
plugin_herdr/cli.py      EDIT agent briefings (:201-210) name rookery send, not mailctl or send_mail.py
rookery_mcp.py           EDIT send via submit; check_inbox/await_message read only; new ack tool; no _register
send_mail.py             EDIT principal from env; --from is the sender
read_mail.py             EDIT read via unread / unread_legacy
mailroom_server.py       EDIT check_start; 127.0.0.1; /send, A2A via the gate; reads through unread_by_name
node_runner.py           EDIT MAILTO, REJECTED, auto-reply via submit; no auto-reply to system senders
terminal_node.py         EDIT type _notice only; pushed-id set; no claim or ack; reply hint names rookery send
postmaster.py            EDIT mention scan: pending only, same session, no body; system_send
relay.py                 EDIT dead-letter via system_send
mail_screen.py           NO CHANGE (release and quarantine stay human tools)
security.py              NO CHANGE (the shared token stays)
cli_up.py                EDIT line 223 names rookery send
bootstrap.py             EDIT add mail_gate.py to the zipapp
CHANGELOG.md README.md docs/QUICKSTART.md docs/COOKBOOK.md docs/STORIES.md docs/NODE_BRIEFING.md
skills/rookery/SKILL.md  EDIT MCP ack, legacy labels (FR-029)
gate_matrix.py (test helper)   NEW
test_gate_log.py test_gate.py test_gate_scan.py test_system_mail.py   NEW
test_paths_local.py test_paths_mcp.py test_paths_send_mail.py test_paths_mailroom.py test_paths_node_runner.py   NEW
test_mailroom_gate.py test_mailroom_start.py test_mailctl.py test_mcp_gate.py test_read_mail.py   NEW
test_reads.py test_mentions.py test_pane.py test_text_hints.py   NEW
```

**Structure Decision**: Keep the flat layout. `mail_gate.py` sits beside `turn_block.py`.

## Requirement Trace

| FR | Built in | Proof |
|---|---|---|
| FR-001 | `mail_gate.submit` (research D1) | `test_gate` order of checks |
| FR-002 | `turn_block._dispatch_block` → `submit` | `test_turn_block`, `test_send` with no change |
| FR-003 | MCP, `send_mail`, `/send`, A2A, node runner → `submit` | `test_paths_*` matrix (27 cases) |
| FR-004 | `system_send`; only `mail_gate`/`rookery` call `send` (D3, D4) | `test_gate_scan` AST, alias, `quarantine` and `system_send` scans; `test_pane` and `test_text_hints` text scans |
| FR-005 | `rookery.send(session_id=...)` with no default (D2) | `test_gate` `TypeError` case |
| FR-006 | loop breaker in `submit` (D10) | `test_gate` MCP loop case |
| FR-007 | `submit` returns `db-busy`/`send-failed`; callers exit non-zero | `test_gate` locked-database case |
| FR-008 | `principal_from_env`, sender choice and refusals in `submit` (D5) | `test_paths_*`; `test_gate` order of checks, `system-name`, `bad-input` |
| FR-009, FR-010 | Removed 2026-10-23 | none |
| FR-011 | no `create=True` on send paths; MCP `_register` goes | `test_paths_*` identity count before = after |
| FR-012 | `system_send`; `new_identity` refuses system names (D3) | `test_gate` system-name cases |
| FR-013 | recipient lookup in `submit`, only when the sender has a session | `test_paths_*` off-team and unknown columns; `test_gate` no-session case |
| FR-014 | `no-session` delivers to `legacy` with a log warning (D6) | `test_gate` no-session case, log warning |
| FR-015 | `mail_gate.unread`: session plus `legacy` to the reader (D11) | `test_reads` two-session case and legacy-to-name case |
| FR-016 | `unread` sender name join; log of `asked_sender` and `principal` (D11) | `test_reads` sender-name case; `test_gate` log fields |
| FR-017, FR-018 | mention scan (D12) | `test_mentions` four mention cases |
| FR-019, FR-020 | `terminal_node` notice, no ack (D13) | `test_pane` pane case with a fixture tool on `PATH` |
| FR-021 | no herdr import in `mail_gate` | `test_gate_scan` import scan |
| FR-022 to FR-024 | `mailroom_server.check_start`, `main()` and `rookery serve` (D8) | `test_mailroom_start` start cases for both |
| FR-025 | `inspect_inbound` in `submit` (D9) | `test_gate` deny-policy case |
| FR-026 | no schema change | `test_sessions_migration`; quickstart step 1 on a copy |
| FR-027 | MCP, `mailctl`, `send_mail` keep working | `test_mcp_gate`, `test_mailroom_gate`, `test_paths_*` valid column |
| FR-028 | Removed 2026-10-23 | none |
| FR-029 | docs, CHANGELOG, SKILL.md, `cli_up.py:223`, `terminal_node.py:61` | `test_pane` and `test_text_hints` text scans for `send_mail.py` hints; review |
| FR-030 | `gate.jsonl` (D15) | `test_gate` log fields |
| FR-031 | code texts | `test_gate` writes texts and runs `ste-lint` |
| FR-032 | MCP read only, `ack` tool (D11) | `test_mcp_gate` |
| FR-033 to FR-036 | Removed 2026-10-23 | none |
| FR-037 | `rookery loop reset` (D23, T041a) | `test_gate` reset case |

## Complexity Tracking

No gate needs a justification. These items go past the literal FR text:

| Item | Why the design needs it | Simpler choice and why not |
|---|---|---|
| `unread_legacy` | a reader with no identity must see `legacy` mail to its name (FR-015) | none: without it that mail is never read |
| `unread_by_name`, `ack_by_name` | HTTP has no identity, so the mailroom reads and acks by node name in any session, as today (D24) | `unread_legacy`: HTTP readers lose all mail stored in a session |
| `mailroom_server.check_start` | `rookery serve` builds its own server and would skip the start rules (D8) | copy the rules into `cli_serve.py`: two copies drift |
| `rookery loop reset` | MCP, HTTP and node agents have no hook, so FR-039 has no reset for them (D23) | none: a held loop never clears |
| `pre_store` and `quarantine_malformed` | the block path keeps `handoff-pending` and `malformed` (D1) | leave them in `turn_block`: it calls `rookery.send` and breaks FR-004 |

### FR-002 and FR-005 conflict (one fixture change)

FR-005 removes the `legacy` default of `rookery.send()`. Two test files call `send()` with no session
ID: `test_identity.py:181` (a spec 001 migration fixture that writes an old `legacy` row) and
`test_held_quarantine.py` (12 calls, from before 0.7). Each call gets `session_id="legacy"`. That is the
value the default gave, so the row and the assert do not change. All other spec 001 tests and
`test_send.py` already pass `session_id`, and they stay unchanged. The PR states this exception to FR-002.
