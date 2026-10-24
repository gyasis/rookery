# Implementation Plan: `rookery up` builds the Rookery project workspace

**Branch**: `003-rookery-up` | **Date**: 2026-10-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/003-rookery-up/spec.md` (decisions D1 to D3 of 2026-10-23,
five clarifications of 2026-10-24)

## Summary

`rookery up TEAM_FILE` reads a JSON team file. It makes the project root, one Rookery session, one identity
and one workspace for each agent, and one git worktree for each writer. It writes the generated views and
runs each agent in a pane with a configured launch command. The old `rookery up` becomes `rookery join` (D1).
The gate log stays in `~/.rookery/logs` (D2). The code already reads `ROOKERY_ROOT` first, so D3 gets proof
tests and no new code.

Agents that `rookery up` runs use the CLI only (no MCP). Each Claude Code agent gets the Rookery hooks
through a generated `_config/settings.json` and `--settings`. The handler text is the same as `rookery init`
writes, so Claude Code runs a handler once when a repo also has it. The team file is the source of
`_config/`. `_threads/` and `_mail/` hold metadata only. A `worktrees` table is the only proof that Rookery
owns a path under `wt/`. `rookery finish` asks three times before it deletes a worktree. `rookery gc` only
lists. Guardrails (guard, watch, claims, ports) stop accidents under 002 D24. Decisions D4 to D23:
[research.md](research.md).

## Technical Context

**Language/Version**: Python 3.10+. It runs on 3.13 here.
**Primary Dependencies**: standard library, `sqlite3`, `git` and the harness binaries (`claude`, `pi`,
`omp`). The pane tools (herdr, tmux) come in through configuration only. No new package.
**Storage**: the mailroom SQLite database, plus four additive tables: `up_agents`, `worktrees`, `claims`,
`ports` ([data-model.md](data-model.md)). Generated files under the project root.
**Testing**: `unittest` files. Each test uses a temporary `HOME`, `ROOKERY_DB` and `ROOKERY_ROOT`, and a
scratch git repo. Launch tests put `fixtures/bin` first on `PATH`, with fake `herdr`, `tmux` and harness
programs that record their arguments, current folder and environment.
**Target Platform**: Linux, one user.
**Project Type**: CLI and hooks in a flat Python repo.
**Performance Goals**: the guard and watch hooks stay inside the spec 001 hook target: under 1 s on 10,000
rows (`test_hooks.HookTiming`). `rookery up` has no time target.
**Constraints**: the host repo does not change (R10). No code for one terminal tool (constitution IX). No
edit of `~/.claude.json` or `~/.claude/settings.json`. Rookery refuses Codex until a test proves R19.
**Scale/Scope**: one user, a few sessions of about 5 agents, one or two repos.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gates from `.specify/memory/constitution.md` (v1.2.1):

- [x] **I. Mailroom is the record**: the new state goes in four new tables (D6). Rookery generates the views
      from the database, and agents only read them. Rookery deletes no row: a worktree that git failed to make
      keeps its registry row as `failed`, and dropped claims become inactive (data-model). Quickstart 1 runs the change on a COPY of the live
      database.
- [x] **II. Proven, not claimed**: each FR has a test in the trace below. R9 to R14 each get the proof test of
      the register (D19), and a task moves each from `open` to `proven` in the OKF register. The live checks carry
      the label UNTESTED until they run (quickstart 5 to 8).
- [x] **III. Any harness**: the contract stays the `rookery` block and the CLI. The hooks are an extra path.
      Research ("Facts") checks the Claude Code features against its docs. pi and omp use their flags
      from `folder-layout.md`. Codex waits for R19.
- [x] **IV. Code decides; models advise**: no model. Validation, guard and watch are code.
- [x] **V. Simple and surgical**: no new package. JSON team file (D4). D3 adds no code. Complexity Tracking
      justifies the items that go past the FR text.
- [x] **VI. Observable and frugal**: `--json` on every new command. Each launch error names the agent, the
      step and the output. Guard and watch decisions go to the `turn-block` log. No new daemon.
- [x] **VII. Agents' world out of the human's**: everything goes under the root. The host repo gets no
      file (R10 test). The hooks go in `_config/settings.json`, not in the host repo or the user configuration.
- [x] **VIII. Plain technical English**: the plan files pass `ste-lint`. The generated agent files and the
      new error texts get a lint test (FR-033).
- [x] **IX. Rookery native**: Rookery makes the session and agent IDs. Panes connect through bindings and
      the `launch` and `alive` configuration (D9).
- [x] **Development Workflow**: `spec-pane cover . 2` passes for spec.md. It runs again on plan.md and
      tasks.md before the build.
- [x] **Security**: mail stays data. `_threads/` and `_mail/` hold no body. `finish` asks three times (or
      `BROTUNDWASSER`) before it deletes a worktree, and no command deletes a branch. `gc` only lists.

**Post-design re-check (after Phase 1)**: all gates pass. Two items stay open by design. First, a local
program can still write anywhere: guard and watch are accident guards (002 D24). Second, the first
interactive launch of each new Claude Code agent needs the human to accept two dialogs (D10).

## Project Structure

### Documentation (this feature)

```text
specs/003-rookery-up/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: facts and decisions D4 to D23
├── data-model.md        # Phase 1: four additive tables, team file rules, agent environment
├── quickstart.md        # Phase 1: copy check, tests, scratch team, live checks, deploy
├── contracts/up.md      # Phase 1: commands, team file, hooks, configuration, generated files
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
cli_up.py -> cli_join.py   RENAME (git mv); texts at :53, :185 say rookery join (D1)
rookery_cli.py             EDIT subparser up -> join; register cli_workspace.add_parser
cli_workspace.py           NEW  up, finish, gc, regen commands; launch steps; first-start note
rookery_team.py            NEW  read and check the team file (D4, D17)
rookery_views.py           NEW  ROOKERY.md, workspace files, _directory, _teams, _threads, _mail (D7, D15)
rookery_worktree.py        NEW  registry, git worktree add, finish, gc (D12 to D14)
rookery_guard.py           NEW  guard and watch logic, claims match, port choice (D16)
cli_hook.py                EDIT register hook guard and hook watch
turn_block.py              EDIT log_event: gate stream under ~/.rookery (D2); ensure_up_tables adds the new tables for the new commands
rookery.py                 EDIT four CREATE TABLE IF NOT EXISTS (D6)
cli_init.py                NO CHANGE (merge_hooks is reused as it is)
rookery_config.default.json EDIT default_binding, launch, alive, harness, ports
bootstrap.py               EDIT module list: cli_join, cli_workspace, rookery_team, rookery_views, rookery_worktree, rookery_guard
fixtures/bin/fakepane fixtures/bin/fakeagent   NEW  a fake pane tool that runs a command; a fake agent that records arguments, folder and environment, and sends mail
fixtures/bin/herdr fixtures/bin/tmux  EDIT print a pane ID for tab create / new-window
specs/002-mail-gate/research.md, spec.md  EDIT D24 pointer: RQ-2.9 is a guardrail (D18)
README.md docs/QUICKSTART.md docs/rookery-explained.html demo_up.sh CHANGELOG.md   EDIT join rename; rookery up docs; layout tree (FR-034)
skills/rookery/SKILL.md    EDIT rookery up, finish, gc; CLI only for these agents
test_up_schema.py test_join_rename.py test_d2_gate_home.py test_d3_root.py   NEW
test_team_file.py test_up_build.py test_up_launch.py test_up_idempotent.py   NEW
test_views.py test_worktree.py test_finish_gc.py test_guard.py test_ports.py test_up_e2e.py   NEW
test_gate_log.py test_text_hints.py   EDIT (D2 path; cli_join.py file name)
```

**Structure Decision**: keep the flat layout. Each new module holds one concern, so that each test file maps
to one module.

## Requirement Trace

Phase requirements and the FRs that build them:

| RQ | FR | RQ | FR |
|---|---|---|---|
| RQ-2.1 | FR-005 | RQ-2.10 | FR-029 (claims) |
| RQ-2.2 | FR-007 | RQ-2.11 | FR-029 (ports), FR-008 |
| RQ-2.3 | FR-025, FR-026 | RQ-2.12 | FR-022 |
| RQ-2.4 | FR-011 | RQ-2.13 | FR-023 |
| RQ-2.5 | FR-012 | RQ-2.14 | FR-014, FR-017 |
| RQ-2.6 | FR-020 | RQ-2.15 | FR-024 |
| RQ-2.7 | FR-015, FR-016 | RQ-2.16 | FR-027 |
| RQ-2.8 | FR-008 | RQ-2.17 | FR-033 |
| RQ-2.9 | FR-029 (guard, watch) | RQ-R.3 | FR-013 |
| | | RQ-R.4 | FR-004, FR-025 (`_teams/`) |

| FR | Built in | Proof |
|---|---|---|
| FR-001 | `rookery_cli` `join`. `cli_join.py`. URL hint in `up` (D1) | `test_join_rename`: `join` parses the old flags. `up http://…` exits 2 with the hint. Old peer tests run against `join` (SC-007) |
| FR-002 | `turn_block.log_event` gate stream (D2) | `test_d2_gate_home`: two roots, both decisions in `~/.rookery/logs/gate.jsonl` (SC-006) |
| FR-003 | no code (D3) | `test_d3_root`: each of the five sites from a workspace cwd uses `ROOKERY_ROOT`. With no `ROOKERY_ROOT`, the old behavior |
| FR-004, FR-009 | `rookery_team.load` (D4) | `test_team_file`: each rule of data-model. A bad file changes nothing and names each entry |
| FR-005 | `cli_workspace` root resolve (D5) | `test_up_build`: root outside the repo. Relative root refused. Host `git status` clean (R10) |
| FR-006 | session and identities in one transaction (D21, D22) | `test_up_idempotent`: second run, 0 new sessions, 0 duplicates (SC-003) |
| FR-007 | `rookery_views.rookery_md` | `test_views`: `ROOKERY.md` exists. No `CLAUDE.md` or `AGENTS.md` in root, `agents/`, `agents/<s>/` |
| FR-008 | launch environment (data-model) | `test_up_launch`: recorded environment has the five variables, absolute paths, `ROOKERY_NODE` = agent ID |
| FR-010 | atomic rewrite of generated files only (D7) | `test_up_idempotent`: `runs/`, `outbox/`, `HANDOFF.md` unchanged |
| FR-011 | `rookery_views.workspace_files` (D7) | `test_views`: every item of the D7 table |
| FR-012 | reader `CLAUDE.md` import. `HOST_RULES.md` symlink (D7) | `test_views`: `@<abs>/CLAUDE.md`. Symlink target (R11). No host CLAUDE.md gives no import and a note |
| FR-013 | `scope-card.md` (D7) | `test_views`: "does NOT do" section. Only cards of the same session |
| FR-014 | `launch` steps (D9) | `test_up_launch`: fake tmux and herdr get the configured argv. Scan test: no `herdr`/`tmux` literal in the new modules |
| FR-015 | cwd per role (D11) | `test_up_launch`: reader cwd = workspace, writer cwd = worktree |
| FR-016 | harness flags (D11) | `test_up_launch`: writer argv has `--settings` and `--append-system-prompt-file`. Pi gets `--append-system-prompt` |
| FR-017 | `capture` then `rookery_identity.bind` (D9) | `test_up_launch`: binding `tmux-pane:%N` stored. `test_wake` style wake reaches it |
| FR-018 | `alive` check. Per-agent report (D9) | `test_up_launch`: alive agent skipped. One failed step reported, the other agent launches. Missing `launch` kind named |
| FR-019 | `rookery_team` Codex rule (D17) | `test_team_file`: `codex` refused, nothing changed |
| FR-020 | `rookery_worktree.add` (D12) | `test_worktree`: path, branch name. Existing branch reused, its commit unchanged |
| FR-021 | `git check-ref-format --branch` (D12) | `test_team_file`: `a..b`, `x.lock` refused for a writer |
| FR-021a | registry check (D12) | `test_worktree`: a human folder at `wt/<session>/` refuses the session. `finish` and `gc` ignore it |
| FR-022 | `rookery_worktree.finish` (D13) | `test_finish_gc`: three `yes` answers commit and delete. Any other answer changes nothing. Branch kept |
| FR-023 | `rookery_worktree.gc` (D14) | `test_finish_gc`: lists the three stale kinds. File and branch counts the same before and after (R14, SC-008) |
| FR-024 | forbidden list (D12) | `test_worktree` scan: no `GIT_DIR`, `GIT_WORK_TREE`, `LD_PRELOAD`, `overlay`, `node_modules` symlink in the new modules |
| FR-025 | `_directory/`, `_teams/` (D15) | `test_views`: INDEX fields. `rookery regen` after a roster change updates INDEX |
| FR-026 | `_threads/INDEX.md` (D15) | `test_views`: a mail body fixture text never appears under `_threads/` |
| FR-027 | `_mail/` mirror (D15) | `test_views`: quarantined and held rows appear by metadata. No body |
| FR-028 | generators use `SELECT` only (D15) | `test_views`: unread count and every `read_at` the same before and after `regen` |
| FR-029 | `rookery_guard`. `hook guard`, `hook watch`. Claims. Ports (D16). D24 pointer (D18) | `test_guard` (R9, R12), `test_ports` (R13). Review of the 002 text |
| FR-030 | CLI only. No MCP entry written (clarify 1) | `test_up_e2e`: a fake agent sends with `rookery send`, the row's sender is its agent ID. `test_views`: no MCP tool name in generated files. `~/.claude.json` unchanged |
| FR-031 | `_config/settings.json` with `merge_hooks` (D8) | `test_up_build`: handlers equal to `rookery init` output. `--settings` in argv. No write to host or user configuration |
| FR-032 | `_config/` from the team file (D7) | `test_up_idempotent`: a new session from the same team file gives the same `_config/` |
| FR-033 | lint of generated files and error texts | `test_views` runs `ste-lint` on each rendered file |
| FR-034 | README and docs layout tree | review. `test_text_hints` checks the tree text names `postmaster/`, `logs/`, `state/` |

SC-001 to SC-010 map to the same tests.
SC-001 `test_up_build`, SC-002 R10 in `test_up_build`, SC-003
`test_up_idempotent`, SC-004 `test_up_launch`, SC-005 `test_up_e2e` (and quickstart 7), SC-006
`test_d2_gate_home`, SC-007 `test_join_rename`, SC-008 `test_finish_gc`, SC-009 quickstart 6 (live, UNTESTED
until run), SC-010 the register update after `test_guard`, `test_ports`, `test_worktree`, `test_finish_gc`.

## Complexity Tracking

No gate needs a justification. These items go past the literal FR text:

| Item | Why the design needs it | Simpler choice and why not |
|---|---|---|
| `up_agents` table | `_directory/INDEX.md` needs harness and role from the database (FR-025) | read the team file at regen time: the file can move, and INDEX would not come from the database |
| `rookery regen` | RQ-2.3 and RQ-2.16 ask for a regen test; status in INDEX goes stale between runs | regenerate only in `rookery up`: a status change needs a full `up` run |
| `capture` in launch steps | herdr opens a pane in one step and runs the command in a second step with the pane ID | bind only from the SessionStart hook: a pane with no hook (pi, a failed launch) gets no binding |
| `HOST_RULES.md` symlink | the AGENTS.md fallback of FR-012 for harnesses that do not follow `@` imports | copy the host file: it goes stale |
| URL hint in `up` | users with the old `rookery up URL` in notes get a clear next step | a plain argparse error: the user does not learn about `join` |

## Open items for tasks

- The OKF register (R9 to R14) and the five design changes of D20 are edits in the OKF bundle, outside this
  repo. A task makes them after the tests pass.
- The herdr `capture` pattern needs quickstart 5 before the default configuration ships as tested.
