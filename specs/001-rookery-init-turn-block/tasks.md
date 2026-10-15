---
type: Tasks
task: 001-rookery-init-turn-block
updated: 2026-10-15
---

# Tasks: `rookery init` and the rookery turn block

**Input**: the design documents in `specs/001-rookery-init-turn-block/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/) and [quickstart.md](quickstart.md).

**Tests**: MANDATORY (constitution II). Each requirement RQ-1.1 to RQ-1.27 and each risk R5 to R8 gets the
test that proves it, in this PR. Use fixtures, not mocks. Each test uses a temporary database and a
temporary Rookery root, as `test_held_quarantine.py` does. Write each test first and see it fail.

**Layout**: a flat Python repo. All paths start at the repo root. Use the standard library only.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: the task can run in parallel. It uses a different file and has no open dependency.
- **[Story]**: the user story of the task (US1 to US5).

## Rules for every task

- Read the research decision that the task names (Dn in [research.md](research.md)) before you write code.
- Each hook catches every exception, logs it, and exits 0 (D16, FR-022).
- Rookery is mail, not a phone call. No hook blocks a stop. The postmaster pushes only to an idle agent
  (D8, D10, FR-017, FR-018).
- Each folder has one writer (constitution VII). The agent and its hooks write `agents/<session>/<name>/`.
  The postmaster writes only `<root>/postmaster/`.
- `turn_block.py` must not import `rookery_identity` at module level. Import it inside the functions that
  need it. `rookery_identity.py` imports `turn_block` for `log_event()` and the path helpers.
- The database path is `ROOKERY_DB`, else `~/Documents/code/rookery/rookery.db` (D6). Never use the
  default path of `rookery.py`.
- Each text that an agent or the user reads must pass `ste-lint --max-score 2.0 --glossary ste-glossary.txt`
  (FR-024). This includes the section, the bounce notes, the notices and the errors.

---

## Phase 1: Setup

**Purpose**: the fixtures and data files that later phases need.

- [x] T001 [P] Copy `.claude/settings.json` of this repo to `fixtures/settings/devkid.json` without change. Tests use this copy as the dev-kid settings file (US3).
- [x] T002 [P] Create `rookery_config.default.json` with the exact content in research D19. Mark `TMUX_PANE` as UNTESTED in a `_comment` key.
- [x] T003 [P] Create `fixtures/turns/README.md`. List each fixture file of T013 and T021 with its expected outcome from the outcome table in [data-model.md](data-model.md).

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: the schema, paths, log, sessions, identities, bindings and resolution. Every story calls
`resolve()` and writes rows with agent IDs.

### Tests first

- [x] T004 [P] Write `test_sessions_migration.py` (FR-029, FR-034, constitution I). Use `ROOKERY_DB` when it exists. Else build an old database from `schema.sql` of commit `e2bbed5` with sample rows. Check these items:
  - The tables `rookery_sessions`, `identities` and `bindings` exist.
  - The column `inbox.session_id` exists, and each old row reads `legacy`.
  - One `legacy` session row exists.
  - Old column values stay identical.
  - A second `init_db()` changes nothing.
  - `rookery.fetch_undelivered(conn, <old node name>)` returns the same rows as before.
- [x] T005 [P] Write `test_identity.py` (RQ-1.20, RQ-1.21, RQ-1.22, R8, FR-025 to FR-027, FR-032, SC-009, SC-011). Check these cases:
  - Session IDs match `rs-` + 8 hex. Agent IDs match `ag-` + 10 hex.
  - No ID repeats in 1,000 creations.
  - A name can repeat across sessions, but not inside one session.
  - Two agent sessions with different `HERDR_PANE_ID` values in one Rookery session get two identities.
  - The same pane in a new Rookery session gets a new agent ID and 0 old mail.
  - Without `ROOKERY_SESSION` and without a session binding, the result is "no session" and no row changes.
  - `ROOKERY_SESSION` works by name and by ID.
  - A `herdr-workspace` session binding selects the session.
  - `ROOKERY_NODE` with a new name creates the identity.
  - An unknown `ROOKERY_NODE` ID writes a log line, and resolution continues with the bindings.
  - Identity A holds a binding and `ROOKERY_NODE` names B. The binding moves to B, and the log shows `binding-moved`.
  - Without `ROOKERY_NODE`, the agent joins the identity that holds its binding.
  - A new identity gets the name `<harness>-<4 hex>` and every presented binding, `claude-session:<id>` too.
  - The code rejects unsafe names: `..`, `a/b` and a name of 65 chars.
  - A session without `--name` gets its session ID as its name. The name of an ended session cannot be used again (D18).
  - `resolve(create=False)` creates no identity and moves no binding (D16, D20).

### Implementation

- [x] T006 Edit `rookery.init_db()` in `rookery.py` (D18). Delete nothing. Add these items:
  - `CREATE TABLE IF NOT EXISTS` for `rookery_sessions`, `identities` and `bindings`, with the columns and `UNIQUE` rules of D18.
  - The column `inbox.session_id TEXT DEFAULT 'legacy'`, with the same `PRAGMA table_info` check that the screening columns use.
  - The `legacy` session row, with `INSERT OR IGNORE`.
- [x] T007 Edit `rookery.send()` and `rookery.quarantine()` in `rookery.py` (D18). `send()` gets `session_id=None`, and `None` writes `legacy`. `quarantine()` reads the `session_id` of the original row and gives it to the bounce. Change nothing else. Then run `python3 test_held_quarantine.py` and `python3 test_sessions_migration.py`. Both must pass.
- [x] T008 Add the shared helpers to `turn_block.py` (D6, D15):
  - `SAFE_NAME`: the regex `[A-Za-z0-9._-]{1,64}`, and a check that rejects `.` and `..`.
  - `db_path()`.
  - `project_root(project)`: `ROOKERY_ROOT`, else `~/.rookery/projects/<project>/`.
  - `workspace(root, session_name, agent_name)`: `<root>/agents/<session>/<name>/`, made on demand.
  - `log_event(root, stream="turn-block", **fields)`: one JSON line through `RotatingFileHandler(maxBytes=1_000_000, backupCount=3)`. `turn-block` writes `<root>/logs/turn-block.jsonl`. `postmaster` writes `<root>/postmaster/postmaster.jsonl`. Use the fields of D15.
  - `open_db()`: `sqlite3.connect(db_path(), timeout=2)`, `PRAGMA busy_timeout=2000`, WAL, `row_factory`, then `rookery.init_db()` (D16).
  - `write_json_atomic(path, obj)`: a temporary file in the same folder, then `os.replace`.
- [x] T009 Create part 1 of `rookery_identity.py` (D17, D19):
  - `load_config()` reads `~/.rookery/config.json`. When the file does not exist, it first copies `rookery_config.default.json` there. Inside the zipapp, read the default through the module loader, as `rookery._load_schema()` does.
  - Session functions: `new_session(conn, name=None, binds=())`, `list_sessions`, `get_session(conn, name_or_id)` and `end_session`. `end_session` sets `state=ended` and deletes nothing. A session without a name gets its session ID as its name (D18).
  - Identity functions: `new_identity(conn, session_id, name, previous_id=None)`, `list_identities` and `get_identity`.
  - IDs come from `secrets.token_hex`. The insert checks that the ID is new, and tries again if not.
- [x] T010 Create part 2 of `rookery_identity.py` (D20, FR-026, FR-027):
  - `bindings_from(env, hook_input, config)` returns `kind:value` pairs from `identity_binding_sources`, plus `claude-session:<session_id>` from the hook input.
  - `bind(conn, session_id, agent_id, kind, value)` moves a value that another identity holds, and logs `binding-moved`.
  - `resolve(conn, env, hook_input, root, create=True)` follows the exact order of D20. With `create=False` it creates no identity and moves no binding (D16). It returns `None` and logs `no-session`, or it returns `(session_row, identity_row, bindings)`.
  - Log `created` and `bound` through `turn_block.log_event()`.
  - Run `python3 -m unittest test_identity -v`. All cases must pass.
- [x] T011 Create `cli_rookery_session.py` with the `session` and `identity` commands of [contracts/cli-and-hooks.md](contracts/cli-and-hooks.md). Leave `--from` and `identity link` for T026. With `--json`, each command prints one JSON object.
- [x] T012 Register the `session` and `identity` subcommands in `rookery_cli.py`, next to `up` and `approve`. Add CLI cases to `test_identity.py`. Each case runs `python3 rookery_cli.py …` as a subprocess and parses the JSON:
  - `session new --name alpha`, then `session list`, `session show alpha` and `session end alpha`.
  - After `session end`, the counts of `identities`, `bindings` and `inbox` rows for `alpha` do not change, and `state` reads `ended` (FR-032).
  - `identity new`, `identity bind`, `identity show` and `identity list` (FR-029).

**Checkpoint**: `python3 -m unittest test_identity test_sessions_migration -v` and `python3 test_held_quarantine.py` pass.

---

## Phase 3: User Story 1 - An agent sends mail by writing a block, and only then (P1) 🎯 MVP

**Goal**: the Stop hook reads the last `rookery` block and sends exactly that mail. No block gives no mail.

**Independent test**: run the fixtures through `turn_block.dispatch_text()` and `rookery hook stop`. No block gives
0 mail. A valid block gives 1 mail. Two blocks give only the last. A malformed block gives 1 `malformed` row
and 1 bounce (R6).

### Tests first

- [x] T013 [P] [US1] Create these fixture turn texts in `fixtures/turns/`:
  - `no-block.txt`.
  - `valid-ask.txt`: `to: law-counsel`, `task: TASK-12`, `kind: ask`, `status: in-progress`, and one `thread`.
  - `two-blocks.txt`.
  - `example-then-real.txt`: an example in a `json` fence, then a real block.
  - `only-example-block.txt`: one `rookery` block in the middle of the message.
  - `unclosed-fence.txt` and `backticks-in-say.txt`.
  - `malformed-json.txt`, `malformed-missing-to.txt`, `malformed-kind-veto.txt`, `malformed-call-field.txt`, `malformed-unknown-field.txt` and `malformed-status.txt`.
  - `malformed-oversize.txt` (more than 4,096 bytes) and `malformed-say-long.txt` (1,201 chars).
  - `malformed-read-multiline.txt`, `malformed-read-long.txt` (257 chars), `malformed-read-outside.txt` and `malformed-read-dotdot.txt`.
  - `malformed-task-path.txt` (`TASK/12`).
  - `wrong-recipient.txt` (`to: law-cousel`) and `off-team.txt`.
  - `handoff-in-progress.txt` and `handoff-done.txt`.
- [x] T014 [US1] Write part 1 of `test_turn_block.py` (RQ-1.1, RQ-1.2, RQ-1.3, RQ-1.4, RQ-1.5, RQ-1.17, RQ-1.25, RQ-1.27, R6, SC-001, SC-011). Use a new database, a new Rookery root, and session `alpha` with the identities `me` and `law-counsel`. Compare each fixture of T013 with the outcome table in [data-model.md](data-model.md). Also check these items:
  - A valid row has agent IDs in `sender` and `recipient`. `topic` holds the task. `body` holds compact JSON with sorted keys. `session_id` holds the session of the sender.
  - The `thread` value stays unchanged in the body.
  - With `ROOKERY_HOLD=1`, the row status is `held`.
  - A malformed row has `decided_by='turn-block-parser'`. Its bounce `note` names the first rule that failed and the next step.
  - Each bounce row has the `session_id` of the original row (FR-034).
  - `handoff-in-progress` gives 0 rows and the outcome `handoff-pending`. `handoff-done` gives 1 row (FR-007).
  - The note for an oversize block states the 4,096-byte limit.
  - `wrong-recipient` has `suggest` = `["law-counsel"]`.
  - `off-team` (a name only in session `beta`) has no `suggest`.
  - With `ROOKERY_URL` set, no row appears. The block goes to `outbox/failed/<TASK-ID>-<ts>.json`, and the log reads `remote-unsupported`.
  - Without a session, no row and no file appear.
  - Each fixture writes exactly one `log_event()` line with its outcome (FR-022).
- [x] T015 [US1] Write part 2 of `test_turn_block.py` (RQ-1.4, D1, D16). Run `rookery hook stop --project p` as a subprocess. Give it Stop JSON on stdin with `last_assistant_message`, `session_id` and `cwd`. Check these items:
  - The outcomes are the same as in part 1.
  - Without `last_assistant_message`, the hook logs `no-message-field` and sends nothing.
  - When `rookery.send()` raises an error, the hook writes the block to `outbox/<TASK-ID>-<ts>.json` and exits 0.
  - Another connection holds a write lock on the database. The hook exits 0 in less than 3 s, logs `db-busy`, and puts the valid block in `outbox/` (D16).
  - stdout is always empty, with or without unread mail (FR-017).
  - A Stop with a session but no identity (no session start ran) keeps a valid block in `<root>/unresolved/<TASK-ID>-<ts>.json` and logs `no-identity`. No row and no identity appear (D16).

### Implementation

- [x] T016 [US1] Write `find_last_block(text)` in `turn_block.py` (D2). Scan line by line:
  - An opening fence has 3 or more backticks or tildes and the info string `rookery` after trim.
  - The closing fence uses the same character and is at least as long.
  - Keep the last complete block. Ignore an unclosed fence.
- [x] T017 [US1] Write `validate(raw, root)` in `turn_block.py` (D12, FR-002 to FR-004, [schema](contracts/turn-block.schema.json)). It returns `(block, None)` or `(None, note)`. Use the note texts of the contract. Check in this order:
  1. The text is one JSON object.
  2. The compact size is 4,096 bytes or less.
  3. No top-level field is unknown. This rejects `call`.
  4. `to`, `task`, `kind`, `say` and `status` are present.
  5. `to` and `task` are safe names.
  6. `kind` is one of the six agent kinds. A system kind gets the `veto` note text.
  7. `status` is `done` or `in-progress`.
  8. `say` has 1 to 1,200 chars.
  9. Each `read` entry is one line of 256 chars or less, has no `..` part, and resolves inside `root`.
  10. `self_check` and `artifacts` have the types of the schema.

  Put the D12 source in a comment next to the size constants.
- [x] T018 [US1] Write two entry points in `turn_block.py` (D3, D4, D17, D22, FR-007, FR-035). `ctx` holds the root, the session, the identity and the adapter name (`stop`, `turn` or `outbox`).
  - `dispatch_text(conn, text, ctx)` calls `find_last_block()`. No block: outcome `no-block`. Else it calls `dispatch_block()`.
  - `dispatch_block(conn, raw, ctx)` uses this order:
    1. `ROOKERY_URL` is set: outcome `remote-unsupported`. For `stop` and `turn`, write the block to `outbox/failed/<TASK-ID>-<ts>.json` (use `no-task` when `task` is not a safe name).
    2. Invalid block: `send(hold=True, session_id=…)` to the raw `to` text (or `rookery`). Then `quarantine(…, "malformed", decided_by="turn-block-parser", note=…)`.
    3. `to` exists only in another session: quarantine as `off-team`.
    4. `to` exists in no session: quarantine as `wrong-recipient`. Get `suggest` from `difflib.get_close_matches(n=3)` over the names in the sender's session.
    5. `kind` is `handoff` and `status` is `in-progress`: outcome `handoff-pending`, no send.
    6. Else: `send()` with agent IDs.
  - Return `{"outcome", "msg_id", "reason"}`. Log each outcome with `log_event()`.
  - When `send()` raises an error, return `send-failed`. Only for `stop` and `turn`, also write the block to `outbox/<TASK-ID>-<ts>.json`. The `outbox` adapter never puts a block back (D3).
- [x] T019 [US1] Create `cli_hook.py` with `stop(args)`. Read the Stop JSON from stdin, open the database with `open_db()`, and call `resolve(create=False)` (D16). Without a session, return at once. With a session but no identity, keep a valid block in `<root>/unresolved/<TASK-ID>-<ts>.json`, log `no-identity`, and return. Else call `dispatch_text()` on `last_assistant_message`. Print nothing: the hook never blocks a stop (FR-017). Put the whole body in `try/except Exception`, log the error, and exit 0. Register `rookery hook session-start|prompt|stop --project NAME` in `rookery_cli.py`. Leave `session-start` and `prompt` empty until T037 and T038.
- [x] T020 [US1] Run this after T014, T015, T016, T017, T018, T019. Run `python3 -m unittest test_turn_block -v`. All US1 cases must pass. Run `python3 test_held_quarantine.py`. It must still pass.

**Checkpoint**: an agent can send mail with a block. US1 works alone.

---

## Phase 4: User Story 2 - Hand-offs happen only when the work is done (P1)

**Goal**: each valid block rewrites `runs/<TASK-ID>/HANDOFF.md`. Rookery holds back only a `handoff` with
`status: in-progress`.

**Independent test**: `handoff-in-progress` rewrites `HANDOFF.md` and gives 0 mail. `handoff-done` gives a
`HANDOFF.md` with `status: done` and 1 mail (R7). A search for `TASK-12` in the workspace lists the file.

### Tests first

- [x] T021 [P] [US2] Create `fixtures/turns/ask-in-progress.txt`: an `ask` with `status: in-progress`, two `artifacts`, two `read` entries and a `self_check.gap`.
- [x] T022 [US2] Add these cases to `test_turn_block.py` (RQ-1.6, RQ-1.7, RQ-1.8, R7, SC-002, SC-008):
  - `handoff-in-progress` gives 0 rows and a `HANDOFF.md` with `status: in-progress`.
  - `handoff-done` gives 1 row and `status: done`.
  - `ask-in-progress` gives 1 row.
  - The frontmatter has `type: Handoff`, `task`, `agent`, `agent_id`, `session`, `updated` and `status`.
  - The four sections come in order. They hold the artifact lines, the read lines, the gap and the open thread of the fixture.
  - Without those fields, the text reads "Nothing stated." and "None.".
  - A malformed block leaves `HANDOFF.md` unchanged.
  - `wrong-recipient` and `off-team` rewrite `HANDOFF.md`.
  - Each file that Rookery wrote under the workspace for `TASK-12` has `TASK-12` in its path, and a `task: TASK-12` line (Markdown) or a `"task": "TASK-12"` key (JSON). This covers `HANDOFF.md` and the put-back files of T015 (FR-010).
- [x] T023 [P] [US2] Add these cases to `test_identity.py` (FR-036, RQ-1.26):
  - `session new --from alpha` creates one identity for each name of `alpha`, with `previous_id` set.
  - It copies the newest `runs/*/HANDOFF.md` of each name into the new workspace, and logs each copy.
  - It copies no mail, no `state/` file and no binding.
  - `identity link --previous <id>` sets `previous_id` and moves no mail.

### Implementation

- [x] T024 [US2] Write `write_handoff(ws, block, identity, session)` in `turn_block.py`. Use the exact template of [data-model.md](data-model.md). Write to a temporary file in the same folder, then call `os.replace`.
- [x] T025 [US2] Edit `dispatch_block()` in `turn_block.py` (FR-009, D11). Call `write_handoff()` for each valid block, before step 3 of T018, only when the adapter is `stop` or `turn`. The `outbox` adapter never writes `HANDOFF.md`: an outbox file is not the end of a turn, and the postmaster writes only `<root>/postmaster/` (constitution VII). A malformed block writes nothing.
- [x] T026 [US2] Add `--from SESSION` to `rookery session new`, and add `rookery identity link --previous AGENT_ID`, in `cli_rookery_session.py` (D21). Put the copy logic in `rookery_identity.carry_over(conn, old, new, root)`.
- [x] T027 [US2] Run this after T021, T022, T023, T024, T025, T026. Run `python3 -m unittest test_turn_block test_identity -v`. All cases must pass.

**Checkpoint**: US1 and US2 both work. A hand-off reaches a peer only when the work is done.

---

## Phase 5: User Story 3 - The user installs Rookery into a project without losing anything (P1)

**Goal**: `rookery init` writes the managed section and merges three hooks next to the dev-kid hooks.

**Independent test**: run init on a copy of `fixtures/settings/devkid.json` and on a `CLAUDE.md` with text
around the markers. Each old hook and key stays. Rookery's hooks appear once. Runs 2 and 3 give
byte-identical files.

### Tests first

- [x] T028 [US3] Write `test_init.py` (RQ-1.10, RQ-1.11, FR-012 to FR-014, SC-004). Work in a temporary folder. Check these cases:
  - Without `CLAUDE.md`, init creates it with only the section. The action is `created`.
  - With text and no markers, init appends the section, and the other bytes stay identical. The action is `inserted`.
  - With an old section, only the text between the markers changes. The action is `replaced`.
  - `--harness codex` writes `AGENTS.md` and does not create `.claude/settings.json`.
  - The dev-kid copy gets one `rookery hook <event> --project <p>` group on each of `SessionStart`, `UserPromptSubmit` and `Stop`. Each old event list stays identical as a prefix.
  - Init replaces a changed Rookery command in place.
  - After 3 runs, the files of runs 2 and 3 are byte-identical, and each `--json` action reads `unchanged`.
  - Invalid JSON gives exit 2 and changes no file. stderr names the path, the line and the column.
  - Two inits at the same time (two processes) leave valid JSON with one Rookery group for each event, and one ROOKERY section in `CLAUDE.md`.
  - After init, `--dir` holds no new file other than `CLAUDE.md` (or `AGENTS.md`) and `.claude/settings.json`. The lock is `<root>/locks/init.lock` (FR-023).
  - The human output ends with the line "Open agent sessions do not join".
  - The section in `CLAUDE.md` holds the root path with `{root}` filled in, `rookery inbox`, `rookery identity show`, and the rule that it applies only when Rookery gives an identity (FR-012).

### Implementation

- [x] T029 [P] [US3] Write `rookery_section.md` (FR-010, FR-012, FR-031). Use only the fields `{project}` and `{root}`. Put no agent name in it. The text states these rules:
  - The rules apply only when Rookery gives you an identity at session start.
  - End a final message with one `rookery` block. Show one example in a `json` fence, and list the fields.
  - Put example blocks in a different fence language.
  - Read your mail with `rookery inbox`. `rookery identity show` tells you who you are.
  - Put the task ID in the path and in a `task:` line of each file that you write.
  - Put long text in a file under `{root}`, and list its path in `read`.

  Run `ste-lint rookery_section.md --max-score 2.0 --glossary ste-glossary.txt`. It must pass.
- [x] T030 [US3] Write `upsert_section(path, text)` in `cli_init.py` (D13, the Spec Kit rule). It creates the file, or inserts or replaces the text between `<!-- ROOKERY START -->` and `<!-- ROOKERY END -->`. It returns the action. It writes only when the content changes, through `write_json_atomic()`'s temporary-file rule, under the init lock of T031.
- [x] T031 [US3] Write `merge_hooks(path, project)` in `cli_init.py` (D13):
  - Parse the file first. On a parse error, raise an error with the line and the column, and write nothing.
  - For each event, find a handler whose `command` starts with `rookery hook `. Equal: no change. Different: replace it in place. Absent: append `{"hooks": [{"type": "command", "command": "rookery hook <event> --project <project>"}]}`.
  - Keep the key order and the indent of the old file.
  - Write only when the content changes. Write through a temporary file and `os.replace`, under `fcntl.flock` on `<root>/locks/init.lock`. Never create a lock file in the host repo (FR-023, constitution VII).
- [x] T032 [US3] Write `main(args)` in `cli_init.py` with `--harness`, `--project`, `--dir` and `--json`, as in the contract. The `--project` default is the folder name of `git rev-parse --show-toplevel` in `--dir`. Exit 2 on invalid settings JSON. Register `init` in `rookery_cli.py`. Run `python3 -m unittest test_init -v`. All cases must pass.

**Checkpoint**: init installs Rookery into a project and changes nothing else.

---

## Phase 6: User Story 4 - Agents notice their mail without having to remember (P2)

**Goal**: session start tells the agent who it is and what is open. The prompt hook gives one line only when
mail waits. Rookery never blocks a stop. The postmaster pushes mail only into an idle pane. This is mail, not a
phone call.

**Independent test**: with 0 unread, the prompt hook gives 0 bytes. With 2 unread from `researcher` about
`TASK-12`, the agent gets one notice line, and its stop is not blocked. A busy agent gets no push. After its
Stop, it gets one push. Without a session, every hook gives 0 bytes.

### Tests first

- [x] T033 [P] [US4] Write `test_hooks.py` (RQ-1.12, RQ-1.13, RQ-1.14, RQ-1.24, FR-015 to FR-017, FR-037, SC-005, SC-006, SC-010). Run each hook as a subprocess. Check these cases:
  - Without a session, each hook prints 0 bytes. The tables `nodes`, `identities` and `inbox` do not change.
  - Session start prints `[rookery] You are <name> (<agent_id>) in Rookery session <name>.`, then the newest `HANDOFF.md`, then the unread line.
  - Session start calls `register_node(agent_id, kind="pane", lifecycle="persistent")`.
  - Session start appends two `export` lines to the file in `CLAUDE_ENV_FILE`.
  - `source: compact` prints the same text.
  - The prompt hook with 0 unread and no new failed block prints 0 bytes.
  - A failed block (a `.result.json` with `send-failed` in the postmaster `failed/` folder of the identity) gives one notice line in the prompt hook. The next prompt does not show it again (FR-037, D23).
  - The prompt hook with 2 unread from `researcher` prints one line with the count, the sender name and the task. The line holds no body text, and no status changes.
  - A stop with unread mail prints nothing and does not block (FR-017).
  - The prompt hook writes `state/turn.json` with `busy`. The Stop hook and the session-start hook write `idle`.
  - Without a session, no hook writes `state/turn.json`.
  - `held` and `quarantined` mail is not unread.
  - An exception inside a hook gives exit 0 and one log line.
- [ ] T034 [US4] Add these inbox cases to `test_hooks.py` (FR-031):
  - `rookery inbox --json` lists only the mail of the resolved agent ID, and marks it read.
  - `rookery peek --json` lists the same mail and changes nothing.
  - Neither command accepts `--as` or `--all`.
  - Each call logs `inbox-read` with the agent ID and the bindings.
  - Without a session, both exit 3 with the message of the contract.
- [x] T035 [P] [US4] Write `test_wake.py` (RQ-1.15, RQ-1.23, FR-018, FR-028). Put a fixture script `fixtures/bin/herdr` on `PATH`. The script appends its arguments as one JSON line to a file. Check these cases:
  - An idle identity has the binding `herdr-pane:w1:p2` and new pending mail. The script runs once for each command of the `wake` entry, as an argument list, with `{value}` and `{text}` filled in.
  - The text names the sender name, the task and `rookery inbox`.
  - A second loop without new mail runs nothing (`<root>/postmaster/pushed/<agent_id>.json`).
  - A busy identity (`state/turn.json` reads `busy`) gets no push. After its state changes to `idle`, the next loop pushes once (FR-018).
  - An identity without `state/turn.json` gets no push, and the log reads `no-idle-state`.
  - An identity without a wake binding gets no push, and the log reads `no-wake-binding`. `postmaster.wake()` is not called (replace it with a recorder).
  - An identity in an ended session gets no push.
  - The postmaster writes nothing under `agents/` except the claim renames of the outbox scan.

### Implementation

- [x] T036 [US4] Write `unread(conn, agent_id)` and `unread_summary(conn, agent_id)` in `turn_block.py` (D7). Unread rows have `recipient=<agent_id>` and `status='pending'`. The summary gives the count, the sender names and the task IDs. Map each agent ID to its name through `identities`. A bounce has the sender name `rookery`. Also write `new_failed_blocks(root, session, name)` (D23). It lists the failed blocks in `<root>/postmaster/outbox/<session>/<name>/failed/` and `agents/<session>/<name>/outbox/failed/` that `state/failed-seen.json` does not name.
- [ ] T037 [US4] Write `session_start(args)` in `cli_hook.py` (FR-015, D9). Resolve the identity. Without a session, return. Else register the node. When `CLAUDE_ENV_FILE` is set, append the exports. Write `state/turn.json` with `idle`. Then print the who-am-I line, the newest `runs/*/HANDOFF.md` by mtime, the unread line, and one line for each new failed block. Add each shown block to `state/failed-seen.json`.
- [ ] T038 [US4] Write `prompt(args)` in `cli_hook.py` (FR-016). Write `state/turn.json` with `busy`. Print one line only when unread mail exists: `[rookery] N unread from A, B about TASK-1, TASK-2. Read them with: rookery inbox`. Then print one line for each new failed block, with the text of D23, and add it to `state/failed-seen.json` (FR-037). Print nothing else.
- [ ] T039 [US4] Add the idle write to `stop()` in `cli_hook.py` (FR-017, D8). After `dispatch_text()`, write `state/turn.json` with `{"state": "idle", "since": ts}` through `write_json_atomic()`. Never print and never block.
- [x] T040 [US4] Add `rookery inbox` and `rookery peek` to `cli_rookery_session.py`, and register them in `rookery_cli.py` (FR-031). `inbox` calls `rookery.claim()` and `rookery.ack()` on the listed IDs. Log `inbox-read`. Without a session, exit 3.
- [x] T041 [US4] Add the wake step to `postmaster.run()` in `postmaster.py` (D10, FR-018, FR-028):
  - Open the database with `turn_block.open_db()`, so the postmaster and the hooks use the same `db_path()` (D6).
  - Each loop, find each identity in an open session with pending mail above the ID in `<root>/postmaster/pushed/<agent_id>.json`.
  - Push only when its `state/turn.json` reads `idle`. Read that file; never write it. No file: log `no-idle-state` and skip.
  - Find its first binding whose kind has a `wake` entry in `load_config()`. Run each command of that entry with `subprocess.run(list, shell=False, timeout=10)`. Then write the new last pushed ID.
  - Without such a binding, log `no-wake-binding` and skip. Do not call `wake()`.
  - Log each push to the `postmaster` stream. Put no herdr or tmux name in the code.
- [ ] T058 [US4] Make `--nodes` optional in `postmaster.py` (D10, FR-018). Without a roster, `run()` skips the roster steps and runs only the outbox scan and the wake step. With `--nodes`, the behavior stays the same. Add a case to `test_wake.py`: start `python3 postmaster.py --poll 0.2` with no `--nodes` as a subprocess, with an idle identity that has new mail. Within 5 s the fixture script runs once. Then stop the process. Run `python3 test_held_quarantine.py`. It must still pass.
- [ ] T042 [US4] Run this after T033, T034, T035, T036, T037, T038, T039, T040, T041. Run `python3 -m unittest test_hooks test_wake -v`. All cases must pass.

**Checkpoint**: the agent sees its mail at start, at each prompt and when it is idle. Nothing interrupts its turn.

---

## Phase 7: User Story 5 - Hookless harnesses and mid-task mail use the same contract (P3)

**Goal**: `rookery turn` and the outbox scan give the same outcomes as the Stop hook.

**Independent test**: the US1 and US2 fixtures give the same outcomes through `rookery turn`. A file in
`outbox/` gives one mail once, and a restart does not send it again.

### Tests first

- [ ] T043 [P] [US5] Add these cases to `test_turn_block.py` (RQ-1.16, FR-020). Give each fixture of T013 and T021 to `rookery turn --project p --json` on stdin. The `outcome`, the rows and `HANDOFF.md` must be identical to the results of `dispatch_text()`. The `--json` output must have the shape of the contract. With the database locked, `rookery turn` exits 4 with the outcome `db-busy` (D16).
- [ ] T044 [P] [US5] Write `test_outbox.py` (RQ-1.9, FR-011). Check these cases:
  - A valid `outbox/a.json` gives one row. The file moves to `<root>/postmaster/outbox/<session>/<name>/sent/TASK-12-a.json` with a `.result.json` that holds `task`, `outcome`, `msg_id` and `reason`.
  - A malformed file gives one `malformed` row. The file moves to `failed/`.
  - Two scans give one row.
  - A file in `sending/` at scan start moves to `failed/` with the note "interrupted: check the mailroom before you send again". No row appears.
  - When `rookery.send()` raises an error, the file moves to `failed/` with `send-failed`, and no new file appears in `outbox/`. A second scan sends nothing (D3).
  - With `ROOKERY_URL` set, the file moves to `failed/` with `remote-unsupported`. Exactly one copy of the block exists.
  - No file is lost.
  - The scan never changes `HANDOFF.md`. A put-back file from turn N does not overwrite the `HANDOFF.md` that turn N+1 wrote (D11).
  - The sender is the identity with that session name and agent name.

### Implementation

- [ ] T045 [US5] Write `turn(args)` in `cli_hook.py` (FR-020). Read stdin and resolve from the environment. Call `dispatch_text()` with the adapter `turn`. Use `resolve(create=True)`. With `--json`, print the result. On `db-busy`, exit 4 (D16). Write no idle state. Add no code that starts a live Codex agent: `rookery turn` is the only Codex path in phase 1 (FR-020, R19). Register `rookery turn --project NAME [--json]` in `rookery_cli.py`.
- [ ] T046 [US5] Write `scan_outbox(conn, root)` in `turn_block.py` (D3, D11). Call it each loop from `postmaster.run()` in `postmaster.py`, for each root under `~/.rookery/projects/*/` and for `ROOKERY_ROOT`. The scan writes only under `<root>/postmaster/outbox/<session>/<name>/`. It does these steps:
  1. Move each file in `sending/` to `failed/` with the note.
  2. Claim each `agents/<session>/<name>/outbox/*.json` with `os.rename` into `sending/`. Skip the `failed/` subfolder of the agent.
  3. Read the file and call `dispatch_block()` with the adapter `outbox`.
  4. Move the file to `sent/` or `failed/` as `<TASK-ID>-<file name>`, with a `.result.json`. Never put a file back.
  5. Log each outcome to the `postmaster` stream.
- [ ] T047 [US5] Run this after T043, T044, T045, T046. Run `python3 -m unittest test_turn_block test_outbox -v`. All cases must pass.

**Checkpoint**: all five stories work.

---

## Phase 8: Polish and cross-cutting concerns

- [ ] T048 Add `turn_block.py`, `rookery_identity.py`, `cli_init.py`, `cli_hook.py` and `cli_rookery_session.py` to `_PY_MODULES` in `bootstrap.py`. Add `rookery_section.md` and `rookery_config.default.json` to `_DATA_FILES` (D14). Run `python3 bootstrap.py`. Then run `rookery init --help` and `rookery hook stop --help` from a folder outside the repo.
- [ ] T049 Prove the migration on a COPY of the live database (quickstart §2). Copy `~/Documents/code/rookery/rookery.db` to `/tmp/rookery-copy.db`. Run `ROOKERY_DB=/tmp/rookery-copy.db python3 test_sessions_migration.py`. Then run `rk peek --as <old node>` on the copy. Put the output in the PR.
- [ ] T050 [P] Add a timing case to `test_hooks.py`. With 10,000 inbox rows, each hook must finish in less than 1 s (the performance goal of the plan).
- [ ] T051 [P] Write `CHANGELOG.md` with a 0.7.0 entry for this phase.
- [ ] T052 [P] Add a short section "rookery init and sessions" to `README.md`, with the commands of quickstart §3.
- [ ] T053 Lint all prose of this phase (RQ-1.19, FR-024). Collect each bounce note, notice and error text from `turn_block.py`, `cli_hook.py`, `cli_init.py` and `cli_rookery_session.py` into one file in the scratch folder. Run `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt` on that file and on these files: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `tasks.md`, `contracts/cli-and-hooks.md`, `rookery_section.md`, `CHANGELOG.md` and the `README.md` section. Also lint the OKF files that this PR changes (constitution VIII covers requirement files and docs): `okf/requirements/rookery-0-7.md`, `okf/design/rookery-stays-in-context.md` and `okf/risks/register.md` (after T057). Fix each failure.
- [ ] T054 Run this after T048, T049, T050, T051, T052. Run the full suite: `python3 -m unittest test_turn_block test_init test_hooks test_identity test_outbox test_wake -v` and `python3 test_held_quarantine.py`. All cases must pass.
- [ ] T055 Run `spec-pane cover` for this feature. Each of RQ-1.1 to RQ-1.27 must appear in `spec.md` and `tasks.md` (SC-007).
- [ ] T056 Run this after T053, T054. Do the live checks of quickstart §6 with the user. Check the wake in a herdr pane: no notice during a running turn, one notice after it ends (RQ-1.15, RQ-1.23). Check a block after `/compact` (R5, RQ-1.18, SC-003). Check a plain `claude` session without a Rookery session (SC-010). Record each transcript path. These checks need the panes of the user. Do not mark them done from fixtures.
- [ ] T057 Edit `~/Documents/code/inter-agent-comms/okf/risks/register.md`. Move R5, R6, R7 and R8 to `proven`. Give the test name or the transcript path of T056 as the evidence for each.

---

## Dependencies and execution order

### Phase dependencies

- **Phase 1 (Setup)**: no dependency.
- **Phase 2 (Foundational)**: after Phase 1. It blocks every story, because each story calls `resolve()` and
  writes rows with agent IDs.
- **US1 (Phase 3)**: after Phase 2.
- **US2 (Phase 4)**: after US1. It edits `dispatch_block()` and adds to `test_turn_block.py`.
- **US3 (Phase 5)**: after Phase 2 only. It does not touch `turn_block.py`, so it can run in parallel with
  US1 and US2.
- **US4 (Phase 6)**: after US1, because it needs `cli_hook.stop` and `dispatch_text()`. Its tests write
  `HANDOFF.md` files directly, so it does not need US2.
- **US5 (Phase 7)**: after US2, because it uses the US2 fixtures again. T046 edits `postmaster.py` after T041 and T058.
- **Phase 8 (Polish)**: after all stories. T057 needs T056.

### Story order

```text
Setup → Foundational ─┬─ US1 → US2 → US5
                      │    └──→ US4 ──┘ (postmaster.py: T041 and T058 before T046)
                      └─ US3
                      all → Polish
```

### Inside each story

- Write the tests first, and see them fail.
- Edit `turn_block.py` before `cli_hook.py`. Edit `cli_hook.py` before the registration in `rookery_cli.py`.
- Tasks that edit the same file run in ID order:
  - `turn_block.py`: T008, T016 to T018, T024, T025, T036, T046.
  - `test_turn_block.py`: T014, T015, T022, T043.
  - `test_hooks.py`: T033, T034, T050.
  - `rookery_cli.py`: T012, T019, T032, T040, T045.
  - `cli_hook.py`: T019, T037, T038, T039, T045.
  - `postmaster.py`: T041, T058, T046.
  - `test_wake.py`: T035, T058.

## Parallel opportunities

- Phase 1: T001, T002 and T003 run together.
- Phase 2: T004 and T005 run together.
- After Phase 2, US3 (T028 to T032) runs next to US1.
- US2: T021 and T023 run together.
- US4: T033 and T035 run together.
- US5: T043 and T044 run together.
- Polish: T050, T051 and T052 run together.

### Parallel example: User Story 4

```text
Task: "Write test_hooks.py hook cases (T033)"
Task: "Write test_wake.py with a fixture herdr script on PATH (T035)"
```

### Parallel example: after Phase 2

```text
Task: "US1: fixtures and test_turn_block.py (T013, T014)"
Task: "US3: test_init.py and rookery_section.md (T028, T029)"
```

## Implementation strategy

### MVP first

1. Do Phase 1 and Phase 2.
2. Do US1. Then stop and run `test_turn_block`. A block sends mail, and no block sends nothing (R6).
3. Do US2 and US3 (both P1). They complete the P1 scope. With US3, init installs the Stop hook into a project.

### Incremental delivery

1. Setup and Foundational: sessions and identities exist.
2. US1: mail from blocks (the MVP).
3. US2: hand-offs and `HANDOFF.md` (R7).
4. US3: `rookery init`.
5. US4: notices, the idle state, the inbox and the idle wake (the R5 live check).
6. US5: `rookery turn` and the outbox scan.
7. Polish: the zipapp, the lint, the live checks and the register.

## Notes

- `[P]` tasks use different files and have no open dependency.
- Commit after each task or each checkpoint.
- Do not change `rk` (FR-031).
- Do not add herdr or tmux code. Those names live only in `rookery_config.default.json` (constitution IX).
