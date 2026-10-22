---
type: Research
task: 001-rookery-init-turn-block
feature: rookery init and the rookery turn block
updated: 2026-10-15
revision: 2 (after clarify session 2026-10-15 and constitution v1.2.1)
---

# Research: `rookery init` and the rookery turn block

Each decision names its evidence. "Code" means a file:line in this repo. "Docs" means the
Claude Code hooks page, <https://code.claude.com/docs/en/hooks>, read on 2026-10-15. "Design" means
`okf/design/rookery-sessions.md`.

Revision 2 replaces D3 (remote mode), D5 (node identity), D6 (workspace path) and D10 (pane push), and
adds D17 to D22. The other decisions did not change.

Revision 3 follows `/speckit-analyze` and a user decision of 2026-10-15: Rookery is mail, not a phone
call. It replaces D8 (no stop gate), D10 (push only to an idle agent) and D11 (a postmaster folder). It
revises D3 (two entry points), D13 (lock place), D15 (outcomes) and D16 (database timeout).

Revision 4 follows the second `/speckit-analyze`. Only the Stop hook and `rookery turn` write
`HANDOFF.md` (D11). The Stop hook resolves read-only (D16, D20). A session name is never empty (D18).
The postmaster runs without a roster (D10). A failed block gives the sender a notice (D23).

## D1. Where the Stop hook gets the final message

- **Decision**: Read the `last_assistant_message` field from the Stop hook's stdin JSON. Do not
  read `transcript_path`.
- **Rationale**: The docs say that this field holds the text of Claude's final reply. The docs also
  say that the transcript file "is written asynchronously and may lag". A parser that reads the
  transcript can miss the block of the current turn, and then it sends nothing (R6 fails quietly).
- **If the field is absent** (an older Claude Code): the hook logs `no-message-field` and sends
  nothing. It never parses stale text.
- **Alternatives considered**: Tail of the transcript JSONL. Rejected because of the documented lag.

## D2. How the parser finds the last block

- **Decision**: A line-based fence scanner. An opening fence is three or more backticks or tildes
  with the info string `rookery` (exact, after trim). The closing fence uses the same character and
  is at least as long. The scanner keeps the last complete block. An unclosed fence is not a block.
- **Rationale**: A regex over the whole text breaks when `say` holds backticks. The line rule
  matches CommonMark, so what the agent sees as a block is what the parser reads.
- **Alternatives considered**: One regex. A Markdown library (a new dependency, constitution V).

## D3. The send path for every harness (revised in revision 3)

- **Decision**: `turn_block.py` has two entry points. `dispatch_text(text, ctx)` finds the last block in
  a turn (D2) and gives it to `dispatch_block(raw, ctx)`. `dispatch_block()` validates and acts. The Stop
  hook and `rookery turn` call `dispatch_text()`. The outbox scan calls `dispatch_block()`, because an
  outbox file is raw JSON with no fence. For a valid block, `dispatch_block()` calls `rookery.send()`
  (`rookery.py:110`) with `sender=<sender agent ID>`, `recipient=<recipient agent ID>`, `topic=task`,
  `body=<the block as compact JSON>` and the sender's session ID (D17, D18).
- **Rationale**: `rookery.send()` is the canonical row writer. Every existing wrapper uses it (code:
  `send_mail.py:15`, `rookery_mcp.py:78`, `mailroom_server.py:427`). It applies `ROOKERY_HOLD`
  (`rookery.py:103`), so RQ-1.17 holds with no new code.
- **Send failure**: only the Stop hook and `rookery turn` put a block back for a retry. They write it to
  the identity's `outbox/` as `<TASK-ID>-<ts>.json`. The outbox scan never puts a file back. It marks the
  file `failed` with the reason `send-failed`. So a database that is down cannot cause a resend loop.
- **Remote mailroom (FR-035, RQ-1.27)**: when `ROOKERY_URL` exists, no adapter sends. The Stop hook and
  `rookery turn` write the block to the identity's `outbox/failed/` as `<TASK-ID>-<ts>.json` with the
  reason `remote-unsupported`. The outbox scan marks the file `failed` with that reason. All adapters log it.
- **Alternatives considered**: Import `rookery_mcp._send`. Rejected: that module needs the `mcp`
  package, and a hook must run with the standard library only. One `dispatch(text)` for all adapters.
  Rejected: an outbox file has no fence, so the scan would always find `no-block`.

## D4. How a malformed block becomes a quarantined row

- **Decision**: `send(..., hold=True)` and then `quarantine(conn, id, "malformed",
  decided_by="turn-block-parser", note=<what is wrong>)` in one transaction.
- **Rationale**: `quarantine()` accepts only a `held` row (`rookery.py:219`, `_decide`). `malformed`
  is already in `QUARANTINE_REASONS` (`rookery.py:205`). `quarantine()` already sends the JSON bounce
  from the system sender `rookery`, which is never held.
- **Recipient when `to` is missing or not a string**: the row uses the recipient `rookery`. The
  bounce goes to the sender in all cases.
- **The same path serves `wrong-recipient` and `off-team`** (D22).

## D5. Identity (replaced: see D17 to D21)

Revision 1 baked a node name into each hook command. Clarify replaced that with Rookery sessions and
session-scoped identities (constitution IX).

## D6. Where the database and the workspace are (revised)

- **Database**: `ROOKERY_DB` when set, else `~/Documents/code/rookery/rookery.db`. This is the same
  default as `rk` (retired on 2026-10-21; use `rookery`) (`~/.local/bin/rk:6`). The `rookery.py` default ("next to the module") is wrong inside
  the zipapp, so the new code never uses it.
- **Workspace**: `$ROOKERY_ROOT/agents/<session>/<name>/` when `ROOKERY_ROOT` exists (phase 2 sets it),
  else `~/.rookery/projects/<project>/agents/<session>/<name>/`. Phase 1 creates only `runs/<TASK-ID>/`,
  `outbox/` and `state/`, and only when it needs them (constitution VII). A new Rookery session therefore
  starts with an empty workspace.
- **`<session>` and `<name>` in paths** are the session name and the agent name. Both follow the same
  safe-name rule as `task` (data-model.md), so a path can never leave the root.

## D7. What "unread" means

- **Decision**: Unread = rows with `recipient=<agent ID>` and `status='pending'`. This is the query in
  `postmaster.py:41` `has_mail()` and `rookery.py:122` `fetch_undelivered()`, with the agent ID in place
  of a name (D17).
- `held` and `quarantined` rows are not pending, so they are not unread (spec assumption).

## D8. No stop gate (replaced in revision 3)

- **Decision**: The Stop hook never blocks a stop. It dispatches the block, then writes
  `state/turn.json` = `{"state": "idle", "since": <ts>}` in the identity's workspace. The prompt hook writes
  `{"state": "busy", "since": <ts>}`. The session-start hook writes `idle`.
- **Rationale**: User, 2026-10-15: Rookery is mail, not a phone call. A phone call interrupts. Mail waits
  until the reader chooses to read it. A gate forced the agent to read mail before it could end a turn,
  even in a chat with the user. The idle push (D10) delivers the mail when the turn ends, so the gate
  added nothing. This supersedes C7 and changes RQ-1.14.
- **A bounce**: a bounce is mail to the sender. The sender is idle after its stop, so the next postmaster
  loop pushes the bounce notice.
- **An interrupted turn** has no Stop event, so the state stays `busy`. The prompt notice of the next turn
  still shows the mail. When that turn ends, the push happens.

## D9. Context injection (revised for opt-in)

- **UserPromptSubmit**: plain stdout on exit 0 becomes context. Empty stdout adds nothing (docs).
  The hook prints one line only when unread mail exists, else zero bytes.
- **SessionStart**: plain stdout becomes context. It also fires after compaction with
  `source: "compact"` (docs). The hook resolves the session and the identity (D20), then prints who the
  agent is, its newest `HANDOFF.md` and the unread summary. It appends `export ROOKERY_SESSION=<id>` and
  `export ROOKERY_NODE=<agent ID>` to `CLAUDE_ENV_FILE` (docs: available to SessionStart), so `rookery
  inbox` in the agent's shell resolves the same identity.
- **No current Rookery session**: every hook prints nothing and writes nothing (FR-027, SC-010).

## D10. Pushing mail into an idle session (replaced in revision 3)

- **Decision**: The postmaster gets a new step. It acts on each identity in an open session with pending
  mail above its last pushed ID. It pushes only when the identity's `state/turn.json` reads `idle`. It
  never pushes into a running turn. It looks for a binding whose kind has a wake command in the
  configuration (D19). It runs that command with `{value}` and `{text}` filled in, as an argument list,
  never through a shell.
- **No push in these cases**: no wake binding → log `no-wake-binding`. No `state/turn.json` (for example a
  Codex agent without hooks) → log `no-idle-state`. The mail waits for the prompt notice or `rookery inbox`.
  Phase 1 has no headless wake for identities: `postmaster.wake()` needs an engine (`postmaster.py:48`), and
  an identity has none. Existing postmaster nodes keep their own wake.
- **Duplicate push guard**: the postmaster keeps the last pushed mail ID of each identity in
  `<root>/postmaster/pushed/<agent_id>.json`. It reads `state/turn.json` and never writes it.
- **Rationale**: Constitution IX: herdr and tmux are entries in the default configuration file, not code.
  Constitution VII: each folder has one writer (D11). The notice holds a pointer, never the mail body
  (mail-flow §3). Mail, not a phone call (D8).
- **Alternatives considered**: Revision 1 called `herdr` from code (rejected by IX). A per-kind idle
  check command (more configuration for no gain: the hooks already know when a turn ends).
- **Start** (revision 4): `--nodes` becomes optional. Without a roster, `python3 postmaster.py` runs only
  the outbox scan and this wake step. With `--nodes`, the roster steps also run, as before.

## D11. The outbox scan (RQ-1.9) (revised in revision 3)

- **Decision**: A postmaster step scans `agents/*/*/outbox/*.json` under each project root. `outbox/` is a
  drop box: the agent writes a file there, and the postmaster takes it. For each file the postmaster
  renames it into `<root>/postmaster/outbox/<session>/<name>/sending/` (the claim). It calls
  `dispatch_block()` (D3). Then it renames the file into `sent/` or `failed/` of that folder as
  `<TASK-ID>-<file name>`, with a `.result.json` beside it that holds `task`, `outcome`, `msg_id` and
  `reason`. The sender is the identity with that session name and agent name.
- **Restart rule**: at start, a file in `sending/` moves to `failed/` with the note "interrupted:
  check the mailroom before you send again". The scan never resends it. This gives at-most-once
  delivery and never a silent loss.
- **One writer per folder** (constitution VII): the agent and its hooks write the agent's workspace. The
  postmaster writes only `<root>/postmaster/`. The claim rename is the hand-over point.
- **No `HANDOFF.md`** (revision 4): the scan never writes `HANDOFF.md`. An outbox file is not the end of
  a turn (FR-009), and the postmaster writes only `<root>/postmaster/`. The Stop hook writes `HANDOFF.md`
  before it sends, so a block that it puts back is already in `HANDOFF.md`. This rule also stops an old
  put-back block from overwriting a newer `HANDOFF.md`.
- **Rationale**: One daemon, not a new one (constitution VI). Rename is atomic on one file system.

## D12. Size limits (RQ-1.3)

- **Decision**: A block may be at most 4,096 bytes as compact JSON. `say` may be at most 1,200
  characters. A `read` entry may be at most 256 characters, one line, with no `..` part, and must stay
  inside the Rookery root after resolution.
- **Source**: The screener envelope limit is ~2,050 tokens for tev1 (register R16;
  `okf/design/screener-chain.md`). The envelope also holds scope cards and acceptance criteria. At about
  4 characters per token, 4,096 bytes is about 1,000 tokens, which leaves half of the envelope for the
  other parts. These are input-size limits on agent mail, not model token caps (constitution IV).
  `turn_block.py` states this source in a comment next to the constants.

## D13. How `rookery init` merges hooks (revised)

- **Decision**: Load `.claude/settings.json`. For each of `SessionStart`, `UserPromptSubmit`, `Stop`,
  find a handler whose `command` starts with `rookery hook `. If it is equal to the new command, change
  nothing. If it differs, replace that one handler in place. If there is none, append a new group
  `{"hooks": [ ... ]}` at the end of the event list. Write only when the content changed, through a
  temporary file and `os.replace`, under an `fcntl` lock on `<root>/locks/init.lock`. The lock file is
  under the Rookery root, so the host repo gets no new file (FR-023). The context file uses the same rule.
- **Hook command**: `rookery hook <event> --project <project>`. It holds no agent name and no session:
  the hook resolves both at run time (D20). The project only selects the Rookery root.
- **Rationale**: Hooks for one event run in parallel and merge across settings levels (docs). No write
  when nothing changed gives byte-identical re-runs (SC-004). Unparsable JSON stops init before any write.
- **Context file**: the same rule as Spec Kit `upsert_context_section()`.
- **Harness to file**: `claude` → `CLAUDE.md` and settings. `codex`, `pi`, `omp` → `AGENTS.md` only.
  Codex reads `AGENTS.md` (design `rookery-stays-in-context.md`). For pi and omp this is UNTESTED: read
  the loader of each before a live run (constitution III).
- **Init output** (FR-014): says that open agent sessions do not join. It tells the user to start a new
  Rookery session with new panes and agents.

## D14. Hook commands and packaging

- **Decision**: `rookery` is the zipapp at `~/.local/bin/rookery`, built by `bootstrap.py`. The new
  modules go into its file list (`bootstrap.py:24`). The managed-section template and the default
  configuration go into `_DATA_FILES`.
- **Rationale**: One command on PATH, with no repo path in the host's `settings.json`. The zipapp copies files
  at build time, so the task list includes a rebuild and a check of the installed copy.

## D15. Logs

- **Decision**: Each adapter writes one JSON line per outcome to `<project root>/logs/turn-block.jsonl`
  through `logging.handlers.RotatingFileHandler` (1 MB, 3 files). Fields: `ts`, `session`, `agent_id`,
  `name`, `task`, `adapter`, `outcome` (`sent` | `no-block` | `quarantined` | `handoff-pending` |
  `send-failed` | `no-message-field` | `no-session` | `remote-unsupported` | `db-busy`), `reason`,
  `msg_id`. Identity events (`created`, `bound`, `binding-moved`, `linked`, `inbox-read`) go to the same log.
- **The postmaster** writes its own log, `<root>/postmaster/postmaster.jsonl`, with the same fields and
  rotation. It holds the outbox outcomes and the push events (`pushed`, `no-wake-binding`,
  `no-idle-state`). Two processes never rotate one file, and each folder has one writer (D11).
- **Rationale**: Constitution VI. The `inbox-read` lines are the audit trail of FR-031.

## D16. Hook failure

- **Decision**: Each hook catches every exception, logs it with its context, and exits 0. The Stop
  hook first writes the block to `outbox/` when the send fails, so the outbox scan can try again (D3).
- **Database wait**: hooks open the database through `turn_block.open_db()`: `sqlite3.connect(path,
  timeout=2)` and `PRAGMA busy_timeout=2000`. The 2 s value keeps a hook far below the 30 s hook limit
  of Claude Code (docs). On a lock timeout the hook logs `db-busy` and exits 0. A Stop hook with a
  valid block writes it to `outbox/` first, so the block is not lost.
- **Read-only resolve** (revision 4): the Stop hook calls `resolve(..., create=False)`. The
  session-start hook already created the identity and its bindings. In WAL mode a read does not wait
  for a writer, so the Stop hook finds the workspace while another connection writes. When it finds a
  session but no identity, it keeps a valid block in `<root>/unresolved/<TASK-ID>-<ts>.json` and logs
  `no-identity`. `rookery turn` has no session-start hook, so it resolves with `create=True`. On
  `db-busy` it exits 4. The caller still has the turn text and can run `rookery turn` again.
- **Rationale**: FR-022. A hook must not crash or hang the agent's session.

## D17. Mail rows address agent IDs, not names (new)

- **Decision**: In rows that the new code writes, `sender` and `recipient` hold agent IDs. `dispatch_block()`
  resolves `to` (a name) to an agent ID inside the sender's session. The block in `body` keeps the names.
- **Rationale**: Names are unique only inside a session (FR-025). The existing readers select by
  `recipient` (`rookery.py:122`, `postmaster.py:41`, `node_runner.py`). With agent IDs there, these
  readers stay correct with no change, and two `researcher` agents in two sessions never share mail.
  `quarantine()` sends its bounce to `m["sender"]`, which is then the right agent ID (`rookery.py:249`).
- **ID format**: agent `ag-` + 10 hex chars, session `rs-` + 8 hex chars, from `secrets.token_hex`.
  Insert checks uniqueness. Never reused (FR-025, FR-032).
- **Legacy**: rows that `rk` (retired on 2026-10-21; use `rookery`), MCP and `send_mail.py` write keep plain names and belong to `legacy`.
- **Alternatives considered**: Names in `recipient` plus a session filter in every reader. Rejected:
  it changes `fetch_undelivered`, `has_mail`, `claim` and `node_runner`, and a missed reader leaks mail
  across sessions.

## D18. Schema change (new)

- **Decision**: Additive only, in `rookery.init_db` (`rookery.py:50`):
  - table `rookery_sessions(session_id PK, name UNIQUE, state, created_at, ended_at)`. A session without
    `--name` gets its session ID as its name, so each session has a folder name. `UNIQUE` also covers
    ended sessions, so Rookery never reuses a session name (revision 4);
  - table `identities(agent_id PK, session_id, name, previous_id, created_at, UNIQUE(session_id, name))`;
  - table `bindings(session_id, owner_type, owner_id, kind, value, created_at)` with
    `UNIQUE(session_id, owner_type, kind, value)`; `owner_type` is `session` or `identity`;
  - column `inbox.session_id TEXT DEFAULT 'legacy'`, and one `legacy` session row.
- `send()` gets an optional `session_id` argument. `quarantine()` passes the session of the original row
  to its bounce. These are the only changes to existing functions.
- **Proof** (constitution I, FR-029): the migration test runs on a COPY of the live database, as R3 did.
- **`nodes` table**: unchanged. The session-start hook also calls `register_node(agent_id, kind="pane",
  lifecycle="persistent")`, so the roster lists the agent. The postmaster does not wake it
  through this row (D10).

## D19. The configuration file (new)

- **Decision**: `~/.rookery/config.json`. When it does not exist, Rookery copies `rookery_config.default.json`
  (a data file in the zipapp) there on first use. The default content:

  ```json
  {"identity_binding_sources": {"HERDR_PANE_ID": "herdr-pane", "TMUX_PANE": "tmux-pane"},
   "session_binding_sources": {"HERDR_WORKSPACE_ID": "herdr-workspace"},
   "wake": {"herdr-pane": [["herdr","agent","prompt","{value}","{text}"]],
            "tmux-pane": [["tmux","send-keys","-t","{value}","-l","{text}"],["tmux","send-keys","-t","{value}","Enter"]]}}
  ```
- The harness session ID from the hook input always gives one more binding, of kind `claude-session`
  (or the harness name).
- **Rationale**: Constitution IX. A new tool is a new entry in this file. This session's environment
  shows the herdr variable names (`HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`). The tmux variable is `TMUX_PANE`.
  No tmux pane ran in this session, so this plan marks that name UNTESTED.

## D20. Resolution and opt-in (new)

- **Decision**: one function, `identity.resolve(env, hook_input)`, used by every hook, `rookery turn`,
  `rookery inbox` and `rookery peek`:
  1. Session: `ROOKERY_SESSION` (name or ID), else a session binding from `session_binding_sources`.
     None → return "no session". Each caller then does nothing (SC-010).
  2. Identity in that session: first `ROOKERY_NODE` (name or agent ID). A new name creates the identity.
     Rookery logs and skips an unknown ID. Then an identity binding. Last, a new identity named
     `<harness>-<4 hex>` with every presented binding.
- **Binding move**: inside one session, `ROOKERY_NODE` can name identity B while the session presents a
  binding of identity A. Then the binding moves to B, and the log records `binding-moved`.
- **Read-only mode** (revision 4): `resolve(..., create=False)` creates no identity and moves no
  binding. The Stop hook uses it (D16).
- **Module name**: `rookery_identity.py`, because `identity.py` is the existing host keypair module.

## D21. Carry-over (new)

- **Decision**: `rookery session new --from <session>` creates the new session. Then, for each agent name
  in the old session, it creates an identity with that name and `previous_id`. It also copies the newest
  `runs/*/HANDOFF.md` of that name into the new workspace. An agent claims a pre-created identity with
  `ROOKERY_NODE=<name>`.
- `rookery identity link --previous <id>` sets `previous_id` on the resolved identity by hand.

## D22. `wrong-recipient` and `off-team` (new)

- **Decision**: In `dispatch_block()`, after validation:
  1. `to` names an identity in the sender's session → send.
  2. `to` names an identity only in other sessions → quarantine `off-team`, bounce, no `suggest`.
  3. No session has that name → quarantine `wrong-recipient`; `suggest` = up to three names from the
     sender's session, from `difflib.get_close_matches` (standard library).
- Both use the D4 path (`send(hold=True)` + `quarantine()`), with `decided_by="turn-block-parser"`.
  The row's `recipient` holds the raw `to` text, which matches no agent ID, so no reader fetches it.

## D23. A notice for a failed block (new in revision 4)

- **Decision**: The session-start hook and the prompt hook list the failed blocks of the identity. They
  read `<root>/postmaster/outbox/<session>/<name>/failed/*.result.json` and
  `agents/<session>/<name>/outbox/failed/*.json`. For each block that `state/failed-seen.json` does not
  name, they print one line, then add its file name to that file. The hooks only read the postmaster
  folder, so each folder keeps one writer. Line:
  `[rookery] Your block for TASK-12 failed (send-failed). It is kept in <path>. Fix the cause, then send the block again.`
- **Rationale**: FR-022 keeps each block, but a kept block that no one sees is lost for the agent. The
  agent thinks that its mail went out. Mail, not a phone call: the notice waits for the next prompt.
- **Alternatives considered**: A bounce row in the mailroom. Rejected: a database that is down caused
  the failure, so a row can fail too. A log line only. Rejected: agents do not read the log.
