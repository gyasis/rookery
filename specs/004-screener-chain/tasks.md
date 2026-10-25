---
type: Tasks
task: 004-screener-chain
updated: 2026-10-25
revision: 1
---

# Tasks: the screener chain and the broker gates

**Input**: the design documents in `specs/004-screener-chain/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/screener.md](contracts/screener.md) and [quickstart.md](quickstart.md).

**Tests**: MANDATORY (constitution II). Each FR and each automatic SC gets the test that proves it, in this PR
(see the Requirement Trace in plan.md). Use fixtures, not mocks. Each test uses a temporary `HOME`,
`ROOKERY_DB` and `ROOKERY_ROOT` (`test_hooks.Base`). Each task writes its test first, sees it fail, then
writes the code. No task leaves a failing test.

**Layout**: a flat Python repo. All paths start at the repo root.

## Format: `- [ ] T### [P?] [Story?] Description`

- **[P]**: the task can run in parallel. It uses different files and has no open dependency in its phase.
- **[Story]**: the user story of the task (US1 to US6).
- **Needs T0xx first.**: an explicit dependency on another task.
- **Verify:** the exact command that proves the task. It runs the tests of the task and the suites that the
  task could break.

## Rules for every task

- Read the research decision that the task names (Dn in [research.md](research.md)) before you write code.
- A task that writes a test also writes the code that makes it pass. No `skip` or `expectedFailure` hides missing
  code. Skip a test only when an outside tool (`ste-lint`, `git`) or the optional PromptChain import is missing.
  Give the reason in the skip message.
- No test calls a model or opens a socket to `192.168.0.159` (FR-036). Model tests set
  `ROOKERY_SCREENER_FAKE` to a fixture file. The backend contract test binds a local `http.server` on
  `127.0.0.1` only.
- No test opens `~/Documents/code/rookery/rookery.db`. The live-copy test reads only a copy that `ROOKERY_DB`
  names, and skips when `ROOKERY_DB` is not set to a file.
- The gate, the hooks and `screener_t0.py` never import `screener_backend` or `promptchain` (D6).
- No code sends `max_tokens`, `max_completion_tokens`, `num_predict`, `num_ctx` or `think` (FR-034).
- No code names a T2 model or a terminal tool. The model name lives only in the defaults of
  `screener_config.py` and in `rookery_config.default.json`.
- Subprocess tests run `[sys.executable, "rookery_cli.py", ...]` with `HOME`, `ROOKERY_DB` and
  `ROOKERY_ROOT` set to a temporary folder. A test that needs a custom configuration writes
  `~/.rookery/config.json` in its temporary `HOME`.
- Each text that an agent or the user reads passes `ste-lint --max-score 2.0 --glossary ste-glossary.txt`
  (FR-032).
- The existing suites are
  `test_turn_block test_send test_hooks test_outbox test_wake test_identity test_init test_gate test_gate_log test_gate_scan test_held_quarantine`.
  Each `Verify:` of a code task runs them.
- T026 adds all six new modules to `bootstrap._PY_MODULES` in one edit, so that parallel tasks never share
  `bootstrap.py`. No other task edits `bootstrap.py`.
- No module imports `promptchain` at the top level. `cli_screen.py` imports `screener` only inside its command
  handlers, because `rookery_cli` imports every `cli_*` module at start and the PromptChain import takes 6 s (F20).
  `mail_gate.py`, `turn_block.py`, `cli_hook.py`, `mail_screen.py` and `screener_t0.py` never import `screener` or
  `screener_backend` (D6).

---

## Phase 1: Setup

**Purpose**: the fixtures and the one change that no story needs.

- [x] T001 [P] Files: `fixtures/bin/hunch`, `fixtures/screener/team.json`, `fixtures/screener/fake_t1.json`,
  `fixtures/screener/injection.txt`. Create `fixtures/bin/hunch` (executable, `#!/usr/bin/env python3`, standard library). It reads the
  state from stdin and `-q <json>`, and takes `--raw` and `--timeout`. It answers from the JSON file in
  `$ROOKERY_FAKE_HUNCH`, with the same rule format as the fake backend of the contract (`match`, `label`,
  `confidence`, `fail`). `fail: "error"` prints exit 0 with `answers[q] = {"error": "..."}`. `fail: "exit1"` exits
  1 with a stderr line. `fail: "sleep"` sleeps 5 s. Each call appends one JSON line with its arguments and
  `HUNCH_MODEL`, `HUNCH_VOTE` to `$ROOKERY_FIXTURE_LOG`. Create `fixtures/screener/team.json` (session `screener-demo`, because `rookery up` keys its
  idempotence on the name; agents `researcher`, `counsel`, `swe`, and a `boss`; scope cards; acceptance; `ask_budget` 3).
  Create `fixtures/screener/fake_t1.json` (one rule for each of the four labels, one `timeout` rule, one
  `bad-answer` rule). Create `fixtures/screener/injection.txt` (one line for each pattern family of D18).
  Verify: `L=$(mktemp) && echo '{"t1":[{"match":"x","label":"deliver","confidence":0.9}]}' > $L.json && echo x | ROOKERY_FAKE_HUNCH=$L.json ROOKERY_FIXTURE_LOG=$L fixtures/bin/hunch - -q '{"route":{"type":"choice","criteria":["deliver","unneeded"]}}' --raw && test -s $L`
- [x] T002 [P] Files: `rookery_names.py`, `rookery_identity.py`, `test_session_names.py`.
  Create `rookery_names.py` with three lists of 64 words (moods, colors, animals; lower-case ASCII, no
  hyphen, no duplicates) and `make_name(rng=secrets)`. Edit `rookery_identity._new_session`
  (`rookery_identity.py:154-180`): when `name is None`, choose a name with `make_name` and try again on a clash,
  inside the existing loop of `_ID_TRIES`. The session ID stays `rs-` plus 8 hex characters. (D22, FR-037). Write `test_session_names.py` first. Cases: 1,000 sessions with no name
  are all different, each matches `^[a-z]+-[a-z]+-[a-z]+$`, and none matches `_ID_LOOKALIKE` (SC-015). Each list
  has 64 unique words. A given name works as before. A clash with a used name makes it choose again (seed a fake
  `rng` that repeats once). `rookery session new` with no name prints a three-word name.
  Verify: `python3 -m unittest test_session_names test_identity test_sessions_migration test_hooks -v`

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: the schema, the configuration, the backend, the record writer and the `escalated` status.

- [x] T003 Files: `rookery.py`, `turn_block.py`, `test_screen_schema.py`.
  Edit `rookery.py`. Add `thread TEXT` with `_add_column` in `init_db`. Add the six tables of
  [data-model.md](data-model.md) (`session_context`, `agent_scope`, `threads`, `screen_mail`, `screen_decisions`,
  `screen_calls`) with their keys and indexes to `_SESSION_TABLES_SQL`. Edit `turn_block._schema_current`
  (`turn_block.py:120-133`) so that it knows the new column and tables (F8). Write `test_screen_schema.py` first.
  Cases: a new database has every table and column of data-model. An old database (built from
  `git show dd56f00:schema.sql` plus the 002 columns, with three inbox rows) migrates twice with no error. The
  inbox count and values do not change. `UNIQUE (mail_id, check_name, attempt)` refuses a second row.
  `ScreenSchemaLiveCopy` copies the file that `ROOKERY_DB` names with the backup API from a `mode=ro` URI into a
  temporary folder. It migrates the copy, as `test_sessions_migration.SessionsMigrationLiveCopy` does (FR-035). After the migration,
  a hook on 10,000 rows stays under 1 s, as `test_hooks.HookTiming` measures (D25).
  Verify: `python3 -m unittest test_screen_schema test_sessions_migration test_up_schema test_turn_block test_hooks test_send -v`
- [x] T004 [P] Files: `screener_config.py`, `rookery_config.default.json`, `test_screen_config.py`.
  Create `screener_config.py` with `load() -> dict` (defaults in code for each missing key of the
  data-model `screener` block). It also has `check(cfg)`, which raises `ValueError` that names the key. The causes
  are a mode not in `shadow|live|off`, bands not `0 <= review <= accept <= 1`, a T2 command that is not a list of
  text, and a non-positive number. It also has `set_mode(check, mode)`. This writes only `screener.checks.<check>`
  in `~/.rookery/config.json` and keeps every other key. Add the `screener` block to `rookery_config.default.json`. (D21, FR-004). Write `test_screen_config.py` first. Cases: an old
  configuration file with no `screener` key gives every default, and every mode is `shadow`. Each bad value gives
  an error that names its key. `set_mode` keeps an unrelated key and the other modes.
  Verify: `python3 -m unittest test_screen_config test_identity -v`
- [x] T005 [P] Files: `screener_backend.py`, `test_screen_backend.py`.
  Create `screener_backend.py` (D6, D7, contract "The decision backend"). It has `Answer`,
  `BackendError(cause)`, `system_one(cfg)`, `hunch_vote(cfg)` and `fake(path)`. It tries the import of
  `promptchain.utils.decision_step` inside `system_one()` and `hunch_vote()`, never at module import (F20). With PromptChain, the backends wrap `SystemOneBackend` and
  `hunch_backend(model, vote=6)`, and a `DecisionError` becomes a `BackendError` with the cause. Without it,
  `system_one` POSTs with `urllib` to `{base_url}/v1/systemone`. Without it, `hunch_vote` runs
  `hunch - -q <json> --raw --timeout T` with `HUNCH_MODEL` and `HUNCH_VOTE=6`. `ROOKERY_SCREENER_FAKE` replaces
  both with `fake`, which writes one line to `<file>.calls` for each call. Each `Answer` carries `impl` and
  `usage`. Needs T001 first. Write `test_screen_backend.py` first.
  Cases:
  - `Contract` runs a local `http.server` on `127.0.0.1` and puts `fixtures/bin` first on `PATH`. Each fixture runs
    on both paths. The fixtures are a good answer, HTTP 500, a timeout with a 1 s timeout and an answer outside the
    labels. They also include hunch `error` and hunch exit 1. The `stdlib` path and the `promptchain` path give the
    same label, confidence and cause. The `promptchain` half skips with a message when the import fails.
  - The body sent to the backend has the model, the state, one `choice` question with the given labels, and no
    `think`, `max_tokens`, `num_predict` or `num_ctx` key (FR-007, FR-034).
  - `hunch_vote` sets `HUNCH_VOTE=6` (read from the fixture log).
  - With `ROOKERY_SCREENER_FAKE` set, no socket opens and no subprocess starts (patch `urllib.request.urlopen`
    and `subprocess.run` to fail the test).
  - Each error gives a `BackendError`, never an `Answer` (FR-012).
  - `import screener_backend` alone does not put `promptchain` in `sys.modules` (F20).
  Verify: `python3 -m unittest test_screen_backend -v`
- [x] T006 Files: `rookery.py`, `postmaster.py`, `rookery_views.py`, `test_escalated.py`.
  Edit `rookery.py`. Needs T003 first (same file). Add `escalate(conn, msg_id, decided_by, reason)`
  (`held` to `escalated` through `_decide`). `release` and `quarantine` accept `held` or `escalated` as the start
  status. `send` takes `thread=None` and writes it. Edit `postmaster.py:144` and `:475` so that both skip
  `escalated`. Edit `rookery_views.mail_mirror` (`rookery_views.py:~484`) so that it lists `escalated` (D14,
  FR-028). Write `test_escalated.py` first. Cases: `escalate` works only from `held`. `release` and
  `quarantine` work from `escalated`, and `unquarantine` does not. An escalated mail is never woken or
  mentioned by the postmaster (`_mention_pass`, wake), and `_answers_open_ask` ignores it (SC-009). The mail
  mirror lists it with no body. `send(thread="t1")` stores the thread.
  Verify: `python3 -m unittest test_escalated test_held_quarantine test_views test_wake test_outbox test_send test_gate -v`
- [x] T007 Files: `screener_t0.py`, `test_screen_t0.py`.
  Create `screener_t0.py` with
  `record(conn, *, mail_id, session_id, stage, check_name, mode, decided_by, label=None, failure=None, attempt=1, confidence=None, band=None, action="none", detail=None, impl=None, usage=None)`
  and `check_decided_by(text)`. `record` refuses a row with both or neither of `label` and `failure`. It refuses an
  `action` other than `none` in shadow mode. It matches `decided_by` against the five forms of FR-010
  (`t0:<check>`, `screener:<model>`, `hunch:vote6:<model>`, `t2:<name>`, `human:<name>`). It inserts the row and
  writes one gate-log line with `path: "screener"` (contract "The gate log line"). Needs T003
  first. Write `test_screen_t0.py` first. Cases: each rule above, and one
  gate-log line for each row (FR-010, FR-012, FR-014, FR-033).
  Verify: `python3 -m unittest test_screen_t0 test_gate_log test_screen_schema -v`
- [x] T008 [P] Files: `cli_workspace.py`, `rookery_team.py`, `test_session_context.py`.
  Edit `cli_workspace.py` so that `rookery up` writes `session_context` and `agent_scope` from the
  team file. It does so in the transaction that makes the session. A second run replaces the rows of that session.
  Edit `rookery_team.py` to accept an optional `ask_budget` (a positive integer, else an error that names it) (D8,
  D17). Add `boss` to `ROLES`: a boss is not a writer and needs no claims (D14, FR-029). A team file with a `boss`
  agent loads. Needs T003 first. Write `test_session_context.py` first. Cases:
  `rookery up --no-launch fixtures/screener/team.json` writes the task, the acceptance and `ask_budget` 3, and one
  `agent_scope` row for each agent with its `does` and `does_not`. A second run gives the same rows, not
  duplicates. `ask_budget: 0` and `ask_budget: "x"` are errors.
  Verify: `python3 -m unittest test_session_context test_team_file test_up_build test_up_idempotent -v`

**Checkpoint**: the schema, the configuration, both backend paths, the record writer and `escalated` work. All
suites are green.

---

## Phase 3: User Story 1 - The screener decides each agent mail and records it (Priority: P1) 🎯 MVP

**Goal**: every stored agent mail gets one T1 record in shadow mode. Sending never waits.

**Independent Test**: send 50 mails of every kind, run `rookery screen run --once` with the fake backend. Each
agent mail has one T1 record, 0 statuses change, and the fake counts 0 calls during the sends.

- [x] T009 [US1] Files: `mail_gate.py`, `turn_block.py`, `test_screen_t0.py`.
  Edit `mail_gate.py`: `submit` and `_submit` take `thread=None` and `block=None`, and pass `thread`
  to `rookery.send`. When a T0 rule of today puts the mail in quarantine at store time, write a `screen_mail` row
  with state `decided` and `final_by` set to that `decided_by`. Edit `turn_block.py:625-629` to pass `thread` and
  the parsed block. Needs T006 and T007 first. Add the cases to `test_screen_t0.py` first. Cases: a block with
  `thread` stores the thread. A plain `rookery send` stores NULL. With `ROOKERY_SCREENER_FAKE` set, 20 sends make 0
  fake calls (FR-001, SC-003). A wrong-recipient quarantine at store gives a `decided` row.
  Verify: `python3 -m unittest test_screen_t0 test_gate test_turn_block test_hooks test_send test_gate_log -v`
- [x] T010 [US1] Files: `screener.py`, `test_screen_run.py`.
  Create `screener.py` with `build_envelope(conn, mail_row, cfg)` and
  `screen_one(conn, mail_id, cfg, backend)` (D8, contract "The decision backend"). The envelope follows the
  data-model shape: compact JSON, keys in order, no `evidence`, scope cards and acceptance from `session_context`
  and `agent_scope` of the mail's session only. A state longer than `envelope_max_chars` gives a `t1` record with
  failure `envelope-too-large` and no backend call. `screen_one` asks the `route` question with the four labels. It
  records the label, the confidence, `band` (`unset` when the bands are null), `decided_by = screener:<model>`,
  `impl`, `prompt_tokens` and `reasoning_tokens`. A `BackendError` gives a failure record with its cause. For
  `wrong-recipient` it asks the `suggest` question over the roster without the sender and the first recipient, plus
  `none`, and records it with stage `suggest`. A `decline` block that names a peer keeps that peer as the `suggest`
  label with `decided_by = t0:gates` and no model call (FR-031). In shadow mode nothing changes the inbox.
  Needs T005, T008 and T009 first. Write `test_screen_run.py` first. Cases: the
  screener records each of the four labels. A 3,001-character state gives `envelope-too-large`, 0 fake calls, and
  no `deliver` (R16, SC-007). `evidence` text never appears in the envelope. Two sessions: each envelope holds only
  its own roster and cards (FR-013). Timeout and bad answer give failures with no label (FR-012). The `suggest`
  labels leave out the sender and the first recipient (FR-008). Every record passes `check_decided_by` (FR-010).
  Verify: `python3 -m unittest test_screen_run test_screen_t0 test_screen_backend -v`
- [x] T011 [US1] Files: `screener.py`, `cli_screen.py`, `rookery_cli.py`, `test_screen_cli.py`.
  Edit `screener.py`. Add `run(sessions=None, once=False, poll=2.0)` with the claim of D12
  (`INSERT OR IGNORE INTO screen_mail` in `BEGIN IMMEDIATE`, an owner token, a lease of `lease_s` plus
  `timeout_s`). `run` also has the queue of FR-005 (real session, non-system sender, no `screen_mail` row, oldest
  first). It has one `try` for each session with a log line to the `screener` stream. It stops on SIGTERM after the
  current mail. It reads the configuration again on each pass. A mode of `off` writes no record. Create
  `cli_screen.py` with `add_parser(sub)`, `screen run` and `screen status` (contract). `status` reports the backend
  path, the Python and each mode. For each session it reports the backlog, the age of the oldest undecided mail,
  and the escalated, failed and decided counts. `host.answers` comes from `GET {base_url}/v1/models` with a 5 s
  timeout. Register `cli_screen.add_parser` in `rookery_cli.py` next to the others (`:116`). `cli_screen.py` imports
  `screener` only inside its handlers (F20). Needs T004 and T010 first. Write `test_screen_cli.py` first. Cases: 50 mails of every
  kind give exactly one T1 record for each agent mail and 0 status changes (SC-001, FR-014). System mail and mail
  quarantined at store get no row (FR-005). A restart in the middle of a batch goes on from the first undecided
  mail, and every mail still has one T1 record (SC-004). Two `screen run --once` subprocesses at the same time give
  one T1 record for each mail (FR-013a). A fake that fails for session A still screens session B. `--session B`
  screens only B. `status --json` has the keys of the contract. `host.answers` uses a local fake lane. `t1` in mode
  `off` gives no record (FR-004). A subprocess runs `rookery hook` and `rookery send` and prints
  `sys.modules`: neither `screener`, `screener_backend` nor `promptchain` is in it (F20, SC-003).
  Verify: `python3 -m unittest test_screen_cli test_screen_run test_screen_config test_hooks -v`

**Checkpoint**: US1 works alone. The screener records a shadow decision for each mail.

---

## Phase 4: User Story 2 - The user compares the decisions and switches a check on (Priority: P1)

**Goal**: the user's calls, the agreement report, the export, the live switch, and the live actions.

**Independent Test**: 150 fake decisions, 140 marked right: the report shows 150 and 93%. At 149 the live switch
refuses and names 1 missing.

- [x] T012 [US2] Files: `cli_screen.py`, `screener_t0.py`, `mail_screen.py`, `test_screen_calls.py`.
  Edit `cli_screen.py`: add `screen review`, `mark`, `report` and `export`, and `screen release`,
  `quarantine` and `escalate` (contract). Add `calls_for_action(conn, mail_id, action, reason, by)` to
  `screener_t0.py` (D14: no model import), which writes the calls of the data-model mapping table for each `t0`, `t1`, `hunch` and `t2`
  decision of the mail. Edit `mail_screen.py` so that its release, quarantine and unquarantine write the same
  calls, and its `--by` default becomes `human:<getpass.getuser()>` (D20). Needs T011 first. Write
  `test_screen_calls.py` first. Cases: each row of the mapping table (FR-015). `mark ID wrong` on a model decision
  refuses and asks for the label. The newest call counts. The report of 150 decisions with 140 right gives
  `compared` 150 and `rate` 0.933. It also gives the disagreements by label pair and the confidence steps, in JSON
  and in text (FR-016). The export has one line for each compared decision with the keys of the contract and no
  body (FR-018). `review` prints no body.
  Verify: `python3 -m unittest test_screen_calls test_held_quarantine test_screen_cli -v`
- [x] T013 [US2] Files: `cli_screen.py`, `test_screen_calls.py`.
  Edit `cli_screen.py`: add `screen mode CHECK MODE` with `screener_config.set_mode`. For `t1`,
  `hunch` and `t2` with `live`, it refuses (exit 2) with fewer than 150 compared decisions. For `t1` it also refuses
  with unset bands (constitution IV). The refusal
  gives the number that is missing (contract text, FR-017). Needs T012 first (same files). Add the class
  `LiveSwitch` to `test_screen_calls.py` first. Cases: 149 compared refuses and says 1. 150 with bands set accepts
  and writes the file. 150 with no bands refuses and names the bands. A T0 check switches to `live` with no count
  (SC-005). `hunch` and `t2` at 149 refuse, and at 150 they accept with no bands.
  Verify: `python3 -m unittest test_screen_calls test_screen_config -v`
- [x] T014 [US2] Files: `mail_gate.py`, `screener.py`, `screener_t0.py`, `test_screen_live.py`. Add the live actions (D13, D14, FR-012a, FR-019, FR-027). Edit `mail_gate.py`: give the existing
  `pre_store` hook a function `screener_t0.pre_hold(conn, recipient, cfg)`. It holds the mail with the reason
  `screening` when `t1` is live and the recipient's review mode is `pre`. Edit `screener.py`: in live mode, act
  only on a mail held with the reason `screening`. Map each label with the verdict table of data-model: `deliver`
  releases, a reject label puts the mail in quarantine with `suggest`. Record the action. A failure or
  `envelope-too-large` on a mail held with `screening` escalates the mail with `retry_at`, and a later pass tries again with `attempt + 1`. A mail
  that `ROOKERY_HOLD` held gets shadow records only. In `post` mode the mail is not held, and a reject label sends
  the escalation notice. A live failure on a mail that is not held with `screening` gives `screen_mail.state =
  failed` and the notice, and the mail keeps its status (D14). Add `screener_t0.notify_escalated(conn, mail_id,
  reason)`. It sends a notice from `rookery` to the agent with the role `boss` in the session (from `up_agents`),
  when one exists. It also fires the plugin `sink` event `escalated`. Both carry the mail ID, the sender, the
  recipient and the reason, and no body. Every escalation calls it. Needs T006 and T011 first. Write `test_screen_live.py` first. Cases: live
  `pre` holds with `screening`, then `deliver` releases it. Each verdict maps to its action (FR-027). A failing
  fake escalates, and no lease or time releases it. A later good answer releases it with `attempt` 2 (FR-012a).
  `ROOKERY_HOLD=1` mail stays held with shadow records. `post` mode delivers at once. A failure in `post` mode gives `failed`, a notice, and no status change. The
  notice reaches the boss with no body. With no boss, only the sink event fires (FR-029). The loop reason is never
  passed to `quarantine` (FR-002).
  Verify: `python3 -m unittest test_screen_live test_screen_run test_escalated test_gate test_held_quarantine test_wake -v`

**Checkpoint**: US2 works. The user can compare, export and switch a check on, and a live check acts.

---

## Phase 5: User Story 3 - A thread gets one review, and a closed thread stays closed (Priority: P1)

**Goal**: the `threads` table, B3, the closers and the real `_threads/INDEX.md`.

**Independent Test**: open a thread, close it with `rookery thread close`, send again. With B3 live the gate
refuses the mail and bounces it. With B3 in shadow mode the gate delivers it with a `would-deny` record.

- [x] T015 [US3] Files: `screener_t0.py`, `mail_gate.py`, `rookery.py`, `screener.py`, `test_threads.py`. Add
  `screener_t0.thread_check(conn, session_id, thread, sender, held, cfg)`. It makes the
  `threads` row for a new thread (`held` or `open`), and returns `would-deny` or `denied` for a closed thread. Call
  it from `mail_gate._submit`. With `thread` live, a mail on a closed thread is not stored. The gate returns the
  outcome `refused` with the reason `thread-closed`. It sends the B3 bounce text of the contract. It writes a `t0`
  record with `mail_id` NULL and label `denied`. In shadow mode the gate stores the mail and writes `would-deny`.
  Edit `rookery.release` so that releasing the first mail of a `held` thread makes it `open`. Edit `screener.py`:
  when `t1` is live and the first mail of a thread gets `deliver`, the thread becomes `approved`. Later mail on an
  approved thread gets a `t1` record with label `skipped` and no call. The exceptions are a `decline`, a mail with
  a `duplicate` record, and a loop hold (D16, FR-020 to FR-022). Needs T009 and T014 first. Write `test_threads.py`
  first. Cases: the first mail makes the row with the right status. The same thread value in two sessions gives two
  rows. Live: an approved thread skips T1 with 0 fake calls. The three exceptions get a full T1 (the test inserts the
  `duplicate` record itself, because T021 builds that check later). B3 live: refused,
  not stored, a bounce, and the recipient is not woken (SC-008, FR-031). B3 shadow: stored with `would-deny`. In
  shadow mode a thread never becomes `approved`.
  Verify: `python3 -m unittest test_threads test_screen_t0 test_screen_live test_gate test_wake -v`
- [x] T016 [US3] Files: `cli_screen.py`, `screener.py`, `turn_block.py`, `test_threads.py`.
  Add the closers (D16, FR-022a). Edit `cli_screen.py`:
  `rookery thread close SESSION THREAD [--as AGENT] [--json]` (contract). Edit `screener.py`: a live quarantine by
  the screener closes the thread with `close_reason = veto`. Edit `turn_block.py`: a `handoff` with `status: done`
  from the agent that opened the thread closes it with `handoff:<agent>`. Needs T015 first. Add the cases to
  `test_threads.py` first. Cases: each of the four closers sets `closed_by`, `close_reason` and `closed_at`. `--as`
  with an agent that is not the boss exits 2. A `done` handoff from another agent does not close. Closing a closed
  thread exits 0 and says so. No code path closes a thread because of time (a scan for `closed` writes outside
  these four sites).
  Verify: `python3 -m unittest test_threads test_turn_block test_hooks test_screen_cli -v`
- [x] T017 [P] [US3] Files: `rookery_views.py`, `test_views.py`.
  Edit `rookery_views.threads` (`rookery_views.py:447`): build `_threads/INDEX.md` from the
  `threads` table, grouped by session and thread, with the status and the mail count from `inbox.thread`. Mail
  with no thread is not listed (FR-023). Needs T015 first. Add the cases to `test_views.py` first. Cases: two
  threads and three mails with no thread give two rows with their status. No body text appears. `regen`
  changes no `read_at`.
  Verify: `python3 -m unittest test_views -v`

**Checkpoint**: US3 works. Threads exist, get one review, and stay closed.

---

## Phase 6: User Story 4 - Shaky decisions get a second opinion, and hard ones go up a tier (Priority: P2)

**Goal**: hunch in the review band, T2 batches, and escalation notices.

**Independent Test**: with bands set, a review-band answer calls the fake hunch. Agreement acts. Disagreement with
no T2 command leaves the mail `escalated`, and the boss gets a notice with no body.

- [x] T018 [US4] Files: `screener.py`, `test_screen_tiers.py`.
  Edit `screener.py` (D11, D14, FR-009, FR-029). With bands set for `t1`, apply the `band()` rule:
  `accept` acts, `review` asks `hunch_vote` the same question, and lower escalates. Record the hunch answer with
  stage `hunch` and `decided_by = hunch:vote6:<model>`. Agreement acts. Disagreement escalates. With unset bands,
  hunch never runs. When `t1` is live and `hunch` is not live, a review-band answer escalates, and the hunch record
  is still written (D11). Each escalation calls `screener_t0.notify_escalated` (T014). Needs T014 first. Write `test_screen_tiers.py` first. Cases: accept acts
  with 0 hunch calls. Review calls hunch once (the fake log shows `HUNCH_VOTE=6`). Agreement releases. Disagreement
  escalates. Unset bands give 0 hunch calls. `t1` live with `hunch` in shadow: a review-band answer escalates with a
  notice. In shadow mode the screener writes the same records and nothing acts.
  Verify: `python3 -m unittest test_screen_tiers test_screen_live test_escalated -v`
- [x] T019 [US4] Files: `screener.py`, `fixtures/screener/t2_ok.py`, `fixtures/screener/t2_bad.py`, `test_screen_tiers.py`.
  Edit `screener.py`: add the T2 pass of D15. When `t2.command` has a value, each pass sends at most
  `t2.batch` escalated mails as JSON lines on stdin. It reads `{"mail_id", "label", "reason"}` lines, records each
  with stage `t2` and `decided_by = t2:<basename>`, and acts in live mode. A non-zero exit, a timeout or a line
  that is not valid keeps the mail `escalated`, with a failure record `bad-answer`. With no command, escalated mail
  waits (FR-003). Add `fixtures/screener/t2_ok.py` and `fixtures/screener/t2_bad.py`. Needs T018 first. Add the
  cases to `test_screen_tiers.py` first. Cases: no command leaves the mail escalated. The good script decides two
  of three mails, and the third keeps a failure. The batch size limits the stdin lines. The bad script keeps all of
  them escalated. No code names a T2 model (scan).
  Verify: `python3 -m unittest test_screen_tiers test_escalated -v`

**Checkpoint**: US4 works. The chain has all three tiers, and a person sees each escalation.

---

## Phase 7: User Story 5 - The broker stops repeat asks, asks over budget, and injection (Priority: P2)

**Goal**: the injection pattern set, the ask budget and duplicate suppression, each in shadow mode first.

**Independent Test**: in shadow mode each fixture gives its record and the gate delivers the mail. In live mode the
injection fixture goes to quarantine, and the recipient is not woken.

- [x] T020 [US5] Files: `screener_t0.py`, `mail_gate.py`, `test_screen_injection.py`.
  Add the injection pattern set to `screener_t0.py` (D18): fixed case-insensitive patterns, one for
  each family of `fixtures/screener/injection.txt`. Call it from `mail_gate._submit` after `inspect_inbound`. A
  match writes `injection-suspect` with the pattern name in `detail`. With `injection` live, the gate puts the mail
  in quarantine with the reason `injection-suspect`. Needs T009 first. Write `test_screen_injection.py` first.
  Cases: each fixture line matches its family. Ten normal asks match nothing. Shadow: a record, and the gate
  delivers the mail (SC-006). Live: quarantined, and a postmaster wake pass does not wake the recipient (R17).
  `inspect_inbound` still acts as before.
  Verify: `python3 -m unittest test_screen_injection test_gate test_wake test_screen_t0 -v`
- [x] T021 [US5] Files: `screener_t0.py`, `mail_gate.py`, `test_screen_b2.py`.
  Add the ask budget and duplicates to `screener_t0.py` (D17, FR-025, FR-026). Budget: count the
  asks of the session and task. The limit is `session_context.ask_budget`, else `screener.ask_budget`. Over the
  limit writes `over-budget`. Duplicate: normalize the `say` text (lower case, letters, digits and single spaces).
  Store its SHA-256 in `screen_mail.ask_hash`. Look for the same sender, recipient, task and hash inside
  `duplicate_window_s`. A match writes `duplicate`. In live mode each one holds the mail with the reason `budget`
  or `duplicate` and sends the bounce text of the contract. Call both from `mail_gate._submit`. Needs T020 first
  (same files). Write `test_screen_b2.py` first. Cases: the team value 3 wins over the config value. The fourth ask
  writes `over-budget`. The same question with other spaces and case is a duplicate inside the window, and not
  after it. Another recipient is not a duplicate. Live: held and a bounce, for each. Shadow: delivered. An A↔B
  ping-pong fixture on one task stops at the loop limit with the 002 hold (R18, SC-010).
  Verify: `python3 -m unittest test_screen_b2 test_screen_injection test_gate test_turn_block -v`

**Checkpoint**: US5 works. B2 and the injection set record in shadow mode and act in live mode.

---

## Phase 8: User Story 6 - Code applies the worker and peer gates from the flags (Priority: P3)

**Goal**: the anomalies G1, G2, G3 and G5.

**Independent Test**: an ask with `task_complete: true` gives `g1-task-complete`. An ask with
`gap_in_my_scope: true` gives `g3-gap-in-scope`.

- [ ] T022 [US6] Files: `screener_t0.py`, `mail_gate.py`, `test_screen_gates.py`. Add
  `screener_t0.gate_anomalies(block, budget_over, b1_failed)` (D19, FR-030). On an ask:
  `task_complete: true` gives `g1-task-complete`, an empty `gap` gives `g2-no-gap`, `gap_in_my_scope: true` gives
  `g3-gap-in-scope`, and over budget or a B1 failure gives `g5-ask-not-allowed`. Each is its own record with check
  `gates`. Call it from `mail_gate._submit`. With `gates` live, the gate holds the mail and then escalates it, and
  `screener_t0.notify_escalated` runs. Needs T021 and T018 first. Write `test_screen_gates.py` first. Cases: each anomaly
  alone. All four on one ask give four records. A non-ask kind gives none. Shadow: delivered. Live: escalated with
  a notice. No code applies G4 or P1 (a scan for `peer_match` and `in_my_scope` in the new modules).
  Verify: `python3 -m unittest test_screen_gates test_screen_b2 test_gate test_escalated -v`

**Checkpoint**: all six user stories work.

---

## Phase 9: Polish and cross-cutting concerns

- [ ] T023 [P] Files: `test_gate_scan.py`.
  Edit `test_gate_scan.py` (D23). Add `screener.py` and `cli_screen.py` to `QUARANTINE_OK`. Watch
  `rookery.escalate` with the allow list `rookery.py`, `screener.py`, `cli_screen.py`, `mail_screen.py`. Add
  rules: no screener module passes the literal `"loop"` to `quarantine`. `mail_gate.py`, `turn_block.py`,
  `cli_hook.py` and `screener_t0.py` never import `screener_backend` or `promptchain`. No non-test module has
  `max_tokens`, `num_predict`, `num_ctx` or `"think"`. No non-test module has the literal `192.168.0.159` except
  `screener_config.py` and `rookery_config.default.json`. No module imports `promptchain` at the top level.
  `mail_screen.py` never imports `screener` or `screener_backend`. `cli_screen.py` imports `screener` only inside a
  function (D23). Needs T022 first.
  Verify: `python3 -m unittest test_gate_scan -v`
- [ ] T024 [P] Files: `test_text_hints.py`.
  Add a case to `test_text_hints.py`. It collects every bounce, notice, refusal and error text of
  `screener.py`, `screener_t0.py`, `screener_config.py`, `cli_screen.py` and `rookery_names.py` into one temporary
  file. It runs `ste-lint --max-score 2.0 --glossary ste-glossary.txt` on that file (FR-032, SC-012). Fix each text
  that fails. Needs T022 first. Skip the case only when `ste-lint` is not on `PATH`.
  Verify: `python3 -m unittest test_text_hints -v`
- [ ] T025 [P] Files: `README.md`, `docs/QUICKSTART.md`, `CHANGELOG.md`, `skills/rookery/SKILL.md`, `test_text_hints.py`.
  Edit `README.md`, `docs/QUICKSTART.md`, `CHANGELOG.md` and `skills/rookery/SKILL.md`. Document
  `rookery screen` (run, status, review, mark, report, export, mode, release, quarantine, escalate) and
  `rookery thread close`. Document shadow mode first and the 150 rule. Document the `screener` configuration and
  the optional PromptChain import. Document session names of three words. CHANGELOG gets "### Added". Add a case to
  `test_text_hints.py` first: SKILL.md names `rookery screen run` and `shadow`. Needs T024 first (same test file).
  Verify: `python3 -m unittest test_text_hints -v && for f in README.md docs/QUICKSTART.md CHANGELOG.md skills/rookery/SKILL.md; do ste-lint $f --max-score 2.0 --glossary ste-glossary.txt | tail -1; done`
- [ ] T026 Files: `bootstrap.py`, `test_join_rename.py`.
  Add `rookery_names.py`, `screener_config.py`, `screener_backend.py`, `screener_t0.py`, `screener.py` and
  `cli_screen.py` to `bootstrap._PY_MODULES` in one edit. Add a case to `test_join_rename.py` first that asserts all
  six are in the list. Run the full suite, the coverage gate, and quickstart 1 on a copy of the live database (in
  `~/Documents/code/.worktrees/`, never `/tmp`). Needs T023, T024 and T025 first. Fix any test that fails in this
  task.
  Verify: `python3 -m unittest discover -p 'test_*.py' && spec-pane cover . 3 ~/Documents/code/inter-agent-comms/okf/requirements/rookery-0-7.md`

---

## After the waves (manual, with the user)

These steps have no checkbox, so dev-kid does not schedule them.

- The final audit: one adversarial audit of the whole branch, with Fable.
- Quickstart 5, the live check on the model host (SC-002, SC-013). Run it in an isolated herdr workspace with a
  scratch database. Run it once under miniconda and once under `/usr/bin/python3`. Record both outputs in
  quickstart.md. Each stays UNTESTED until it runs.
- Update the OKF bundle (D24): R16, R17 and R18 become proven with their test names, and R15 becomes built. The
  R18 row text becomes "held, bounce". Add the design notes to `design/screener-chain.md`. This is outside the
  repo.
- Push, open the PR, check that it can merge, merge, and mirror the merge to Gitea with the fast-forward PR.
- Deploy waits for the user (quickstart 6).

---

## Phase requirements by task

| RQ | Tasks | RQ | Tasks |
|---|---|---|---|
| RQ-3.1 | T007, T020, T021, T022 (loop: D2, T014) | RQ-3.8 | T015, T016, T017 |
| RQ-3.2 | T005, T010 | RQ-3.9 | T021 (B1 unchanged: T026) |
| RQ-3.3 | T010, T018, T019 | RQ-3.10 | T014 |
| RQ-3.4 | T007 | RQ-3.11 | T012 |
| RQ-3.5 | T010 | RQ-3.12 | T006, T018 |
| RQ-3.6 | T011, T012, T013 | RQ-3.13 | T024, T025 |
| RQ-3.7 | T014 | RQ-3.14 | T010, T011 |
| RQ-R.1 | T022 | RQ-R.2 | T010 (P1 suggest), T015 (P0) |

Risk proofs: R15 T013 (built). R16 T010. R17 T020. R18 T021.

## Dependencies and execution order

- Phase 1: T001 and T002 have no dependency.
- Phase 2: T003 first. Then T004 and T005 (T005 needs T001) in parallel with T003. T006, T007 and T008 need T003.
  T006 shares `rookery.py` with T003.
- Phase 3 (US1): T009 needs T006 and T007. T010 needs T005, T008, T009. T011 needs T004, T010.
- Phase 4 (US2): T012 needs T011. T013 needs T012. T014 needs T006, T011.
- Phase 5 (US3): T015 needs T009, T014. T016 needs T015. T017 needs T015.
- Phase 6 (US4): T018 needs T014. T019 needs T018.
- Phase 7 (US5): T020 needs T009. T021 needs T020.
- Phase 8 (US6): T022 needs T018 and T021.
- Phase 9: T023 and T024 need T022. T025 needs T024. T026 needs T023, T024, T025.

User story order: US1, then US2 and US3 (P1), then US4 and US5 (P2), then US6 (P3). US5 can run beside US2 to
US4, because T020 and T021 touch `screener_t0.py` and `mail_gate.py` only after T009. Then T014 and T015 must
not run in the same wave as T020 or T021, because they share `mail_gate.py`.

## Parallel opportunities

```text
Wave A: T001, T002, T003, T004                 (fixtures; names + rookery_identity.py; rookery.py; screener_config.py)
Wave B: T005, T006, T007, T008                 (backend; rookery.py + postmaster; screener_t0.py; cli_workspace.py)
Wave C: T009                                   (mail_gate.py, turn_block.py)
Wave D: T010, T020                             (screener.py; screener_t0.py + mail_gate.py injection)
Wave E: T011, T021                             (cli_screen.py + screener.py; screener_t0.py + mail_gate.py B2)
Wave F: T012                                   (cli_screen.py, mail_screen.py, screener_t0.py)
Wave G: T013, T014                             (cli_screen.py; mail_gate.py + screener.py + screener_t0.py)
Wave H: T015                                   (screener_t0.py, mail_gate.py, rookery.py, screener.py)
Wave I: T016, T017, T018                       (closers: cli_screen + turn_block + screener; views; tiers: screener)
                                               T016 and T018 share screener.py: dev-kid runs them in order
Wave J: T019, T022                             (T2: screener.py; gates: screener_t0.py + mail_gate.py)
Wave K: T023, T024 → T025 → T026
```

## Implementation strategy

1. MVP: Phases 1 to 3. The screener records a shadow T1 decision for each mail. Nothing changes for agents.
2. Add Phase 4. The user can compare decisions and switch a check on. This is the first release a user can run
   day to day.
3. Add Phases 5 to 8 in order. Each phase ends green and gets its wave audit.
4. Phase 9, then the manual steps.

## Notes

- Seven test files are not in the plan's file list: `test_screen_config.py`, `test_session_context.py`,
  `test_screen_live.py`, `test_screen_tiers.py`, `test_screen_injection.py`, `test_screen_b2.py` and
  `test_screen_gates.py`. They keep one test file for each concern, so that parallel tasks never share a file.
- The record writer lives in `screener_t0.py`, not `screener.py`, so that the gate can write records without an
  import of the model backend (D6).
- If a test shows that the plan is wrong, stop. Record the finding in research.md and fix the plan before the
  code (document at discovery).
