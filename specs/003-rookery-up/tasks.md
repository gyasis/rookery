---
type: Tasks
task: 003-rookery-up
updated: 2026-10-24
revision: 1
---

# Tasks: `rookery up` builds the Rookery project workspace

**Input**: the design documents in `specs/003-rookery-up/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/up.md](contracts/up.md) and [quickstart.md](quickstart.md).

**Tests**: MANDATORY (constitution II). Each FR and each automatic SC gets the test that proves it, in this
PR (see the Requirement Trace in plan.md). Use fixtures, not mocks. Each test uses a temporary `HOME`,
`ROOKERY_DB` and `ROOKERY_ROOT`, and a scratch git repo. Each task writes its test first, sees it fail,
then writes the code. No task leaves a failing test.

**Layout**: a flat Python repo. All paths start at the repo root. Use the standard library only.

## Format: `- [ ] T### [P?] [Story?] Description`

- **[P]**: the task can run in parallel. It uses different files and has no open dependency in its phase.
- **[Story]**: the user story of the task (US1 to US7).
- **Needs T0xx first.**: an explicit dependency on another task.
- **Verify:** the exact command that proves the task. It runs the tests of the task and the suites that the
  task could break.

## Rules for every task

- Read the research decision that the task names (Dn in [research.md](research.md)) before you write code.
- A task that writes a test also writes the code that makes it pass. No `skip` or `expectedFailure` hides
  missing code. Skip a test only when an outside tool such as `git` or `ste-lint` is missing.
- Subprocess tests run `[sys.executable, "rookery_cli.py", ...]` with `HOME`, `ROOKERY_DB` and `ROOKERY_ROOT`
  set to a temporary folder, as `test_identity.CliSessionIdentity` does. `load_config` reads
  `~/.rookery/config.json`, so a test that needs a custom `launch` writes that file in its temporary `HOME`.
- No new module names `herdr` or `tmux` in code (constitution IX). Pane tools come only from the `launch` and
  `alive` configuration.
- No new code writes `~/.claude.json`, `~/.claude/settings.json` or any file in the host repo.
- No new code removes a branch. Only `rookery finish` removes a worktree, after three confirmations.
- Each text that an agent or the user reads passes `ste-lint --max-score 2.0 --glossary ste-glossary.txt`
  (FR-033): error texts, the first-start note, the generated Markdown files.
- The existing suites are
  `test_turn_block test_send test_hooks test_outbox test_wake test_identity test_init test_gate test_gate_log`.
  Each `Verify:` of a code task runs them.
- Every new module goes into `bootstrap._PY_MODULES` in the same task that creates it.

---

## Phase 1: Setup

**Purpose**: the fixtures that later phases need.

- [ ] T001 [P] Edit `fixtures/bin/tmux` and `fixtures/bin/herdr`. Keep the current behavior: append the
  arguments as one JSON line to `$ROOKERY_FIXTURE_LOG` (herdr: its current record variable) and exit 0. Add:
  `tmux new-window ...` prints `%<n>` on stdout, where `<n>` counts the lines already in the log.
  `herdr tab create ...` prints `{"pane_id": "h<n>"}`. `tmux display-message -p -t X ...` and `herdr pane get X` exit 0
  only when `X` is a word in `$ROOKERY_FIXTURE_ALIVE`, else exit 1. `test_wake` must still pass with no
  change to its file.
  Verify: `python3 -m unittest test_wake -v && L=$(mktemp) && ROOKERY_FIXTURE_LOG=$L fixtures/bin/tmux new-window -d -P`
- [ ] T002 [P] Create `fixtures/bin/fakepane` and `fixtures/bin/fakeagent` (executable, Python).
  `fakepane --cwd DIR [--env K=V]... -- CMD...` prints `pane:fp<n>`, then runs `CMD` in `DIR` with the given variables
  added to its environment, waits for it, and exits 0. `fakeagent` appends one JSON line
  `{"argv", "cwd", "env"}` (only the `ROOKERY_*` variables) to `$ROOKERY_FIXTURE_LOG`. If you set `$FAKEAGENT_SEND_TO`, it then
  runs `rookery_cli.py send $FAKEAGENT_SEND_TO hello` with its environment. Each script has a first-line
  description (R-SL0a).
  Verify: `L=$(mktemp) && ROOKERY_FIXTURE_LOG=$L fixtures/bin/fakepane --cwd /tmp --env ROOKERY_X=1 -- fixtures/bin/fakeagent a && cat $L`

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: the schema, the team file reader, D2 and the proof of D3.

- [ ] T003 [P] Edit `rookery.py`. Add the four tables of [data-model.md](data-model.md) (`up_agents`,
  `worktrees`, `claims`, `ports`) to `_SESSION_TABLES_SQL` with `CREATE TABLE IF NOT EXISTS`. Edit
  `turn_block._schema_current` (`turn_block.py:120`) so that its table set has the four names (D6). Write
  `test_up_schema.py` first. Cases: a new database has the four tables with the columns of data-model. An old
  database (made with the spec 001 schema and one inbox row) gets the four tables on `open_db`. The
  counts of `inbox`, `rookery_sessions`, `identities` and `bindings` do not change. `ports.port` is unique.
  Verify: `python3 -m unittest test_up_schema test_identity test_turn_block test_hooks test_send -v`
- [ ] T004 [P] Create `rookery_team.py` with `load(path) -> dict` and `TeamError(errors: list)`. It reads the
  whole file and applies every rule of the data-model "Team file" table (D4, D17) before it returns. A writer's
  name, the session and the `task_id` must pass `git check-ref-format --branch agent/<session>/<name>/<task_id>`
  (FR-021). `codex` gives "Codex waits for R19" (FR-019). Each error is `{"index", "name", "problem"}`, and
  each `problem` text passes ste-lint. Add `rookery_team.py` to `bootstrap._PY_MODULES`. Write
  `test_team_file.py` first. Cases: a valid file of the contract example loads. Each of these gives one
  error that names the entry. No `session`. No `agents`. An empty `agents` list. A duplicate name. A bad
  `safe_name`. Harness `codex`. Harness `x`. Role `x`. A writer with no `task_id`. A writer named `a..b`.
  A writer named `x.lock`. An unknown `binding` key (with a config that has `tmux-pane` only). A file with
  three bad entries gives three errors. Bad JSON gives one error with the line number. A reader named
  `x.lock` gives no error (only writers need a branch part).
  Verify: `python3 -m unittest test_team_file test_text_hints -v`
- [ ] T005 Edit `turn_block.log_event` (`turn_block.py:~98`). Needs T003 first (same file). The stream
  `gate` always writes to `~/.rookery/logs/gate.jsonl`, whatever `root` or `ROOKERY_ROOT` holds. The other
  streams keep their roots. Do not change `gate_root()` (D2). Write `test_d2_gate_home.py` first. Cases: with
  `ROOKERY_ROOT=<tmp>/p1`, then `<tmp>/p2`, one `submit` each: both records are in
  `<HOME>/.rookery/logs/gate.jsonl`, and neither root has `logs/gate.jsonl` (SC-006). A `turn-block` record
  still lands in the project root. Edit `test_gate_log.py:6,53` to the new path.
  Verify: `python3 -m unittest test_d2_gate_home test_gate_log test_gate test_turn_block test_hooks -v`
- [ ] T006 [P] Write `test_d3_root.py` (D3: proof, no code change). For each of the five sites, set
  `ROOKERY_ROOT` to a temporary project root and make the current folder `<root>/agents/s1/scout`. Then assert
  that the site uses the root. `rookery session new --from` (`cli_rookery_session.py:111`) copies into the root.
  `rookery identity bind` (`:244`) logs under the root. `rookery peek` (`:274`) reads with the root.
  `rookery send` (`cli_hook.py:623`) dispatches with the root. `postmaster.main` with no `--root`
  (`postmaster.py:675`) resolves the root. Then, with `ROOKERY_ROOT` unset and the current folder named
  `proj`, each site uses `~/.rookery/projects/proj` (FR-003, story 3 scenario 4). If a case fails, the bug is
  real. Fix the site in the same task, the smallest way, and name it in the commit.
  Verify: `python3 -m unittest test_d3_root test_identity test_hooks test_send -v`

**Checkpoint**: the schema, the team reader, D2 and D3 work. All suites are green.

---

## Phase 3: User Story 3 - Existing commands keep working after the rename (P1)

**Goal**: the old `rookery up` is `rookery join` (D1).

**Independent Test**: `rookery join` takes the old flags. The old peer tests pass against `join`.

- [ ] T007 [P] [US3] Rename `cli_up.py` to `cli_join.py` with `git mv`. In `rookery_cli.py:75-89`, the
  subparser `up` becomes `join`, with the same flags and `func=cmd_up` from `cli_join`. Change the texts
  at `cli_join.py:53,185` and the docstring to `rookery join`. Edit `bootstrap.py:26` (module name) and
  `:259` (installer text), and `test_text_hints.py:6` (`TEXT_FILES`). Write `test_join_rename.py` first.
  Cases: `rookery join --help` lists `--node-id` and `--insecure-tls`. `rookery join` with a URL that refuses
  the connection exits with the same code and message shape as the old `up` did. Read `cli_up.py` before the
  rename for the expected text. `rookery --help` lists `join`. The zipapp module list has `cli_join.py` and
  not `cli_up.py`. Run `test_demo_starts` after the change and fix `demo_up.sh` lines 2, 72, 97 to `rookery join`.
  Verify: `python3 -m unittest test_join_rename test_text_hints test_demo_starts test_identity test_cli_init_unit -v`
- [ ] T008 [US3] Edit the docs that name the old command. In `README.md`, edit lines 67, 191, 421-427, 464, 472 and
  the file table row for `cli_up.py`. In `docs/QUICKSTART.md`, edit lines 17, 42, 56, 251-262, 299, 305, 354.
  In `docs/rookery-explained.html`, edit lines 416-420, 451, 555. Each peer-join use becomes `rookery join`. Needs T007
  first. Add a case to `test_join_rename.py` first: no file in `README.md`, `docs/` or `demo_*.sh` matches
  the pattern `rookery up (https?://|<url>|\[url\])`. Do not touch `specs/` (history).
  Verify: `python3 -m unittest test_join_rename -v && ste-lint docs/QUICKSTART.md --max-score 2.0 --glossary ste-glossary.txt | tail -1`

**Checkpoint**: the name `up` is free. Peer join works under `join`.

---

## Phase 4: User Story 1 - One command builds a team of reader agents (P1) 🎯 MVP

**Goal**: `rookery up TEAM_FILE --no-launch` builds the root, the session, the identities and the
workspaces. The host repo does not change.

**Independent Test**: a two-reader team in a scratch repo gives the root, two workspaces and two identities
in one session. `git status` in the host repo is clean.

- [ ] T009 [US1] Create `rookery_views.py` with `workspace_files(root, session, agent, team, host)`. Needs
  T004 first. It writes the generated items of the D7 table for one agent: `CLAUDE.md`, `AGENTS.md`,
  `CONTEXT.md`, `_config/persona.md`, `_config/scope-card.md` and `_config/system-prompt.md`.
  `_config/scope-card.md` has the "does" and "does NOT do" lists, plus the cards of the other agents in the
  same team file only (RQ-R.3). Each file opens with the "generated" line of the contract, and
  `cli_init.write_text_atomic` writes it. It
  makes `runs/`, `outbox/` and `state/` if they are missing and never writes inside them. It also writes
  `rookery_md(root)` (L0 routing; D15) and writes no `CLAUDE.md` or `AGENTS.md` in the root, `agents/` or
  `agents/<session>/` (FR-007). No generated file names an MCP tool (FR-030). Leave the host import and
  `HOST_RULES.md` to T018. Add the module to `bootstrap._PY_MODULES`. Write `test_views.py` first with these
  cases. Each D7 item exists. A second call does not change a file that an agent wrote in `runs/`, `outbox/`
  or `HANDOFF.md` (FR-010). `scope-card.md` has a "does NOT do" section and only the same team's cards
  (FR-013). No generated file contains `check_inbox`, `await_message` or `mcp__`. No `CLAUDE.md` above the
  workspace.
  Verify: `python3 -m unittest test_views test_text_hints -v`
- [ ] T010 [US1] Add `settings_json(path, project, repo=None)` to `rookery_views.py`. Needs T009 first. It calls
  `cli_init.merge_hooks(path, project, with_permissions=True)` (D8). When `repo` has a committed
  `.claude/settings.json` with Rookery handlers (`cli_init._is_rookery`), it copies their exact command text for
  those events instead (D8, H1). Nothing more for now (T019 adds the
  guard and watch handlers). Add cases to `test_views.py` first. The `SessionStart`, `UserPromptSubmit` and
  `Stop` handler commands are byte for byte the same as those that `cli_init.merge_hooks` writes into a
  fresh `.claude/settings.json` for the same project (FR-031). `permissions.allow` has the `rookery inbox`
  rule. The file is valid JSON. A `repo` whose committed file has `/opt/bin/rookery hook stop --project app`
  gives exactly that `Stop` command text, and only one `Stop` handler.
  Verify: `python3 -m unittest test_views test_cli_init_unit test_init -v`
- [ ] T011 [US1] Create `cli_workspace.py` with `add_parser(sub)` and `cmd_up(args)`, and register it in
  `rookery_cli.build_parser`. Needs T003, T004, T007 and T010 first.
  `rookery up TEAM_FILE [--no-launch] [--json]` follows D22 steps 1, 2, 3, 5 and 7 (launch comes in T013,
  worktrees in T015). Resolve the host
  repo, project and root as D5 says (use `git rev-parse --git-common-dir` for a linked worktree). Refuse a
  relative `ROOKERY_ROOT` and a root inside the host repo (copy the root rules of `cli_init.py:544-548`). Hold
  `cli_init.init_lock(root)`. In one transaction: `new_session` or reuse by name (an ended session gives exit 2
  with a hint), `new_identity` or reuse, one `up_agents` row each. Then write each workspace
  (`workspace_files`, `settings_json` into `_config/settings.json`) and `ROOKERY.md`. A URL argument
  (`http://`, `https://`) exits 2 with the contract text (D1). A bad team file exits 2, prints each error and
  changes nothing (FR-009). Output follows the contract (text and `--json`). Add the module to
  `bootstrap._PY_MODULES`. Write `test_up_build.py` first. Cases: a two-reader team with `--no-launch`
  gives the root, `ROOKERY.md`, two workspaces, one session, two identities, two `up_agents` rows (SC-001
  for readers). The root is outside the repo. `git status --porcelain` in the host repo is the same before
  and after. `pytest --collect-only -q` finds the same number of tests in a host fixture with one test
  file (R10, SC-002). A relative `ROOKERY_ROOT` exits 2. A root inside the repo exits 2. A bad team file
  makes no root and no session. `up http://x` exits 2 and names `rookery join`. Running from a linked
  worktree of the host repo uses the host project name. `~/.claude.json` and `~/.claude/settings.json` in the
  temporary `HOME` do not exist after the run (FR-030, FR-031).
  Verify: `python3 -m unittest test_up_build test_views test_team_file test_identity test_init test_hooks -v`
- [ ] T012 [US1] Write `test_up_idempotent.py` and fix `cli_workspace.cmd_up` where it fails. Needs T011
  first. Cases: a second run with the same team file makes 0 new sessions, 0 new identities and 0 new
  `up_agents` rows. It says `reused` for each agent (FR-006, SC-003). Files that an agent wrote in `runs/`,
  `outbox/` and `HANDOFF.md` are byte for byte the same (FR-010). A generated file that the test edited by hand
  is back to its generated text. A team file with a new persona text changes `_config/persona.md`. A new
  session name with the same agents gives the same `_config/` files apart from the session name in the text
  (FR-032). An agent added to the team file gets its identity and workspace, and the others say `reused`.
  Verify: `python3 -m unittest test_up_idempotent test_up_build test_views -v`

**Checkpoint**: MVP. `rookery up --no-launch` builds a reader team. Commit and run the wave audit.

---

## Phase 5: User Story 2 - Each agent runs in its own pane and can send and get mail (P1)

**Goal**: `rookery up` runs each agent with the configured launch command, binds its pane, and the agent can
send mail through the gate.

**Independent Test**: with fake panes, the recorded launch has the right folder, environment and flags. A
fake agent sends one mail that its teammate gets.

- [ ] T013 [US2] Add the launch to `cli_workspace.py` (D9, D10, D11) and the new keys `default_binding`,
  `launch`, `alive`, `harness`, `ports` to `rookery_config.default.json` as in the contract. Needs T001 and
  T012 first. For each agent: run the `alive` probe on each pane binding of the identity and skip a live
  agent (`running`). Else expand the steps: `{cwd}` (reader: workspace. Writer: worktree, from T015, until
  then the workspace). `{label}` (`<session>/<name>`). `{env}` (the FR-008 variables with absolute paths,
  `ROOKERY_NODE` = agent ID, `ROOKERY_PORTS` = `{}` until T020, `ROOKERY_DB` if set). `{cmd}` (the harness
  command with its D11 flags). `{pane}` (from `capture`). After the last step, `rookery_identity.bind` the
  identity to `<kind>:<pane>`. A missing `launch` kind, a step that exits non-zero, or a `capture` with no
  match gives `not started: <reason>` for that agent, and the other agents still launch (FR-018). Print the
  first-start note for each new Claude Code agent. `--no-launch` skips all of this. Write `test_up_launch.py`
  first, with `fixtures/bin` first on `PATH` and `ROOKERY_FIXTURE_LOG` set. Cases: the default
  `tmux-pane` config gives the fake tmux the argv of the contract, with `-c <workspace>` and one `-e K=V` for
  each FR-008 variable, every path absolute (FR-008, FR-014). Rookery stores the binding `tmux-pane:%0` (FR-017).
  The herdr config gives `tab create` then `pane run h0 ...`. The reader argv has `--settings` and
  `--add-dir <host>`, and the writer argv has `--settings` and `--append-system-prompt-file` (FR-016). A `pi` agent gets
  `--append-system-prompt`. Rookery skips a pane in `$ROOKERY_FIXTURE_ALIVE` (FR-018). The report names a kind with no
  `launch` entry, and the workspace still exists. One failing step does not stop the other
  agent. With `state/turn.json` set to `{"state": "idle"}`, `postmaster.wake_identities` with the stored binding sends the wake to the fake tmux (FR-017). With no `state/turn.json`, it sends no wake (D10). A scan
  of the new modules finds no `herdr` or `tmux` string literal (constitution IX).
  Verify: `python3 -m unittest test_up_launch test_up_build test_up_idempotent test_wake test_hooks -v`
- [ ] T014 [US2] Write `test_up_e2e.py`. Needs T002 and T013 first. In the test's `~/.rookery/config.json`,
  set a `fake-pane` launch kind that runs `fixtures/bin/fakepane --cwd {cwd} {env} -- {cmd}` with `capture`
  `^pane:(\S+)$`, and set `harness.claude.cmd` to `fixtures/bin/fakeagent`. Team: readers `a` and `b`. Set
  `FAKEAGENT_SEND_TO=b` for the run. Add a second mode to `fixtures/bin/fakeagent`: with `FAKEAGENT_BLOCK_TO` set, it writes a transcript whose last assistant message ends with a `rookery` block to that name, and runs `rookery_cli.py hook stop --project <project>` with the hook input built as `test_hooks` builds it. Cases: the inbox has one row from `a`'s agent ID to `b`'s agent ID in the
  session (SC-005). A run with `FAKEAGENT_BLOCK_TO=b` instead adds one row from `a` to `b` through the Stop hook path (US2 scenario 3). `rookery identity show` run with `a`'s recorded environment shows `a`, the session and the
  project root (SC-004). The gate log under `~/.rookery/logs/` has the decision (D2). `~/.claude.json` does
  not exist (FR-030). If a case fails, fix `cli_workspace.py` in this task.
  Verify: `python3 -m unittest test_up_e2e test_up_launch test_gate test_send -v`

**Checkpoint**: readers run in panes and mail flows. Commit and run the wave audit.

---

## Phase 6: User Story 4 - Code-writing agents get their own worktree (P2)

**Goal**: writers get a worktree. `finish` and `gc` manage the worktrees that Rookery registered.

**Independent Test**: one writer gets its worktree and branch. The host repo does not change. `finish` and
`gc` act only on registered worktrees.

- [ ] T015 [US4] Create `rookery_worktree.py` with `add(conn, root, session, agent, repo, task_id) -> path`
  and wire it into `cli_workspace.cmd_up` for writers (D12, D22 step 4). Needs T013 first. Before the
  transaction, refuse the session if `<root>/wt/<session>/` exists and has no registry row (FR-021a). Insert
  the `worktrees` row, then run `git -C <repo> worktree add` (existing branch: no `-b`, no reset; new branch:
  `-b agent/<session>/<name>/<task_id>` from `HEAD`). Pass the writer repo to `settings_json` (T010) so that the
  hook text matches the repo's committed handlers. Insert the row as `pending`. Set it to `active` when git succeeds. If git fails, set it to `failed` (never
  delete it) and report the agent as `not started: <git error>`. A second run reuses the row and the path, and
  sets a `failed` row back to `pending` before it runs git again. The writer's launch `{cwd}` is the
  worktree. Add the module to `bootstrap._PY_MODULES`. Write `test_worktree.py` first. Cases: the path is
  `<root>/wt/<session>/<name>/<repo>/` on branch `agent/<session>/<name>/T1` (FR-020). An existing branch
  keeps its commit. A human folder at `<root>/wt/s1/` refuses session `s1` and changes nothing. The host
  repo's `git status --porcelain` and test count are the same (R10). A git error leaves the row as `failed` and no worktree. A second run after the fix makes the worktree and sets the row `active`. The row count never goes down. A
  scan of the new modules finds no `GIT_DIR`, `GIT_WORK_TREE`, `LD_PRELOAD`, `overlay` or `node_modules`
  (FR-024).
  Verify: `python3 -m unittest test_worktree test_up_build test_up_launch test_up_idempotent -v`
- [ ] T016 [US4] Add `finish` and `gc` to `rookery_worktree.py` and the subcommands
  `rookery finish NAME [--session S] --message TEXT [--json]` and `rookery gc [--json]` to `cli_workspace.py` (D13, D14). Needs
  T015 first. `finish` shows `git status --short`, `git diff --stat` and the changed files that another agent
  claims. It asks `Type yes to continue (n of 3)` three times on stdin, or accepts `BROTUNDWASSER=1`. Then it
  commits, pushes if the repo has a remote (else it says so), runs `git worktree remove`, and sets the row to
  `finished`. A git error stops at that step and keeps the row `active`. `gc` lists rows with reason
  `session-ended`, `finished-path-exists`, `path-missing`, `failed` or `pending-stale` (data-model), and writes nothing. Write `test_finish_gc.py`
  first. Cases: three `yes` answers commit the change on the branch, remove the worktree, keep the branch, and
  set `finished` (FR-022). The answer `no` at any of the three prompts exits 2 and changes nothing. A fixture
  with no remote finishes and says so. `gc` after `rookery session end s1` lists the worktree with
  `session-ended`. `gc` lists a `finished` row whose path still exists, an `active` row whose path is missing, a `failed` row, and a `pending` row older than one hour. The
  branch count and the file count under `wt/` are the same before and after `gc` (FR-023, R14, SC-008). `gc`
  never lists a human folder under `wt/`.
  Verify: `python3 -m unittest test_finish_gc test_worktree test_identity -v`

**Checkpoint**: writers work end to end. Commit and run the wave audit.

---

## Phase 7: User Story 5 - Agents can find each other and the team (P2)

**Goal**: the generated views and `rookery regen`.

**Independent Test**: after `rookery up`, `ROOKERY.md` and `_directory/INDEX.md` exist. A roster change and
`rookery regen` update INDEX.

- [ ] T017 [US5] Add to `rookery_views.py` the generators `directory(conn, root)` (`_directory/INDEX.md`,
  `ports.md`, `claims.md`), `teams(conn, root, session, team)` (`_teams/<session>.md`), `threads(conn, root)`
  (`_threads/INDEX.md`, metadata only) and `mail_mirror(conn, root)` (`_mail/quarantine/<reason>/<id>.md`,
  `_mail/review/<id>.md`, metadata only) (D15). Each uses `SELECT` only. Call them at the end of `cmd_up`, and
  add `rookery regen [--json]` to `cli_workspace.py`. Needs T012 first. `_teams/<session>.md` needs the team
  file's task and criteria, so `cmd_up` writes it, and `regen` keeps the existing file. Add cases to
  `test_views.py` first. INDEX lists name, harness, pane, status, current task, role and team for each
  identity (FR-025). A new identity added through the CLI shows after `rookery regen`. `_teams/<session>.md` has
  the task, the criteria and the members (RQ-R.4). A mail with the body `SECRET-BODY-7` appears in
  `_threads/INDEX.md` by sender and topic, and the text `SECRET-BODY-7` appears in no file under `_threads/`
  or `_mail/` (FR-026). A quarantined row and a held row appear under `_mail/` (FR-027). The unread count and
  every `read_at` value are the same before and after `regen` (FR-028).
  Verify: `python3 -m unittest test_views test_up_build test_up_idempotent -v`

---

## Phase 8: User Story 6 - Reader agents keep the host project rules (P2)

**Goal**: a reader's `CLAUDE.md` imports the host rules, and `AGENTS.md` reaches them through a symlink.

**Independent Test**: a host-only rule is reachable from the reader's generated files.

- [ ] T018 [US6] Edit `rookery_views.workspace_files` (D7, FR-012). Needs T017 first (same file). For a
  reader, `CLAUDE.md` gets the line `@<absolute host path>/CLAUDE.md` when that file exists. `HOST_RULES.md`
  is a symlink to the host `AGENTS.md`, else to the host `CLAUDE.md`. `AGENTS.md` tells the agent to read
  `HOST_RULES.md`. With no host rules file, the reader gets no import and no symlink, and `cmd_up` prints one
  note. A writer gets no import (its worktree has the repo's own `CLAUDE.md`). Add cases to `test_views.py`
  first: the import line names the absolute path (R11). The symlink target is the host file. A host with only
  `AGENTS.md` links to it. A host with neither gives no import, no symlink and the note. A writer's
  `CLAUDE.md` has no `@` import.
  Verify: `python3 -m unittest test_views test_up_build -v`

---

## Phase 9: User Story 7 - Guardrails stop accidents (P3)

**Goal**: guard, watch, claims and ports, as accident guards under 002 D24.

**Independent Test**: the guard denies a Write outside. The watch flags a Bash write outside. A write to a claimed file
warns. Two agents get different free ports.

- [ ] T019 [US7] Create `rookery_guard.py` with `guard(hook_input, env, conn) -> dict | None` and
  `watch(hook_input, env, conn) -> dict | None` (D16). Register `rookery hook guard` and `rookery hook watch` in
  `cli_hook.py` the same way as the other hook subcommands, so that an error never exits non-zero. Add the
  `PreToolUse` (`Write|Edit|NotebookEdit`) and `PostToolUse` (`Bash`) handlers to
  `rookery_views.settings_json`. Store each agent's `claims` from the team file in `cmd_up`: add new patterns,
  set `active = 1` on the patterns that stay and `active = 0` on dropped ones. Never delete a claims row. Needs T016 and T018 first. Add the module to `bootstrap._PY_MODULES`.
  Write `test_guard.py` first. Cases: a Write inside the workspace gives no output. A Write to `/tmp/x` gives
  the contract's deny JSON (R9). An Edit inside the active worktree gives no output. A Write to a file that
  matches another agent's claim gives an allow with the claims reason, and one `turn-block` log line (R12).
  `finish` lists that claimed file (R12). No identity in the environment gives no output and exit 0. Bad
  JSON on stdin gives exit 0 and one log line. `watch` on `echo x > /tmp/foreign` gives the warning and a
  `watch-flag` log line (R9). `watch` on `echo x > out.txt` inside the worktree gives nothing. The guard runs
  in under 1 s with 10,000 inbox rows (as `test_hooks.HookTiming`). `settings_json` now has the two new
  handlers, and the first three handlers are still equal to the `rookery init` output. A second `up` with a
  claim dropped from the team file sets it inactive, the guard ignores it, and the claims row count stays the same.
  Verify: `python3 -m unittest test_guard test_views test_finish_gc test_hooks test_turn_block -v`
- [ ] T020 [US7] Add `assign_ports(conn, agent_id, config) -> dict` to `rookery_guard.py` and use it in
  `cmd_up`, so that `ROOKERY_PORTS` holds the JSON map (D16, FR-008). Needs T019 first. A port is free when
  no `ports` row has it and a bind on `127.0.0.1` succeeds. An agent that has ports keeps them on a second
  run. Write `test_ports.py` first. Cases: two agents get different ports for each name in `ports.names`,
  inside `ports.range`. The assignment skips a port that the test holds open with a socket. A second run gives the same
  map. Two child processes, each started with its agent's `ROOKERY_PORTS`, both bind their `api` port with no
  `EADDRINUSE` (R13). The launch environment of `test_up_launch` now has a non-empty `ROOKERY_PORTS`.
  Verify: `python3 -m unittest test_ports test_up_launch test_up_build -v`

**Checkpoint**: all stories work. Commit and run the wave audit.

---

## Phase 10: Polish and cross-cutting concerns

- [ ] T021 [P] Edit `specs/002-mail-gate/research.md` (D24) and `specs/002-mail-gate/spec.md`: the pointer to
  RQ-2.9 now says that RQ-2.9 is a guardrail against accidents, not security (D18, FR-029).
  Verify: `grep -n 'RQ-2.9' specs/002-mail-gate/research.md specs/002-mail-gate/spec.md && for f in specs/002-mail-gate/research.md specs/002-mail-gate/spec.md; do ste-lint $f --max-score 2.0 --glossary ste-glossary.txt | tail -1; done`
- [ ] T022 [P] Edit `README.md`, `docs/QUICKSTART.md`, `CHANGELOG.md` and `skills/rookery/SKILL.md`. Document
  `rookery up`, `finish`, `gc` and `regen`, the team file, the first-start dialogs (D10) and CLI-only mail
  for these agents. The README layout tree shows `postmaster/outbox/`, `postmaster/pushed/`, `logs/` and
  `agents/<session>/<name>/state/` (FR-034). CHANGELOG gets "### Added" and "### Changed" (the `join`
  rename, the gate log home). Add a case to `test_text_hints.py` first: SKILL.md names `rookery up` and
  `rookery join`, and the README tree names `postmaster/` and `state/`.
  Verify: `python3 -m unittest test_text_hints -v && for f in README.md docs/QUICKSTART.md CHANGELOG.md skills/rookery/SKILL.md; do ste-lint $f --max-score 2.0 --glossary ste-glossary.txt | tail -1; done`
- [ ] T023 Add a lint case to `test_views.py`: produce every generated Markdown file from the contract's
  example team file and run `ste-lint --max-score 2.0 --glossary ste-glossary.txt` on each. Collect every
  error and note text of `rookery_team.py`, `cli_workspace.py`, `rookery_worktree.py` and `rookery_guard.py`
  into one temporary file and lint it too (FR-033). Fix each text that fails. Needs T020 first. Skip the case
  only when `ste-lint` is not on `PATH`.
  Verify: `python3 -m unittest test_views -v`
- [ ] T024 Run the full suite, the coverage gate and quickstart step 1 (the schema change on a COPY of the
  live database). Needs T021, T022 and T023 first. Fix any test that fails in this task.
  Verify: `python3 -m unittest discover -p 'test_*.py' && spec-pane cover . 2 ~/Documents/code/inter-agent-comms/okf/requirements/rookery-0-7.md`

---

## After the waves (manual, with the user)

These steps have no checkbox, so dev-kid does not schedule them.

- The final audit: one adversarial audit of the whole branch.
- Quickstart 5 to 8 are the live checks. They cover herdr launch and capture, a reader that quotes a host
  rule (SC-009), two agents that send mail, and the guard. Record each output. Each one stays UNTESTED until it runs.
- Update the OKF bundle: move R9 to R14 from `open` to `proven` in `risks/register.md`, with the test names
  (SC-010). Apply the five design changes of research D20. This is outside the repo, so it needs the user's
  go-ahead.
- Push, open the PR, check that it can merge, merge, then deploy (quickstart 9).

---

## Phase requirements by task

| RQ | Tasks | RQ | Tasks |
|---|---|---|---|
| RQ-2.1 | T011 | RQ-2.10 | T019 |
| RQ-2.2 | T009 | RQ-2.11 | T020 |
| RQ-2.3 | T017 | RQ-2.12 | T016 |
| RQ-2.4 | T009, T012 | RQ-2.13 | T016 |
| RQ-2.5 | T018 | RQ-2.14 | T013 |
| RQ-2.6 | T015 | RQ-2.15 | T015 |
| RQ-2.7 | T013 | RQ-2.16 | T017 |
| RQ-2.8 | T013, T020 | RQ-2.17 | T023 |
| RQ-2.9 | T019, T021 | RQ-R.3 | T009 |
| | | RQ-R.4 | T004, T017 |

## Dependencies and execution order

- Phase 1 (T001, T002) has no dependency.
- Phase 2: T003, T004, T006 in parallel. T005 needs T003 (same file `turn_block.py`).
- Phase 3 (US3): T007 can run with Phase 2. T008 needs T007.
- Phase 4 (US1): T009 needs T004. T010 needs T009. T011 needs T003, T004, T007, T010. T012 needs T011.
- Phase 5 (US2): T013 needs T001, T012. T014 needs T002, T013.
- Phase 6 (US4): T015 needs T013. T016 needs T015.
- Phase 7 (US5): T017 needs T012. It shares `rookery_views.py` with T018 and T019.
- Phase 8 (US6): T018 needs T017.
- Phase 9 (US7): T019 needs T016, T018. T020 needs T019.
- Phase 10: T021, T022 in parallel at any time after T016. T023 needs T020. T024 needs T021, T022, T023.

User story order: US3 and US1 first (P1), then US2 (P1), then US4, US5, US6 (P2), then US7 (P3). US4 and
US5 can run in parallel after T013 and T012, because they touch different files.

## Parallel opportunities

```text
Wave A: T001, T002, T003, T004, T006, T007     (six files, no shared file)
Wave B: T005, T008, T009                       (turn_block.py; docs; rookery_views.py)
Wave C: T010 → T011 → T012                     (one chain)
Wave D: T013, then T014 and T017 in parallel   (cli_workspace.py; test_up_e2e.py; rookery_views.py)
Wave E: T015 → T016, with T018 beside them     (rookery_worktree.py; rookery_views.py)
Wave F: T019 → T020; T021 and T022 beside them
Wave G: T023 → T024
```

## Implementation strategy

1. MVP: Phases 1 to 4. `rookery up --no-launch` builds a reader team, and `join` replaces the old `up`.
2. Add Phase 5. Agents run in panes and send mail. This is the first release that a user can run day to day.
3. Add Phases 6 to 9 in order. Each phase ends green and gets its wave audit.
4. Phase 10, then the manual steps.

## Notes

- `test_up_schema.py` is not in the plan's file list. T003 adds it, because the schema proof needs its own file.
- If a test shows that the plan is wrong, stop. Record the finding in research.md and fix the plan before the
  code (document at discovery).
