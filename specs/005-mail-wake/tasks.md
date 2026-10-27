---
type: Tasks
task: 005-mail-wake
updated: 2026-10-27
revision: 1
---

# Tasks: Event-driven mail wake

**Input**: the design documents in `specs/005-mail-wake/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/cli.md](contracts/cli.md) and [quickstart.md](quickstart.md).

**Tests**: MANDATORY (constitution II). Each FR and each automatic SC gets the test that proves it, in this PR (see
"Requirements by task"). Use fixtures, not mocks. Each task writes its test first, sees it fail, then writes the
code. No task leaves a failing test.

**Layout**: a flat Python repo. All paths start at the repo root.

## Format: `- [ ] T### [P?] [Story?] Description`

- **[P]**: the task can run in parallel. It uses different files and has no open dependency in its phase.
- **[Story]**: the user story of the task (US1 to US7).
- **Needs T0xx first.**: an explicit dependency on another task.
- **Verify:** the exact command that proves the task.

## Rules for every task

- Read the research entry that the task names (Rn in [research.md](research.md)) before you write code.
- A task that writes a test also writes the code that makes it pass. No `skip` or `expectedFailure` hides missing
  code. Skip a test only when an outside tool (`ste-lint`, `git`) is missing, and give the reason.
- No test calls a model, runs real herdr or tmux, or opens `~/Documents/code/rookery/rookery.db` (FR-018). Pane
  commands are the fakes in `fixtures/bin` (`herdr` writes its arguments to `$HERDR_RECORD`). Use
  `test_wake.WakeBase` or the same config: a temporary `HOME`, `ROOKERY_DB`, `ROOKERY_ROOT` and `PATH` with
  `fixtures/bin` first.
- Import cycles: `postmaster` imports `mail_gate`. So `mail_gate.py`, `rookery.py` and `screener.py` import
  `rookery_wake` **inside** the function that calls it, and `rookery_wake.py` imports `postmaster` inside its
  functions. No top-level import of `rookery_wake` in those three modules.
- No code calls a model, and no code adds `max_tokens`, `num_predict`, `num_ctx` or `think`.
- No core module names `herdr`, `tmux` or a harness. Pane commands come from the `wake` config (FR-015, FR-026).
- `after_pending` and `spawn` never raise into the caller. Every failure becomes one log line (FR-002, FR-014).
- Every sleep and clock in the sweep and in the remote follower comes from an argument, so tests inject them. No
  test sleeps more than 2 seconds.
- Each text that an agent or the user reads passes `ste-lint --max-score 2.0 --glossary ste-glossary.txt`.
- **Base suites** (each `Verify:` line writes them out, because dev-kid runs the line as it is):
  `python3 -m unittest test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`.
  Each `Verify:` of a code task runs its own tests and these suites in one `unittest` call.
- T003 adds `postmaster.py`, `rookery_wake.py` and `rookery_remote.py` to `bootstrap._PY_MODULES` in one edit. No
  other task edits `bootstrap.py`.
- `rookery_cli.py` gets the subcommand `wake` in T005, `sweep` in T012 and `check` in T019, in that order. No other
  task edits it.

---

## Phase 1: Config

**Purpose**: the config keys and the bundle entry that later tasks read.

- [x] T001 [P] Files: `screener_config.py`, `test_screener_enabled.py`. Add `enabled` (default `False`) to the
  defaults of the `screener` block. Add `screening_on(conn, config, session_id)`. It returns true when
  `screener.enabled` is true. It also returns true when the session has a binding of kind `screener-enabled` with
  value `1` (owner type `session`). Write the test first. No config gives false. `enabled: true` gives true. The
  session binding gives true for that session only. `check()` refuses a value that is not a bool in config, like
  the other keys. (R6)
  Verify: `python3 -m unittest test_screener_enabled test_screen_config test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T002 [P] Files: `rookery_wake.py`, `test_wake_core.py`. Create `rookery_wake.py` with these functions.
  `program_argv()` names the folder or zip archive that holds `rookery_cli.py`. It returns `[sys.executable,
  "<dir>/rookery_cli.py"]` or `[sys.executable, "<archive>"]`. `spawn(args, log_root=None)` runs `subprocess.Popen`
  of `program_argv() + args` with `start_new_session=True`, `stdin/stdout/stderr=DEVNULL` and `close_fds=True`. It
  never waits. It keeps each child handle in a module list. At each call it calls `poll()` on the list and drops the
  children that ended, so a long-running parent (the MCP service, the mailroom service) never collects zombie
  processes. It catches any exception, logs `wake-spawn-failed` and returns `False`. `try_lock(path)` opens the
  file and calls `fcntl.flock(LOCK_EX | LOCK_NB)`. It returns the open file or `None`, and makes the folder.
  `resolve_root(conn, session_id)` returns the `rookery-root` session binding, else `ROOKERY_ROOT`, else `None`.
  `sweep_interval(config)` reads top-level `sweep_s` (default 300, raised to 300 when below). It returns
  `(seconds, raised: bool)`. Write the test first. Test `program_argv` from a checkout and from a built zipapp
  (`bootstrap.build_pyz` into the temp folder). `spawn` returns in under 50 ms for a child that sleeps 2 s, and the
  child really runs (it writes a file). `spawn` with a bad path logs `wake-spawn-failed` and returns `False`. Fifty `spawn` calls of a child that exits at
  once, in one process, leave at most 2 zombie children (count the children in state `Z` under `/proc`). A
  second `try_lock` on the same path returns `None` while the first holds it. It works after the first closes. A
  child process that holds the lock frees it when it dies. Test `resolve_root` in all three cases. Test
  `sweep_interval` for 0.5, 300 and 600. (R2, R3, R4, R7)
  Verify: `python3 -m unittest test_wake_core test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T003 [P] Files: `bootstrap.py`, `rookery_remote.py`. Create `rookery_remote.py` with only its docstring
  (the remote check, research R8; T019 fills it). Add `postmaster.py`, `rookery_wake.py` and `rookery_remote.py`
  to `_PY_MODULES` after `cli_screen.py`. The installed zipapp needs `postmaster.py`, because `rookery wake` and
  `rookery sweep` import `postmaster.wake_one`. All modules that `postmaster.py` imports at the top are already in the
  list.
  Verify: `python3 -c "import bootstrap,zipfile,tempfile,os; p=os.path.join(tempfile.mkdtemp(),'r.pyz'); bootstrap.build_pyz(p); n=zipfile.ZipFile(p).namelist(); assert {'postmaster.py','rookery_wake.py','rookery_remote.py'} <= set(n), n"`

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: one wake code path (FR-004, D2, R5).

- [x] T004 Files: `postmaster.py`, `test_wake_split.py`. Split `wake_identities` into a loop. Add the function
  `wake_one(conn, root, config, ident, run=subprocess.run, skip_logged=None, backoff=None, now=time.time, nopush=None,
  probe_state=None)`. It returns the outcome string (`pushed`, `nothing`, `busy`, `wake-failed`, `no-idle-state`,
  `no-wake-binding`, `unsafe-name`). Move the pane typing into `type_notice(entry, value, text, run)`. It returns
  `None` or the error text. Keep the back-off record in a file: `ROOT/postmaster/backoff/AGENT_ID.json` with
  `until`, `delay`, `newest`, `logged`. `wake_one` reads it when the `backoff` argument is `None`. It writes or
  removes it. A file that does not parse counts as no back-off. `wake_identities` keeps its signature and its
  return value. Write the test first. `wake_one` returns each outcome. A failed wake writes the back-off file. A
  second `wake_one` in a new call (no shared dict) waits for it. A wake that succeeds removes the file. A corrupt
  back-off file counts as none. (R5, R4)
  Verify: `python3 -m unittest test_wake_split test_wake test_turn_block test_send test_hooks test_outbox test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`

**Checkpoint**: `test_wake.py` passes unchanged. The wake rules live in `wake_one`.

---

## Phase 3: User Story 1 - New mail wakes an idle agent at once (Priority: P1) 🎯 MVP

**Goal**: each step that makes a mail `pending` starts one wake for the recipient (FR-001, FR-002, FR-005,
FR-006, FR-012, FR-014).
**Independent test**: store an ask for an idle identity bound to the fake herdr; one push appears in
`$HERDR_RECORD`; a `status` mail adds none.

- [x] T005 [US1] Files: `rookery_wake.py`, `rookery_cli.py`, `test_wake_lock.py`. Add `run_wake(conn, root,
  config, session_id, agent_id, rounds=3)`. Take `ROOT/postmaster/lock/AGENT_ID.lock` with `try_lock`. If busy,
  log `wake-busy` and return it. Else call `postmaster.wake_one` for the identity and release the lock. Then read
  the pending mail again. Repeat when mail that `_needs_push` and `pushed/` does not list is there, up to `rounds`. Repeat only after the
  outcome `pushed` or `nothing`. Stop at once after `busy`, `wake-failed`, `no-idle-state`, `no-wake-binding` or
  `no-root`, because the next round fails the same way.
  With no root, log `no-root`. Add the subcommand `rookery wake --session S --agent A [--json]`. Follow
  [contracts/cli.md](contracts/cli.md) (exit 2 bad input, 3 no session or agent, 4 database busy, else 0). Write
  the test first. One wake pushes once. A held lock gives `wake-busy` and no push. Inject a mail through a `run`
  stand-in that stores a mail during the first push. That mail gets a second push in round 2. The loop stops at 3
  rounds. After `wake-failed`, no second round runs and the log has one `wake-failed` line. The CLI prints the JSON fields and the exit codes. Needs T002, T004 first. (R4, R5)
  Verify: `python3 -m unittest test_wake_lock test_wake_core test_wake_split test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T006 [US1] Files: `rookery_wake.py`, `test_wake_event.py`. Add `after_pending(conn, msg_ids)`. For each ID,
  read the row. Skip rows that are not `pending`, are in `legacy`, are in a session that is not `open`, are a
  self-send, or that `postmaster._needs_push` rejects. Group by recipient. Call `spawn(["wake", "--session", sid,
  "--agent", aid])` once per recipient. Never raise: catch everything and log `wake-spawn-failed` with the error.
  Write the test first with `spawn` replaced by a recorder. An ask spawns one wake. A `status` mail, a self-send,
  a `legacy` mail and a closed session spawn none. Two mails for one agent spawn one wake. A database error inside
  gives one log line and no exception. Needs T005 first. (R1, R2)
  Verify: `python3 -m unittest test_wake_event test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T007 [P] [US1] Files: `mail_gate.py`, `test_wake_event_gate.py`. After the commit in `_submit` that stores
  a mail as `pending`, call `rookery_wake.after_pending(conn, [msg_id])` (import inside the function). A refused,
  held or quarantined mail calls nothing. Write the test first with a recorder for `spawn`. `rookery send` of an
  ask spawns one wake. A mail held by the loop breaker spawns none. A quarantined mail spawns none. A `spawn` that
  raises still returns the normal send result. Needs T006 first. (R1)
  Verify: `python3 -m unittest test_wake_event_gate test_gate test_send test_turn_block test_hooks test_outbox test_wake test_identity test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T008 [P] [US1] Files: `rookery.py`, `test_wake_event_release.py`. In `release`, `unquarantine` and
  `requeue_dlq`, after the commit, call `rookery_wake.after_pending(conn, [msg_id])` (import inside). Do not call it
  in `_decide_from_open` (it may not commit) or in `requeue_stale` (v1). Write the test first. Each of the three
  spawns one wake for an ask. A release of a `status` mail spawns none. Needs T006 first. (R1)
  Verify: `python3 -m unittest test_wake_event_release test_held_quarantine test_escalated test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T009 [P] [US1] Files: `screener.py`, `test_wake_event_screener.py`. Collect the IDs that a screening step
  releases with `_decide_from_open(..., "pending", ...)` (`screener.py:453`, `:645`). Call
  `rookery_wake.after_pending` after the commit of that step (import inside). Write the test first with the fake
  screener backend. A live `t1` that delivers a held ask spawns one wake after the commit. A mail that stays held
  or escalates spawns none. Needs T006 first. (R1, Q1)
  Verify: `python3 -m unittest test_wake_event_screener test_screen_run test_screen_live test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T010 [US1] Files: `test_wake_detached.py`. End-to-end with no stand-in: a real `rookery send` (subprocess)
  of an ask to an idle identity bound to the fake herdr. Within 5 seconds `$HERDR_RECORD` has one push and the log
  has `pushed` (SC-001). Ten asks within 1 second give at most 10 pushes, and no mail ID appears twice across the
  announced IDs of `pushed/` and the notices (SC-005). Time 20 sends with the wake on and 20 with `spawn` turned
  off by a test switch (`ROOKERY_WAKE=off`, read in `after_pending`). The median grows by at most 50 ms (SC-006).
  Add the `ROOKERY_WAKE=off` switch (also an operator kill switch, documented in T022) to `rookery_wake.after_pending` in this task. Needs T007 first.
  Verify: `python3 -m unittest test_wake_detached test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`

**Checkpoint**: an idle agent gets a notice from the gate, a release, an unquarantine, a requeue and the screener.

---

## Phase 4: User Story 2 - An agent that ends a turn with unread mail gets woken (Priority: P1)

**Goal**: FR-003.
**Independent test**: store an ask for a busy identity, run the Stop hook, see one wake.

- [x] T011 [US2] Files: `cli_hook.py`, `test_wake_stop.py`. In `stop`, after each `_write_idle` and only when
  `ROOKERY_URL` is not set, read the pending mail of the identity. When one row passes `_needs_push` and
  `postmaster/pushed/AGENT_ID.json` does not list it, call `rookery_wake.spawn(["wake", ...])`. Wrap it so that the
  hook never raises and never prints. Write the test first (recorder for `spawn`). An unread ask spawns one wake.
  An announced ask spawns none. No pending mail spawns none. A `status` mail spawns none. A `spawn` that raises
  leaves stdout empty and the exit code 0. Add one end-to-end case with no recorder: run the Stop hook as a
  subprocess for an idle-bound identity with an unread ask. Within 5 seconds `$HERDR_RECORD` has one push (SC-002).
  Needs T005 first. (R1, FR-003)
  Verify: `python3 -m unittest test_wake_stop test_hooks test_turn_block test_send test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`

---

## Phase 5: User Story 3 - Idle costs nothing, and a lost event is still caught (Priority: P1)

**Goal**: FR-007, FR-008, FR-016, FR-017, FR-022.
**Independent test**: one sweep round with an injected clock wakes an agent whose event was lost.

- [x] T012 [US3] Files: `postmaster.py`, `rookery_cli.py`, `test_wake_sweep.py`. Add `run_session_sweep(root,
  config, session, sleep=time.sleep, now=time.time)` to `postmaster.py`. Expose it as `postmaster.py --session S` and
  as the subcommand `rookery sweep --session S [--root ROOT]` (`rookery_cli.py`). `rookery up` starts the subcommand,
  so that the sweep works from the zipapp. Take
  `ROOT/postmaster/lock/sweep-SESSION_ID.lock` (`sweep-busy` and exit 0 when held). Each round calls
  `wake_identities` for the identities of that session only (add an optional `session_id` filter). Sleep
  `sweep_interval(config)` seconds through an injected `sleep`. Exit 0 when the session is not `open`. In the mode
  with no `--session` and no `--nodes`, raise a `--poll` below 300 to 300. Print one `NOTE:` line to stderr and log
  `poll-raised` once (R9). `--nodes` keeps its poll. Write the test first with an injected clock and sleep. One
  round wakes a lost event once. A second round does not wake again for an announced mail. A closed session ends
  the loop. A second sweep gets `sweep-busy`. `--poll 0.5` gives `NOTE:`, `poll-raised` and a 300 s sleep. The
  sweep of session A never wakes an identity of session B. `rookery sweep --session S` (subprocess, with the test
  switch `ROOKERY_SWEEP_ROUNDS=1`) runs one round and exits 0. Needs T002, T004, T005 first. (R7, R9)
  Verify: `python3 -m unittest test_wake_sweep test_wake_lock test_wake test_turn_block test_send test_hooks test_outbox test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T013 [P] [US3] Files: `test_wake_cost.py`. Cost proofs with the fake screener backend counting calls. Replace
  `spawn` with a recorder. One simulated idle hour (injected clock) of the sweep with no mail gives 0 model calls
  and at most 12 rounds (SC-003). Test 2, 5 and 10 agents, where each agent sends one ask to one other agent. The
  number of spawned wakes equals the number of asks (SC-004). Needs T006, T012 first.
  Verify: `python3 -m unittest test_wake_cost test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`

---

## Phase 6: User Story 4 - The screener starts when mail arrives (Priority: P2)

**Goal**: FR-009, FR-010, FR-011, D3.
**Independent test**: with screening on, a stored mail gives one screening pass for its session.

- [x] T014 [US4] Files: `cli_screen.py`, `test_screen_event.py`. `rookery screen run --session S --once`: take
  `ROOT/postmaster/lock/screen-SESSION_ID.lock` (`screen-busy` and exit 0 when held). Run
  `screener.run(sessions=[S], once=True)` and release the lock. Then look for mail with no decision. Repeat at most
  3 rounds. Change the default `--poll` of the loop to 300. Write the test first with the fake backend. One pass
  decides the mail. A held lock gives `screen-busy`. A mail stored during the pass gets a decision in round 2. The
  loop stops at 3 rounds. Needs T002 first. (R6)
  Verify: `python3 -m unittest test_screen_event test_screen_cli test_screen_run test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T015 [US4] Files: `mail_gate.py`, `test_screen_event_gate.py`. After the store in `_submit`, call
  `rookery_wake.spawn(["screen", "run", "--session", sid, "--once"])`. Do this for every stored
  mail when `screener_config.screening_on(conn, config, sid)` is true. `screener.run` picks the mail with no decision
  by itself. With screening off, spawn
  nothing. Write the test first (recorder). Off by default spawns no screening. `screener.enabled: true` spawns one.
  The session binding spawns one for that session only. A `spawn` that raises keeps the send result. Needs T001,
  T007, T014 first. (R6)
  Verify: `python3 -m unittest test_screen_event_gate test_wake_event_gate test_gate test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T016 [US4] Files: `postmaster.py`, `test_wake_sweep_screen.py`. In the session sweep, after
  `wake_identities`, call `screener.run(sessions=[sid], once=True)` when `screening_on` is true (import inside).
  Write the test first. Screening on gives one screening pass for each round. Screening off gives none and 0 model
  calls. Needs T001, T012 first. (R7, FR-022)
  Verify: `python3 -m unittest test_wake_sweep_screen test_wake_sweep test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`

---

## Phase 7: User Story 7 - The user starts or resumes a session by its name (Priority: P2)

**Goal**: FR-023 to FR-026.
**Independent test**: `rookery up --no-launch` twice, the second time with `--session NAME`, gives one session.

- [x] T017 [US7] Files: `rookery_team.py`, `cli_workspace.py`, `test_up_session.py`. Add `--session NAME` to
  `rookery up`. It sets the session name before validation. Make `session` optional in the team file when a name
  comes from the flag or the default. With no name, call `rookery_names.make_name()`. Retry on a name that exists,
  at most 5 times, then fail with exit 2. Print the name. Add `session_name` and `name_made` to `--json`. Write the
  test first. No name gives a three-word name and `name_made: true`. `--session` wins over the file. A second run
  with the same name reuses the session and the identities. An ended session gets a refusal as today. The team file
  validation refuses a bad `--session` like a bad file name. (R10)
  Verify: `python3 -m unittest test_up_session test_team_file test_up_idempotent test_up_build test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_launch test_mcp_gate test_mailroom_gate`

---

## Phase 8: User Story 5 - `rookery up` sets it all up (Priority: P2)

**Goal**: FR-013, SC-009.
**Independent test**: `rookery up --no-launch` starts one sweep; a second run does not start another.

- [x] T018 [US5] Files: `cli_workspace.py`, `test_up_sweep.py`. After `_build_rows`, start the sweep with
  `rookery_wake.spawn(["sweep", "--session", SID, "--root", ROOT])`. `spawn` uses `program_argv`, so this works from a
  checkout and from the zipapp. Write the `screener-enabled` session binding when the team file has `screener: true`.
  Add `sweep` (`started` or `running`) to `--json`. It reads `running` when `try_lock` on the sweep lock fails.
  Write the test first (recorder for `spawn`, and a held lock for `running`). The first run spawns one sweep. A
  second run with the lock held reports `running` and spawns none. `screener: true` writes the binding.
  `--no-launch` still starts the sweep (the sweep is not an agent). Needs T012, T017 first. (R7)
  Verify: `python3 -m unittest test_up_sweep test_up_session test_up_launch test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_team_file test_mcp_gate test_mailroom_gate`

---

## Phase 9: User Story 6 - An agent on another machine checks the mailroom (Priority: P2)

**Goal**: FR-019, FR-020, FR-021, SC-011.
**Independent test**: with a fake mailroom on `127.0.0.1` and an injected clock, at most 5 checks and one notice.

- [x] T019 [US6] Files: `rookery_remote.py`, `rookery_cli.py`, `test_wake_remote.py`. Fill `rookery_remote.py`.
  `check_once(url, token, node, config, run, opener)` does `GET URL/inbox?node=NODE` with the token. It applies the
  push rules to the rows: a self-send and kind `status` do not push, and an `answer` pushes. It skips IDs in
  `~/.rookery/remote/NODE.json`. It types one notice with `postmaster.type_notice`. The notice goes through the pane
  binding that the `identity_binding_sources` config key resolves from the environment (the same key that local
  identity resolution reads). Then it rewrites `NODE.json` with the IDs that are still
  unread. A mailroom error returns `check-failed`. Add the subcommand `rookery check [--follow] [--json]` (the single
  check in this task). It exits 2 when `ROOKERY_URL` or `ROOKERY_NODE` is missing. Write the test first with a local
  `http.server` on `127.0.0.1` that serves `/inbox`. An ask gives one notice. The same ask on a second check gives
  none. A `status` gives none. A server that answers 500 gives `check-failed` and exit 0. Needs T003, T004, T005,
  T012 first. (R8)
  Verify: `python3 -m unittest test_wake_remote test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [x] T020 [US6] Files: `rookery_remote.py`, `test_wake_remote_follow.py`. `--follow`: write `NODE.follow`
  `{"start": now}`, then spawn the follower (`rookery check --follower`, hidden flag). Print `follower-started`, or
  `follower-running` when `NODE.lock` has a holder. The follower takes the lock and checks at once. Then it sleeps
  300 s (injected) and checks again. Before each check it reads `NODE.follow`. It restarts its count when the start
  changed. It stops after the first notice or after 5 checks since the last start. Write the test first with an
  injected clock and sleep. No mail gives exactly 5 checks. An ask on check 3 gives one notice and stops. A new
  trigger after check 4 gives 5 more checks from it. A second `--follow` while one runs spawns no second follower
  (SC-011). Needs T019 first. (R8)
  Verify: `python3 -m unittest test_wake_remote_follow test_wake_remote test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mcp_gate test_mailroom_gate`
- [ ] T021 [US6] Files: `cli_hook.py`, `rookery_mcp.py`, `test_wake_remote_triggers.py`. In `stop`, when
  `ROOKERY_URL` exists, spawn `rookery check --follow`. Never raise and never print. In the MCP `send` tool, spawn
  the same after a remote send that succeeded. Write the test first (recorder). The Stop hook with `ROOKERY_URL`
  spawns one `check --follow` and no local wake. Without it, the hook spawns no `check`. An MCP remote send spawns
  one. A failed send spawns none. Needs T011, T020 first. (R8)
  Verify: `python3 -m unittest test_wake_remote_triggers test_wake_stop test_mcp_gate test_turn_block test_send test_hooks test_outbox test_wake test_identity test_gate test_gate_log test_held_quarantine test_escalated test_screen_run test_screen_cli test_up_build test_up_idempotent test_up_launch test_team_file test_mailroom_gate`

---

## Phase 10: Polish and cross-cutting concerns

- [ ] T022 [P] (Fable, in the end phase; not a Sonnet wave) Files: `README.md`, `CHANGELOG.md`, `skills/rookery/SKILL.md`. Document these under `Unreleased`.
  The event wake, `rookery wake`, `rookery check`, the session sweep and its 300 s floor. `screener.enabled` (off by
  default, and why). `rookery up --session` and the three-word name. The outcomes of FR-014. The limit of the
  remote check (hooks or MCP only). The `ROOKERY_WAKE=off` switch. Write `test_docs_005.py` first. Each changed file passes ste-lint at 2.0. Skip
  with a reason when `ste-lint` is missing. Each file names `rookery wake`, `rookery check` and `screener.enabled`.
  Verify: `python3 -m unittest test_docs_005`
- [ ] T023 Files: `test_wake_full.py`. Run the whole suite. Run a compile test under Python 3.10:
  `/usr/bin/python3 -m py_compile` on every changed module. Build the zipapp. Run `python3 ARCHIVE wake --help`
  and `python3 ARCHIVE check --help` from it. The test file holds the zipapp test. Needs T001 to T022 first.
  Verify: `python3 -m unittest discover -p 'test_*.py' && /usr/bin/python3 -m py_compile rookery_wake.py rookery_remote.py postmaster.py mail_gate.py rookery.py screener.py cli_screen.py cli_hook.py rookery_mcp.py cli_workspace.py rookery_team.py rookery_cli.py screener_config.py`

---

## After the waves (manual, with the user)

These steps have no checkbox, so dev-kid does not schedule them.

- End phase with Fable, its only use in this spec (user rule 2026-10-27): (1) one adversarial audit of the whole branch;
  (2) the documentation: Fable carries out T022 and checks, edits and ste-lints every doc the feature touches. Wave work
  and wave audits use Sonnet or Opus. Fix every confirmed finding with a test.
- Live test per [quickstart.md](quickstart.md) in a scratch herdr workspace with a scratch database:
  `rookery up --no-launch`, fake panes, no agent, no model. Report SC-001, SC-002, SC-005 and SC-007 as measured.
- Risk register: add R19 (a wake event lost, or a wake typed twice) in `okf/risks/register.md`, mitigation "lock
  for each agent, recheck, session sweep", proof `test_wake_lock` and `test_wake_sweep`, status `proven`.
- PR on GitHub, merge, mirror to Gitea (fast-forward PR), then deploy only when the user asks.

---

## Requirements by task

| Requirement | Tasks |
|---|---|
| FR-001 gate wake | T006, T007 |
| FR-002 detached, never fails the send | T002, T006, T007, T010 |
| FR-003 Stop hook | T011 |
| FR-004 one wake code path, back-off in a file | T004, T005 |
| FR-005 one wake per agent, recheck | T002, T005, T010 |
| FR-006 no wake for empty, self, legacy, closed | T006 |
| FR-007 sweep, 300 s, raise below 300 | T002, T012 |
| FR-008 sweep catches, no repeat | T012 |
| FR-009 screening event when on | T001, T015 |
| FR-010 one screening pass, recheck | T014 |
| FR-011 no 2 s screener poll | T014, T016 |
| FR-012 every change to `pending` | T007, T008, T009 |
| FR-013 `up` starts one sweep | T003 (bundle), T012 (`rookery sweep`), T018 |
| FR-014 one log line per outcome | T002, T005, T006, T012 |
| FR-015 core names no pane tool | T004, T019 (fake panes through config) |
| FR-016 no model call, cost with mail | T013 |
| FR-017 old `--poll 0.5` works | T012 |
| FR-018 no herdr, tmux, model or live DB in tests | all (rules) |
| FR-019 to FR-021 remote check and bound | T019, T020, T021 |
| FR-022 sweep ends with the session, covers screening | T012, T016 |
| FR-023 to FR-025 `up --session`, name, resume | T017 |
| FR-026 no harness resume in core | T017 (no harness code), T022; existing proof of the harness prompt flag: `test_up_launch.py:169`, `:173` |
| SC-001, SC-005, SC-006 | T010 |
| SC-002 | T011 (end-to-end case) |
| SC-003, SC-004 | T013 |
| SC-007 | T012 |
| SC-008 | T008, T009 |
| SC-009 | T018 |
| SC-010 | T023 |
| SC-011 | T020 |

## Dependencies and execution order

- Phase 1 (T001, T002, T003) runs in parallel.
- T004 needs nothing from Phase 1 but blocks every story.
- US1: T005 → T006 → (T007, T008, T009 in parallel) → T010.
- US2: T011 after T005.
- US3: T012 after T002, T004 and T005; T013 after T006 and T012.
- US4: T014 after T002; T015 after T001, T007 and T014; T016 after T001 and T012.
- US7: T017 has no story dependency. US5: T018 after T012 and T017.
- US6: T019 after T003, T004, T005 and T012; T020 after T019; T021 after T011 and T020.
- Polish: T022 at any time after the code exists; T023 last.

## Parallel opportunities

- T001, T002, T003, T004, T017 touch different files.
- T007, T008, T009 touch `mail_gate.py`, `rookery.py` and `screener.py`.
- T011, T012, T014 touch `cli_hook.py`, `postmaster.py` and `cli_screen.py`.

## Implementation strategy

1. Phases 1 and 2, then US1 (MVP): the gate wakes an idle agent.
2. US2 and US3: the turn end and the sweep. With these, the local wake is complete.
3. US4, US7 and US5: the screener event and `rookery up`.
4. US6: the remote check.
5. Polish, then the manual steps.
