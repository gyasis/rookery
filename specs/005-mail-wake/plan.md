# Implementation Plan: Event-driven mail wake

**Branch**: `005-mail-wake` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/005-mail-wake/spec.md`

## Summary

Each step that makes a mail `pending` starts one short, detached wake for the recipient (D1, Q1). The Stop hook
starts the same wake when a turn ends with unread mail that needs action. The wake reuses the postmaster rules,
split out as `wake_one`, under a lock for each agent with a recheck before it lets go (D2, Q4). The postmaster
becomes a sweep for one session every 300 seconds, which `rookery up` starts (Q5, Q6). The screener starts on the
same event when screening is on, and its poll goes away (D3). A remote agent checks the mailroom at turn end and
after a send, then at most 4 more times, 5 minutes apart (Q2). `rookery up` gets `--session NAME` and a three-word
default name, so a second run resumes the session (clarification 5). Research: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.10+. Miniconda 3.13 and `/usr/bin/python3` 3.10 both run Rookery.
**Primary Dependencies**: standard library only: `subprocess`, `fcntl`, `sqlite3`, `urllib.request`. No new
dependency.
**Storage**: the mailroom SQLite database, unchanged. No schema change. New files under the project root:
`postmaster/lock/*.lock` and `postmaster/backoff/*.json`. New files on a remote machine:
`~/.rookery/remote/NODE.{json,lock,follow}` ([data-model.md](data-model.md)).
**Testing**: `unittest` files with a temporary `HOME`, `ROOKERY_DB` and `ROOKERY_ROOT` (`test_hooks.Base`). The pane
commands are fake programs in `fixtures/bin` that record their arguments. Spawned processes are replaced by a
recorder in unit tests. One integration test runs the real detached process against a scratch root. The sweep and
the remote follower take an injected clock and sleep function. No test calls a model, runs real herdr or tmux, or
opens the live database (FR-018).
**Target Platform**: Linux and the Mac (`fcntl.flock` and `start_new_session` work on both).
**Project Type**: CLI, hooks and short-lived processes, in a flat Python repo.
**Performance Goals**: a send takes at most 50 ms more with the wake on (SC-006). A notice reaches an idle agent
within 5 seconds (SC-001, SC-002).
**Constraints**: no process runs between mails except the sleeping sweep of each session (D1, D2). The sweep
interval is at least 300 seconds (FR-007). Every retry has a bound: 3 lock rounds (R4), 5 remote checks (R8), the
back-off from 30 s to 600 s.
**Scale/Scope**: one user, a few sessions of about 5 agents, tens to hundreds of mails a day.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gates from `.specify/memory/constitution.md` (v1.3.0):

- [x] **I. Mailroom is the record** — PASS. No schema change. Locks, back-off and remote records are files that
      hold run state, not mail. The database stays the source of the mail and its status.
- [x] **II. Proven, not claimed** — PASS. Each FR and SC has a test in the task list. The risk register gets a row
      for the wake (R19) that moves `built → proven` in the same PR. Fake pane programs, not mocks of `subprocess`,
      prove the typing.
- [x] **III. Any harness** — PASS. The CLI (`rookery wake`, `rookery check`) is the contract. The Stop hook is one
      trigger. A harness without hooks still gets the gate event and the sweep (spec assumption).
- [x] **IV. Code decides; models advise** — PASS. No model call in this feature. The push gate decides in code.
- [x] **V. Simple and surgical** — PASS. No new dependency. `wake_one` is a split of existing code, not a rewrite.
- [x] **VI. Observable and frugal** — PASS. `spawn` reaps its ended children, so a long-running service collects no zombies. Every outcome writes one log line (FR-014): `pushed`, `wake-failed`,
      `wake-busy`, `no-root`, `wake-spawn-failed`, `sweep-busy`, `poll-raised`, `check-failed`. `rookery wake
      --json` prints the outcome. No auto-restart: a dead sweep stays dead until the next `rookery up`.
- [x] **VII. Agents' world out of the human's** — PASS. All new files are under `ROOKERY_ROOT` or
      `~/.rookery`.
- [x] **VIII. Plain technical English** — PASS when spec, plan, research, data model, contracts, quickstart and
      tasks pass ste-lint at 2.0. The tasks include the lint.
- [x] **IX. Rookery native** — PASS. Pane commands stay in the `wake` config. Core names no pane tool and no
      harness (FR-015, FR-026).
- [x] **X. No token spend without a need** — PASS. The wake, the sweep and the remote check call no model. Idle: only the sweep sleeps. Cost grows with
      mail. Bounds: 3 lock rounds, 5 remote checks, the back-off. Screening is off by default (R6), so an update
      adds no model call.
- [x] **Security** — PASS. The notice carries no mail body (as today). The remote check sends the token that
      `rookery join` stored, over the same URL. Nothing deletes data.

**Re-check after Phase 1:** all gates still PASS. The design added files, not tables, and no model path.

## Project Structure

### Documentation (this feature)

```text
specs/005-mail-wake/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── cli.md           # Phase 1: rookery wake, check, up --session, postmaster --session, screen run --once
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
rookery_wake.py        # NEW: after_pending(), spawn(), the lock, the recheck, rookery wake
rookery_remote.py      # NEW: rookery check and the bounded follower (research R8)
postmaster.py          # wake_one() split from wake_identities(); type_notice(); --session sweep; 300 s floor
mail_gate.py           # after commit: after_pending(); screening event when screening is on
rookery.py             # release(), unquarantine(), requeue_dlq(): after_pending() after commit
screener.py            # after_pending() for mail released in a step; --once lock and recheck
cli_screen.py          # --session --once event entry; default --poll 300
cli_hook.py            # Stop hook: local wake for self, or the remote follower when ROOKERY_URL is set
rookery_mcp.py         # after a remote send: the remote follower
cli_workspace.py       # up: --session, three-word default, start the sweep of the session
rookery_team.py        # session optional when --session or the default name gives one
rookery_cli.py         # subcommands: wake, sweep, check
screener_config.py     # screener.enabled (default false)
bootstrap.py           # bundle postmaster.py, rookery_wake.py, rookery_remote.py (the zipapp lacked postmaster.py)
test_wake_*.py         # NEW: core, split, lock, event (gate, release, screener), detached, stop, sweep, cost, remote
test_screen_event*.py  # NEW: screening event only when on, one pass per session, recheck
test_up_session.py     # NEW: --session, three-word name, resume
test_up_sweep.py       # NEW: up starts one sweep
test_wake.py           # existing: still passes after the split (wake_one)
```

**Structure Decision**: two new modules. `rookery_wake.py` holds the event entry. `rookery_remote.py` holds the remote check, so that its tasks can run beside the wake tasks. Everything
else is a small edit at the point where a mail becomes `pending` or where a process starts.

## Phases for tasks

1. **Split, no behavior change**: `wake_one` and `type_notice` out of `wake_identities`. Back-off to a file.
   `test_wake.py` passes unchanged.
2. **US1 event wake**: `rookery_wake.after_pending`, `spawn`, `rookery wake` with the lock and recheck. Calls in the
   gate, `release`, `unquarantine`, `requeue_dlq` and the screener.
3. **US2 Stop hook**.
4. **US3 sweep**: `postmaster --session`, the 300 s floor, the exit on close.
5. **US4 screening event**: `screener.enabled`, the gate event, the `--once` lock.
6. **US5 and US7 `rookery up`**: start the sweep, `--session`, the three-word name.
7. **US6 remote check** and the follower.
8. **Polish**: README, CHANGELOG, SKILL.md, risk register R19, ste-lint, bundle, live check in a scratch herdr
   workspace (`rookery up --no-launch`, fake agents).

## Complexity Tracking

No violations to justify.
