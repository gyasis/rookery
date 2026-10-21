---
type: DataModel
task: 001-rookery-init-turn-block
updated: 2026-10-15
revision: 2 (sessions, identities, bindings)
---

# Data model: `rookery init` and the rookery turn block

The schema change is additive (research D18). Run the migration on a COPY of the live database before
merge (constitution I).

## Safe name rule

`task`, a session name and an agent name each match `[A-Za-z0-9._-]{1,64}` and are not `.` or `..`.
Each one can become a folder name, so this rule keeps every path inside the Rookery root.

## Rookery session (FR-032, RQ-1.24) — table `rookery_sessions`

| Column | Rule |
|---|---|
| `session_id` | `rs-` + 8 hex, generated, never reused |
| `name` | safe name, unique |
| `state` | `open` or `ended` |
| `created_at`, `ended_at` | ISO-8601 UTC |

One row `legacy` (`state=open`) holds all rows from before phase 1. `end` sets `state=ended`; nothing
is deleted.

## Identity (FR-025, FR-029, RQ-1.20, RQ-1.25) — table `identities`

| Column | Rule |
|---|---|
| `agent_id` | `ag-` + 10 hex, generated, never reused |
| `session_id` | the session it belongs to |
| `name` | safe name, unique inside the session |
| `previous_id` | agent ID in an older session, or NULL (FR-036) |
| `created_at` | ISO-8601 UTC |

## Binding (FR-026, RQ-1.21) — table `bindings`

| Column | Rule |
|---|---|
| `session_id` | the session the binding is valid in |
| `owner_type` | `session` or `identity` |
| `owner_id` | a `session_id` or an `agent_id` |
| `kind`, `value` | opaque strings; Rookery never interprets them |

`UNIQUE(session_id, owner_type, kind, value)`: inside one session a value belongs to one owner. A
session binding has `session_id` = its own session.

## Mail row (existing `inbox` table + `session_id`)

| Column | Value from the new code |
|---|---|
| `sender` | sender agent ID (research D17) |
| `recipient` | recipient agent ID; for `wrong-recipient`/`off-team`/`malformed`, the raw `to` text (or `rookery`) |
| `topic` | `task` (NULL when not valid) |
| `body` | the block as compact JSON, keys sorted (malformed: the raw block text) |
| `session_id` | the sender's session (FR-034); bounces inherit it |
| `status`, `verdict`, `reason`, `decided_by`, `note` | from `send()` and `quarantine()` |

Rows that `rk`, MCP or `send_mail.py` write keep names and get `session_id='legacy'`.

## Rookery block (FR-002 to FR-004, RQ-1.2, RQ-1.3)

Contract: [contracts/turn-block.schema.json](contracts/turn-block.schema.json).

| Field | Type | Mandatory | Rule |
|---|---|---|---|
| `to` | string | yes | safe name; an agent name in the sender's session |
| `task` | string | yes | safe name (it becomes a folder name) |
| `kind` | string | yes | `ask`, `answer`, `decline`, `handoff`, `status`, `recruit` (C2) |
| `say` | string | yes | 1–1,200 chars |
| `status` | string | yes | `done` or `in-progress` |
| `thread` | string | no | carried unchanged (C4) |
| `read` | list of string | no | each: one line, ≤ 256 chars, inside the Rookery root, no `..` |
| `self_check` | object | no | `task_complete` bool, `gap` string, `gap_in_my_scope` bool, `evidence` string |
| `artifacts` | list of object | no | each `{path: string, note: string}` |

- Compact JSON is at most 4,096 bytes (research D12).
- An unknown top-level field makes the block malformed, for example the retired `call` object (C1).
- `veto`, `takeover` and `bounce` make the block malformed (C2).

### Outcome of one block (FR-005 to FR-007, FR-030, FR-033, FR-035)

| Input | Mail | `HANDOFF.md` |
|---|---|---|
| no current Rookery session | none (hook does nothing) | unchanged |
| `ROOKERY_URL` set | none; block in `outbox/failed/`, reason `remote-unsupported` | unchanged |
| no block | none | unchanged |
| malformed | 1 row quarantined `malformed` + 1 bounce | unchanged |
| valid, `to` only in another session | 1 row quarantined `off-team` + 1 bounce | rewritten |
| valid, `to` in no session | 1 row quarantined `wrong-recipient` + 1 bounce with `suggest` | rewritten |
| valid, `kind=handoff`, `status=in-progress` | none (outcome `handoff-pending`) | rewritten, `status: in-progress` |
| valid, `kind=handoff`, `status=done` | 1 row (`pending`, or `held` with `ROOKERY_HOLD=1`) | rewritten, `status: done` |
| valid, other kind, either status | 1 row | rewritten |
| Stop hook: a session but no identity | none; block in `<root>/unresolved/`, reason `no-identity` | unchanged |

The `outbox` adapter gives the same mail outcomes, but it never writes `HANDOFF.md` (research D11).

## Workspace on disk (FR-009, FR-011, FR-023)

```text
<root>/                                ROOKERY_ROOT, else ~/.rookery/projects/<project>/
  logs/turn-block.jsonl                hooks and CLI; rotated, 1 MB x 3 (research D15)
  locks/init.lock                      rookery init lock (research D13)
  unresolved/<TASK-ID>-<ts>.json       a valid block of a Stop with no identity (research D16)
  agents/<session>/<name>/             written by the agent and its hooks only
    runs/<TASK-ID>/HANDOFF.md          rewritten by the Stop hook or rookery turn for a valid block
    outbox/<file>.json                 drop box: the agent writes, the postmaster takes
    outbox/<TASK-ID>-<ts>.json         a block that the Stop hook put back after a send failure
    outbox/failed/<TASK-ID>-<ts>.json  a block kept with the reason remote-unsupported (FR-035)
    state/turn.json                    {"state": "idle"|"busy", "since": ts} (research D8)
    state/failed-seen.json             failed blocks that a notice showed (research D23)
  postmaster/                          written by the postmaster only (research D10, D11)
    postmaster.jsonl                   outbox outcomes and pushes; rotated, 1 MB x 3
    outbox/<session>/<name>/sending/   claimed outbox files
    outbox/<session>/<name>/sent/      <TASK-ID>-<file> + .result.json (kept)
    outbox/<session>/<name>/failed/    <TASK-ID>-<file> + .result.json (kept)
    pushed/<agent_id>.json             {"last_pushed_id": n}
~/.rookery/config.json                 binding sources + wake commands (research D19)
```

### `HANDOFF.md` (RQ-1.7, RQ-1.8)

```markdown
---
type: Handoff
task: <task>
agent: <name>
agent_id: <agent ID>
session: <session name>
updated: <UTC ISO-8601>
status: <done | in-progress>
---
## Last thing I did
<say>
## State now (what exists, with paths)
- <artifact path> — <note>      (one line per artifact, then one line per read pointer)
## What is left
<self_check.gap, or "Nothing stated.">
## Open threads
<thread> with <to> (<kind>)     (or "None.")
```

The path holds the task ID and the frontmatter holds `task:`, so `rg -l <TASK-ID> <root>` finds it. Phase 1
never writes the design's third status, `blocked`, because no block field carries it.

### Outbox file states (FR-011)

`new` (agent `outbox/*.json`) → `claimed` (postmaster `sending/`) → `sent` (`sent/`) or `failed`
(`failed/`). A file in `sending/` at scan start → `failed` with the note "interrupted" (research D11).
A send failure in the scan → `failed` with `send-failed`. The scan never puts a file back (research D3).
Each `.result.json` holds `task`, `outcome`, `msg_id` and `reason`, so a search for the task ID finds it.

## Managed section (FR-012)

The text between `<!-- ROOKERY START -->` and `<!-- ROOKERY END -->`. The template is
`rookery_section.md` with the fields `{project}` and `{root}`. It holds no agent name: the session-start
hook tells each agent who it is. It must pass `ste-lint` (FR-024).

## Hook registration (FR-013)

```json
{"hooks": [{"type": "command", "command": "rookery hook stop --project <project>"}]}
```

Init appends one such group to each of `SessionStart`, `UserPromptSubmit` and `Stop`. A handler is
Rookery's when its `command` starts with `rookery hook `.
