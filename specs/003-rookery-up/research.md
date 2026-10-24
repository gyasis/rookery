# Research: `rookery up` builds the Rookery project workspace

**Feature**: [spec.md](spec.md). **Date**: 2026-10-24.

Three research passes fed this file. Each pass read the sources and did not change code.

- A code map of `master` b01916f (this branch), with file and line for each item.
- The Claude Code docs at `code.claude.com/docs/en/` (cli-reference, settings, memory, hooks, permissions,
  security), read on 2026-10-24.
- The OKF design: `folder-layout.md`, `icm-rookery-workspace.md`, `harness-contract.md`,
  `requirements/rookery-0-7.md:55-75,102-103`, `risks/register.md:21-31`, `roles/`.

The spec holds the user's decisions D1 to D3 and the clarify answers of 2026-10-24. This file numbers the
plan decisions from D4. Each decision has its reason and the choices that the plan did not take.

## Facts that the plan depends on

| Fact | Source |
|---|---|
| `project_root()` already returns `ROOKERY_ROOT` first. It does not check that the path is absolute. | `turn_block.py:49-54` |
| The five call sites (`cli_rookery_session.py:111,244,274`, `cli_hook.py:623`, `postmaster.py:675`) pass `basename(cwd)` to `project_root()`, which ignores it when `ROOKERY_ROOT` is set. | code map |
| The gate log goes to `<gate_root()>/logs/gate.jsonl`. `gate_root()` is `ROOKERY_ROOT`, else `~/.rookery`. | `turn_block.py:23-27,93-95` |
| `rookery init` writes three hooks. Each command is the text `rookery hook <sub> --project <project>`. | `cli_init.py:229,394` |
| `merge_hooks(path, project)` treats a missing file as `{}`. So it can write a new settings file. | `cli_init.py:406` |
| `rookery send` reads `ROOKERY_SESSION`, `ROOKERY_NODE`, `ROOKERY_ROOT` and `ROOKERY_DB`. It never reads `~/.claude.json` or the MCP node. It does not make an identity (`create=False`). | `cli_hook.py:563-623` |
| A hook that resolves with `create=True` binds the pane variables that it sees (`HERDR_PANE_ID`, `TMUX_PANE`). | `rookery_identity.py:405-470` |
| The core is stdlib only. There is no YAML parser. | README line 79; no `import yaml` |
| `wake` holds one command list for each binding kind. There is no launch configuration. | `rookery_config.default.json` |
| No code exists for worktrees, ports, claims, `ROOKERY.md`, `_directory`, `_teams`, `_threads` or `_mail`. | code map |
| `--settings` merges with the other settings files. Hooks from all levels run. "If you define the same handler in more than one settings file, it runs once." | docs: settings, hooks |
| `--append-system-prompt-file` works in interactive mode. | docs: cli-reference |
| Claude Code loads `CLAUDE.md` from the current folder and from each parent folder. It does not load a file named `ROOKERY.md`. | docs: memory |
| An `@` import of a file outside the current folder shows an approval dialog the first time, in an interactive session. | docs: memory |
| A first interactive start in a folder that is not trusted shows the trust dialog. The only documented way to skip it is to edit `~/.claude.json`. | docs: security, permissions |
| A hook process gets the environment of its parent. The docs do not say this for Bash tool commands. | docs: hooks |
| herdr opens a pane with `herdr tab create --cwd PATH --env K=V --label TEXT` and runs a command with `herdr pane run PANE CMD`. tmux does both with `tmux new-window -P -F '#{pane_id}' -c PATH -e K=V CMD`. | `herdr --help`, tmux man page |

## D1 to D3 (user decisions): how to build them

- **D1.** Rename the subparser `up` to `join` in `rookery_cli.py:75-89`. Rename the file `cli_up.py` to
  `cli_join.py` (with `git mv`), so that the name `up` belongs to the new command only. Update
  `bootstrap._PY_MODULES`, `test_text_hints.TEXT_FILES`, the README file table, `docs/QUICKSTART.md`,
  `docs/rookery-explained.html`, `demo_up.sh` and the two `rookery up` texts in `cli_up.py:53,185`. If the new
  `rookery up` gets an argument that starts with `http://` or `https://`, it exits 2 and says "Use
  `rookery join URL`". This helps users who have the old command in their notes. The standalone script
  `rookery_join.py` keeps its name. It is not a subcommand, and the plan does not touch it.
  - *Not taken*: keep the file name `cli_up.py`. The name would then point at the wrong command.
- **D2.** In `turn_block.log_event`, the `gate` stream always writes under `~/.rookery`, whatever
  `ROOKERY_ROOT` holds. `gate_root()` keeps its other job: it is the project root that `resolve()` uses. The
  `turn-block` and `postmaster` streams stay in each project root. Old gate logs in project roots stay where
  they are. Nothing deletes them. `test_gate_log.py:6,53` changes to match.
  - *Not taken*: change `gate_root()`. Four callers use it as the project root for identity resolve, not
    for the log.
- **D3.** The code already reads `ROOKERY_ROOT` first. So D3 needs proof, not new code. Each of the five call
  sites gets a test that runs it with the current folder set to a workspace and `ROOKERY_ROOT` set to the
  project root. `rookery up` always sets an absolute `ROOKERY_ROOT` (FR-008). The plan does not make
  `project_root()` refuse a relative path, because a hook must never exit non-zero, and a relative root can
  only come from a user who set it by hand.
  - *Not taken*: a new helper for the five sites. It would change working code for no new behavior.

## D4. The team file is JSON

The team file is JSON, given as `rookery up TEAM_FILE`. Constitution V keeps the core stdlib only, and the
stdlib has no YAML parser. A hand-written YAML subset would be new code that can fail in new ways. The
contract is in [contracts/up.md](contracts/up.md).

The team file must name the session. A team file with no session name would get a new session on each run,
and that breaks FR-006 and SC-003.

- *Not taken*: YAML (new dependency); Markdown with front matter (needs a parser for the front matter).

## D5. Project, host repo and root

- The host repo is the git top folder of the current folder. If the current folder is in a linked worktree,
  the host repo is the main worktree: the parent of `git rev-parse --git-common-dir`. This covers the edge
  case "the user runs `rookery up` inside a worktree of the host repo".
- The project name is the base name of the host repo, as `cli_init._default_project` does.
- The root is `ROOKERY_ROOT` if it is set and absolute, else `~/.rookery/projects/<project>/`. `rookery up`
  refuses a relative `ROOKERY_ROOT` and a root inside the host repo, with the same checks as `cli_init.py:544-548`.
- `rookery up` holds `cli_init.init_lock(root)` while it builds, so that two runs do not race.

## D6. New state goes in the database (additive)

Constitution I: SQLite is the record, and files are views. Four new tables hold the new state. Each change
only adds a table. The quickstart runs the migration on a COPY of the live database.

- `up_agents`: the profile of each agent that `rookery up` made (harness, role, repo, task ID, binding
  kind). `_directory/INDEX.md` needs these fields, and the database has no other place for them.
- `worktrees`: the worktree registry (clarify answer 5, FR-021a).
- `claims`: file patterns that an agent owns (FR-029).
- `ports`: the port map of each agent (FR-029).

The shapes are in [data-model.md](data-model.md).

- *Not taken*: keep this state in JSON files under the root. That puts a second record beside the database
  (constitution I).

## D7. The workspace and its generated files

`rookery up` makes `agents/<session>/<name>/` with these items:

| Item | Writer | Content |
|---|---|---|
| `CLAUDE.md` | generated | identity (name, session, role), the routing to `ROOKERY.md` and `CONTEXT.md`, and for a reader `@<host>/CLAUDE.md` |
| `AGENTS.md` | generated | the same text with no `@` import, and a pointer to `HOST_RULES.md` |
| `HOST_RULES.md` | symlink | to the host `AGENTS.md`, else to the host `CLAUDE.md` (the AGENTS.md fallback of FR-012) |
| `CONTEXT.md` | generated | how this agent stages its work: the three stage folders and the `HANDOFF.md` rule |
| `_config/persona.md` | generated from the team file | voice, rules, "read the roster before you ask the user" |
| `_config/scope-card.md` | generated from the team file | "does" and "does NOT do"; the cards of the same team only (RQ-R.3) |
| `_config/system-prompt.md` | generated | persona plus scope card, for `--append-system-prompt-file` |
| `_config/settings.json` | generated | the Rookery hooks (D8) |
| `runs/`, `outbox/`, `state/` | agents | made empty if missing; `rookery up` never writes inside them |

One writer for each item (constitution VII): Rookery owns the generated files, `HOST_RULES.md` and `_config/`.
The agent owns `runs/`, `outbox/` and `state/`. No Rookery command writes inside an agent item, and the
agent must not edit a Rookery item (the next run writes it again).

A second run rewrites each generated file atomically (`cli_init.write_text_atomic`). It does not touch
`runs/`, `outbox/`, `state/` or any `HANDOFF.md`.

The root gets no `CLAUDE.md`, because Claude Code loads `CLAUDE.md` from every parent folder. That is the
reason for the name `ROOKERY.md` (C9). A test checks that `rookery up` writes no `CLAUDE.md` or `AGENTS.md`
in the root, in `agents/` or in `agents/<session>/`.

- *Not taken*: write `HOST_RULES.md` as a copy. A copy goes stale when the host rules change.

## D8. Hooks reach each agent through `_config/settings.json`

`rookery up` writes `_config/settings.json` with `cli_init.merge_hooks(path, project, with_permissions=True)`.
That gives the same three handlers with the same command text that `rookery init` writes. It also adds
the `rookery inbox`, `peek` and `ack` allow rules, so that the agent gets no prompt for its own mail. The docs
say that the same handler in two settings files runs once. So a worktree whose repo has the hooks committed
still sends each mail once (FR-031). The project string must be the same in both files, and D5 makes it the
same.

The code accepts a Rookery handler with a path in front of `rookery` (`cli_init._OURS`). So a repo can have
its hooks committed with other command text than `merge_hooks` writes, and the two texts would not match. For a
writer, `rookery up` first reads the writer repo's committed `.claude/settings.json`. If it has Rookery handlers
(`cli_init._is_rookery`), the generated file copies their exact command text for the same events. Else it uses
the `merge_hooks` text. A reader runs in its workspace, where no project settings file exists, so the
`merge_hooks` text is enough.

The generated file also gets two handlers that `rookery init` does not write: `PreToolUse` runs
`rookery hook guard`, and `PostToolUse` on `Bash` runs `rookery hook watch` (D16).

Every Claude Code agent gets `--settings`, readers too. The OKF launch table gives `--settings` to writers
only (`folder-layout.md:38`). A reader with no `--settings` has no Stop hook, so it sends no mail. This plan
changes the design on that point and records it in D20.

- *Not taken*: a `.claude/settings.json` in the workspace (a writer would not have it); the user-level
  settings (FR-031 forbids it).

## D9. Launch through configured commands (constitution IX)

A new configuration key `launch` gives the steps for each binding kind. A second key `alive` gives a check
for each kind. The defaults cover `herdr-pane` and `tmux-pane`. Rookery has no code for one terminal tool.
A new tool needs only configuration.

- A step is an argument list with placeholders: `{cwd}`, `{label}`, `{env}` (expanded with the kind's
  `env_flag`), `{cmd}` (the harness command, expanded to many arguments) and `{pane}`.
- A step may have `capture`, a regular expression with one group. It reads the pane ID from that step's
  output and fills `{pane}` for the later steps.
- After the last step, `rookery up` binds the identity to `<kind>:<pane>` with `rookery_identity.bind`. The
  postmaster can then wake the agent (FR-017). The SessionStart hook also binds the pane variable that it
  sees. Both give the same binding.
- Before it runs an agent, `rookery up` runs the `alive` check on each pane binding of the identity. Exit
  0 means the agent runs, so `rookery up` skips it (FR-018).
- If the configuration has no `launch` for the agent's binding kind, `rookery up` builds the workspace,
  runs no agent and names the kind.

The herdr output format of `tab create` was not checked on a live server, so that the check did not open a
tab in the user's session. The quickstart has a live check for it. The default `capture` for herdr is
UNTESTED until that check passes.

- *Not taken*: Python code for herdr or tmux (constitution IX).

## D10. The first interactive start shows two dialogs

Each workspace is a new folder outside every repo. On the first interactive start, Claude Code shows the
trust dialog. A reader's `@` import of the host CLAUDE.md also shows the external import dialog. The only
documented way to skip both is to edit `~/.claude.json`, and FR-030 forbids that. So the human answers both
dialogs once in each new pane. `rookery up` prints one line for each new Claude Code agent: "First start:
accept the trust and import dialogs in pane <pane>." The postmaster does not wake an agent whose
`state/turn.json` does not exist yet, so a wake cannot type into an open dialog.

- *Not taken*: write `hasTrustDialogAccepted` into `~/.claude.json` (forbidden by FR-030 and constitution VII).

## D11. Harness commands and flags

| Harness | Reader | Writer |
|---|---|---|
| Claude Code | cwd = workspace. `claude --settings <ws>/_config/settings.json --add-dir <host>` | cwd = worktree. `claude --settings <ws>/_config/settings.json --append-system-prompt-file <ws>/_config/system-prompt.md --add-dir <ws> --add-dir <root>/_directory` |
| pi, omp | cwd = workspace. `pi` (it reads `AGENTS.md`) | cwd = worktree. `pi --append-system-prompt "<system prompt text>"` |
| Codex | refused until R19 is proven (FR-019) | refused |

The harness commands are configuration too (key `harness`), so that a user can change the binary or add a
flag. The table above is the default.

pi and omp get the Rookery hooks through the extension that `rookery init` installs. `_config/settings.json`
is for Claude Code only. FR-031 names Claude Code agents.

## D12. Worktrees

- Path: `<root>/wt/<session>/<name>/<repo>/`. Branch: `agent/<session>/<name>/<task_id>`. A writer must have a
  `task_id` in the team file.
- `rookery up` checks each branch with `git check-ref-format --branch`. It refuses a writer name, session
  name or task ID that fails (FR-021). `safe_name` stays as it is for readers.
- If the branch exists, `git worktree add <path> <branch>` uses it with no reset. Else
  `git worktree add -b <branch> <path>` makes it from the host's current `HEAD`.
- `rookery up` refuses the session if `wt/<session>/` exists and the registry has no row for it (FR-021a). This
  protects the human worktrees that are already under `wt/`.
- Each new worktree gets a `pending` registry row in the same transaction as the identity. Git success sets
  `active`, and git failure sets `failed`. No command deletes a registry row (data-model).
- No in-repo worktree, no symlinked `node_modules`, no OverlayFS, no custom shell, no `LD_PRELOAD`, no hand-set
  `GIT_DIR` or `GIT_WORK_TREE` (FR-024). A scan test checks the new modules for these names.

## D13. `rookery finish NAME`

1. Find the active registry row for `NAME` in the session (`--session`, else `ROOKERY_SESSION`).
2. Show `git status --short`, `git diff --stat` and the files changed that another agent claims (R12).
3. Ask for confirmation three times. This is a destructive action, and the constitution asks for 3×
   confirmation or `BROTUNDWASSER`.
4. Commit with `--message`, push to the branch's remote if the repo has a remote (else say so), and run
   `git worktree remove`. Never delete the branch.
5. Set the registry row to `finished`.

Any git failure stops `finish` at that step. The registry row stays `active`, and the message names the
step and the git error.

## D14. `rookery gc` only lists

`rookery gc` lists the registry rows that are stale. Data-model gives the rules: the session ended; a
`finished` row whose path still exists; an `active` row whose path is missing; a `failed` row; a `pending`
row older than one hour.

- *Not taken*: `gc --delete` with confirmation. The constitution keeps `gc` to listing. A delete belongs to
  `finish`.

## D15. Generated views

`rookery up` writes the views at the end of each run. A new command `rookery regen` writes them again from the
database, with no other change. RQ-2.3 and RQ-2.16 ask for a regen test.

| View | Content |
|---|---|
| `ROOKERY.md` | L0 routing: what the root holds and where to look |
| `_directory/INDEX.md` | each identity of open sessions: name, harness, pane, status, current task, role, team. Status is the `state` of `state/turn.json` (`idle`, `busy`), or `none` when the file is missing. Current task is the name of the newest `runs/<TASK-ID>/` folder, else `up_agents.task_id`, else `-`. Pane is the newest pane binding. |
| `_directory/ports.md`, `_directory/claims.md` | the port map and the claims |
| `_teams/<session>.md` | task, acceptance criteria, members (RQ-R.4) |
| `_threads/INDEX.md` | one line for each thread: thread, sender, recipient, topic, time, state. No body. |
| `_mail/quarantine/<reason>/<id>.md`, `_mail/review/<id>.md` | the metadata of each quarantined or held row. No body. |

Every generator reads the database with plain `SELECT` statements and writes no row, so nothing becomes
read (002 D11). `_mail/` holds no body for the same reason as `_threads/`: an agent must not read a
quarantined body, and the human reads it with the screening tools. Each view starts with a "generated, do
not edit" line and the time.

## D16. Guardrails (under 002 D24)

- **Guard** (`rookery hook guard`, PreToolUse, matcher `Write|Edit|NotebookEdit`): it reads the identity from
  the environment and the allowed folders from the database (the workspace and the active worktree). It
  denies a write outside them with the documented `permissionDecision: "deny"` output (R9). A write to a
  file that another agent claims gets an allow with a warning (R12).
- **Watch** (`rookery hook watch`, PostToolUse, matcher `Bash`): it reads the command text and looks for
  write targets (`>`, `>>`, `tee`, the last argument of `cp`, `mv` or `install`, `touch`, `mkdir`). It logs each
  target outside the allowed folders to the `turn-block` log and returns a warning. This is a text check: it
  catches common accidents and does not catch everything (D24).
- **Claims**: a team file entry `claims` gives file patterns for an agent. They go in the `claims` table.
- **Ports**: the configuration gives a port range and the names. `rookery up` gives each agent one free port
  for each name. A port is free if no other agent has it in the `ports` table and a test bind on
  `127.0.0.1` succeeds. The map goes in `ROOKERY_PORTS` as JSON (R13).

The guard and the watch never exit non-zero by mistake: an internal error gives an allow and a log line, as
the other hooks do.

## D17. Codex waits for R19

A team file entry with `harness: "codex"` fails validation with the message "Codex waits for R19". The
whole run changes nothing (FR-009, FR-019).

## D18. The 002 D24 pointer

The spec of 002 (D24) says that RQ-2.9 gives stronger local controls. This feature corrects that text in
`specs/002-mail-gate/research.md` and `spec.md`: RQ-2.9 is a guardrail against accidents, not security.

## D19. Proof of the risks

| Risk | Automatic test | Live check (quickstart) |
|---|---|---|
| R9 | guard denies a Write outside; watch flags `echo x > /tmp/foreign` | one Claude Code agent tries a Write outside |
| R10 | after `rookery up` on a fixture repo, `git status --porcelain` is empty and the `pytest --collect-only -q` count is the same (the register's proof) | the same on the Rookery repo |
| R11 | the reader's CLAUDE.md has `@<abs host>/CLAUDE.md`; `HOST_RULES.md` points at the host file | a reader quotes a host-only rule (SC-009) |
| R12 | guard warns on a write to a file that another agent claims; `finish` lists claimed files | none |
| R13 | two agents get different ports and both bind | none |
| R14 | `gc` lists an ended session's worktree and deletes nothing; a branch count is the same before and after | none |

The constitution asks the PR to move each risk from `built` to `proven` in the register. The register is in
the OKF bundle, outside this repo. A task updates it after the tests pass.

## D20. Design changes to record in the OKF bundle

This feature differs from the OKF design in five places. The PR lists them, and a task updates the bundle:

1. Readers get `--settings` too (`folder-layout.md:38` gives it to writers only). Reason: D8.
2. `_threads/` holds metadata only (`icm-rookery-workspace.md:31` and RQ-2.3 say ask, answer and verdict
   files). Reason: clarify answer 4.
3. The team file is the source of `_config/` (`icm-rookery-workspace.md:36` says the scope card is the source
   of `_directory`). Reason: clarify answer 3.
4. The worktree registry is a database table and the only proof of ownership. The design names a registry but
   does not define it. Reason: clarify answer 5.
5. `harness-contract.md:23` lists the MCP `send` tool as an adapter. For agents that `rookery up` runs, the
   contract is the CLI only. Reason: clarify answer 1.

## D21. A second run

| Item | Second run |
|---|---|
| Session | `get_session(name)`; reuse it. An ended session: refuse, and tell the user to choose a new name. |
| Identity | `get_identity(name, session)`; reuse it. |
| Generated files | rewrite them. |
| Agent files | do not touch them. |
| Worktree | reuse the registry row and the path. |
| Running agent | skip it (D9). |
| Ports, claims | keep the ports that the agent has. Add new claims, and mark dropped claims inactive (no delete). |

## D22. Order of work in one run

1. Read and check the whole team file (FR-009). On any error, exit 2 and name each bad entry. Change nothing.
2. Resolve the host repo, project and root (D5). Take the lock.
3. In one database transaction: the session, the identities, `up_agents`, `ports`, `claims`, and the
   `worktrees` rows for new worktrees.
4. Make the worktrees with git. Set each registry row to `active`, or to `failed` when git fails, and report it.
5. Write the workspaces and the generated views.
6. Run the agents (D9). Report each agent that did not come up, with the reason. Keep the others.
7. Print a summary, or JSON with `--json`.

## D23. Tests use fixtures, not live tools

The automatic tests use a fake launch command (a script in `fixtures/bin/`) that writes its arguments,
current folder and environment to a file. The end-to-end test (SC-005) uses a fake agent: a script that runs
`rookery send` in the environment that `rookery up` gave it. The live Claude Code, herdr and tmux checks are
in the quickstart. Each one is UNTESTED until the user or a session runs it.
