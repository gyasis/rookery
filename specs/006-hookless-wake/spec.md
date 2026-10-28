# Feature Specification: Hookless wake through herdr

**Feature Branch**: `006-hookless-wake`
**Created**: 2026-10-27
**Status**: Draft
**Input**: User description: "Quick fix until the agent-hardening work: when a session runs, one service watches the mail that comes through and, through herdr for now, tells the agent 'check your mail'. It must work for any session and any harness." Correction 2026-10-27: "not one service, one service per rookery up to a session, and it should die when the session ends."

## Background

Feature 005 wakes an idle agent when mail that needs action arrives. The wake step (`postmaster.wake_one`) types a notice into the pane only when the file `agents/<session>/<name>/state/turn.json` says `idle`. Only the Rookery turn hooks write that file: the Claude Code hooks (feature 001) and the pi/omp extension (`feat/pi-extension`).

Other harnesses (Kimi, Codex, Gemini, and any agent started by hand) write no `turn.json`. For them the wake step returns `no-idle-state` and stops. The mail stays unread until a person types in the pane.

### Found 2026-10-27 (session `grid`, herdr workspace `w8`)

- Four panes: Claude, Kimi, omp (MiniMax M2.7) and Codex. Each identity has a `herdr-pane` binding. The session has a `rookery-root` binding.
- `rookery send kimi ... --kind ask` stored mail #132. The event wake ran and logged `no-idle-state`. At the same time `herdr agent get kimi` reported `idle`.
- `rookery wake --session grid --agent kimi` gave the same result: `{"outcome": "no-idle-state", "mail_id": 132}`.
- The notice text says `Read it with: rookery inbox`. An agent whose process has no `ROOKERY_SESSION` / `ROOKERY_NODE` gets "No current Rookery session" from that command.
- The harness table (`rookery_config.default.json` `harness`) has `claude`, `pi` and `omp` only. `rookery up` cannot start Kimi or Codex.
- `rookery up` starts one sweep for each session that it makes (`postmaster.run_session_sweep`). The sweep stops when the session is not `open`. It reads that state only once per round, so it can live up to 300 s after `rookery session end`.

### Found during the live test (2026-10-27)

- The wake typed the notice for mail #132 into the Kimi pane while the user had a draft in the input box. `herdr agent prompt` adds text to the box. It does not replace the text. Kimi got the draft and the notice as one prompt. Herdr reported `idle` while the draft was in the box.
- This comes from the `herdr-pane` wake entry of feature 005. It also applies to Claude panes. The old bridge (`plugin_herdr.bridge.prompt`) saved the screen to `~/.rookery/stash/` and sent `ctrl+u` first. The wake entry does neither. The user decides the fix. This feature does not change the wake entry.

### Done before this feature

- `plugins.probe_status("herdr-pane", value, cache)` asks herdr for the state of a pane. `_busy_is_over` uses it to end a stale `busy` turn (fix `stale-busy`).
- The `herdr-pane` wake entry already types the notice with `herdr agent prompt {value} {text}` (fix `herdr-agent-wake`).
- `postmaster.run_session_sweep`: one sweep for each session, started by `rookery up`, with a lock file for each session. It runs the outbox scan, the wake step and the screener sweep. Feature 005 sets its interval to 300 s or more.

## Decisions

Given by the user on 2026-10-27. The plan must not reopen them.

- **D1. Quick fix.** This feature is a stopgap. The agent-hardening work replaces it later. Keep the change small and use the code paths that exist.
- **D2. Herdr is the notice channel for now.** The watcher tells the agent "check your mail" through herdr.
- **D3. One watcher for each session.** `rookery up` starts one watcher for the session. The watcher stops when the session ends. There is no service for the whole machine. (Correction by the user, 2026-10-27.) The watcher is the session sweep of feature 005. This feature does not add a second process.
- **D4. Any harness.** A harness with no Rookery hooks gets the same wake as Claude.
- **D5. The interval is a setting, and its default is 2 minutes.** The user asked for a time setting with a default of 120 s (2026-10-27). This replaces D2 of feature 005 ("at most every 5 minutes") for the session watcher. The lower limit is 60 s (the user accepted it on 2026-10-27).
- **D6. Wait for an empty input line.** The wake does not type into an input line that has text. It tries again in the next round (the user, 2026-10-28). See FR-016.

## Open questions

For `/speckit-clarify` and plan. The spec does not decide them.

- **Q1.** Answered 2026-10-27: a setting, default 120 s. See D5 and FR-013 to FR-015.
- **Q2.** The exact `cmd` and prompt flag for `kimi` and `codex` in the harness table (FR-012). Plan reads them from each CLI's `--help`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A hookless agent wakes on new mail (Priority: P1)

A person runs Kimi in a herdr pane bound to a Rookery identity. Another agent sends Kimi an `ask`. Kimi is idle at its prompt. Within seconds the pane gets one notice, and Kimi reads its mail.

**Why this priority**: Without this, mail to any non-Claude agent waits for a person. This is the whole fix.

**Independent Test**: Make a session with a `herdr-pane` binding for an agent that has no `turn.json`. Send it an `ask`. Check that `herdr pane read` shows one `[rookery] new mail for` line and the wake log has `outcome: pushed` with `source: herdr`.

**Acceptance Scenarios**:

1. **Given** an identity has a `herdr-pane` binding and no `turn.json`. Herdr reports `idle` or `done`. **When** mail that needs action arrives, **Then** the wake types one notice. It logs `pushed` with `source: herdr`.
2. **Given** the same identity and herdr reports `working`, **When** mail arrives, **Then** the wake types no notice and returns `busy`.
3. **Given** herdr reports `blocked` or `unknown`, or gives no answer. **When** mail arrives, **Then** the wake types no notice. It logs `no-idle-state` with the herdr state as reason (`herdr:blocked`, `herdr:unknown`, `herdr:none`).
4. **Given** an identity with a `turn.json`, **When** mail arrives, **Then** the wake uses `turn.json` exactly as in feature 005. Herdr does not override it.

---

### User Story 2 - The session watcher lives and dies with the session (Priority: P1)

A person runs `rookery up` for a team of Claude, Kimi, omp and Codex. Rookery starts one watcher for that session. A wake fails once (the pane is not ready). The watcher finds the mail on its next round and wakes the agent. The person runs `rookery session end`. The watcher stops within seconds.

**Why this priority**: The event wake is one try. The watcher retries a missed wake. A watcher that lives on after its session wastes a process and can type into panes that now hold other work.

**Independent Test**: Run `rookery up` with a team file. Check that one watcher process runs for the session. Store mail for an idle hookless agent while the event wake is off. Check that the watcher pushes one notice within one interval. Run `rookery session end`. Check that the watcher process is gone within 10 seconds.

**Acceptance Scenarios**:

1. **Given** `rookery up` made the session. **When** the session is open, **Then** exactly one watcher runs for it.
2. **Given** the watcher runs. **When** the session has unannounced mail that needs action for an idle agent, **Then** the watcher pushes one notice within one interval.
3. **Given** the watcher runs. **When** a person runs `rookery session end`, **Then** the watcher logs `sweep-end` and exits within 10 seconds.
4. **Given** `rookery up --session NAME` runs again for an open session. **When** a watcher already runs, **Then** Rookery starts no second watcher.
5. **Given** the event wake and the watcher see the same mail. **Then** the agent gets one notice only.

---

### User Story 3 - The notice works without the environment (Priority: P2)

Codex runs in a pane bound to identity `codex`, but its process has no `ROOKERY_SESSION` / `ROOKERY_NODE`. It gets a notice and runs the command in the notice. The command works.

**Why this priority**: An agent started before the session existed must still read its mail without a restart.

**Independent Test**: Push a notice to a hookless identity. Run the command from the notice in a shell that has no Rookery variables. It lists the mail.

**Acceptance Scenarios**:

1. **Given** the wake used the herdr fallback, **When** it types the notice, **Then** the command in the notice names the session and the identity: `ROOKERY_SESSION=<session> ROOKERY_NODE=<name> rookery inbox`.
2. **Given** the wake used `turn.json`, **Then** the notice text stays the same as in feature 005.

### Edge Cases

- A person closed the pane in the binding, or a different agent now runs in it. Herdr reports no agent for the pane. The wake logs `pane-gone` and types nothing.
- The pane holds a plain shell. `herdr agent prompt` fails. The back-off of feature 005 applies.
- Herdr is not installed or its server is down. The probe gives no answer. The wake logs `no-idle-state` once per mail and types nothing.
- The watcher and an event wake run at the same moment for the same agent. The per-agent lock of feature 005 lets one run. The other logs `wake-busy`.
- A session made with `rookery session new`, not `rookery up`, has no watcher. Its event wakes still use the herdr fallback (FR-001).
- The machine restarts. No watcher comes back. The next `rookery up --session NAME` starts it again (feature 005 resume).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When `turn.json` is missing for an identity, the wake step MUST ask herdr for the state of the identity's first `herdr-pane` binding with `plugins.probe_status`. `idle` and `done` count as idle. `working` counts as busy. Every other answer, and no answer, counts as no idle state.
- **FR-002**: When `turn.json` exists, the wake step MUST behave as in feature 005.
- **FR-003**: The herdr fallback MUST be on by default and MUST obey the existing config key `herdr_probe` (false turns it off).
- **FR-004**: A wake that used the fallback MUST log `source: herdr` with its outcome.
- **FR-005**: A notice typed after the fallback MUST give the full command with `ROOKERY_SESSION` and `ROOKERY_NODE`. The 400-character cap and the safe-name filter of feature 005 still apply.
- **FR-006**: The fallback MUST NOT type into a pane where herdr reports no agent. It logs `pane-gone`.
- **FR-007**: The session sweep of feature 005 is the session watcher. This feature MUST NOT add a second watcher process or a service for the whole machine.
- **FR-008**: While it waits between wake rounds, the watcher MUST read the state of its session at least every 5 seconds. When the state is not `open`, it MUST log `sweep-end` and exit. This read is one row of the database and calls no model. The wake round interval stays as in feature 005.
- **FR-009**: The same 5-second check MUST also stop the watcher in the other cases of feature 005: a replaced database file (`sweep-end`, reason `db-replaced`) and a deleted project root (no log, so that Rookery does not make the folder again).
- **FR-010**: `rookery up` MUST report the watcher in its output (`Sweep: started|running|off|failed`), as in feature 005.
- **FR-011**: `rookery session end` MUST print whether a watcher was running for the session.
- **FR-012**: The default harness table MUST include `kimi`, so that `rookery up` can start it. Kimi has no flag for a system prompt. It reads the generated `AGENTS.md`. Codex stays out: FR-019 of feature 003 keeps Codex out of `rookery up` until R19 is proven. This feature does not change that rule.

- **FR-013**: The watcher interval MUST be a setting with a default of 120 seconds. `rookery_wake.MIN_SWEEP_S` (300) changes to a default of 120 and a lower limit of 60.
- **FR-014**: Three places MUST set the interval. The first one found wins: the flag `rookery up --sweep SECONDS`, then `sweep_s` in the team file, then `sweep_s` in `~/.rookery/config.json`. The session stores the value it used. A resume with no flag keeps that value, also when the team file has a different `sweep_s`. The team value is written only for a session that has no value yet.
- **FR-015**: A value below 60 MUST be raised to 60 with one notice, as feature 005 does for its limit. A value that is not a number MUST be refused with exit 2.
- **FR-016**: Before the wake types into a `herdr-pane`, it MUST read the input line of the pane. If the line has text, the wake MUST type nothing and log `draft-in-box` with the length of the text, never the text. The next round tries again. Dim text is a placeholder and does not count. An agent kind with no known input line, or no input line on the screen, MUST get the wake as before. Config `draft_wait: false` turns the check off.

### Key Entities

- **Identity with herdr-pane binding**: the target of a hookless wake. Unchanged schema.
- **Wake log event**: gains the field `source` (`turn` or `herdr`) and the outcome `pane-gone`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the `grid` session, an `ask` to Kimi, omp or Codex puts one notice in its pane within 5 seconds, with no person typing. (Codex gets the wake through its `herdr-pane` binding. Only its launch by `rookery up` waits for R19.)
- **SC-002**: The agent runs the command in the notice and lists its mail, with or without Rookery variables in its process.
- **SC-003**: Zero double notices for one mail when the event wake and the session watcher both see it.
- **SC-006**: After `rookery session end`, no watcher process for that session runs 10 seconds later.
- **SC-004**: All feature 005 tests still pass.
- **SC-005**: The change to `postmaster.py` is under 80 lines, not counting tests.

## Out of scope

- Turn hooks or extensions for Kimi, Codex or Gemini (the hardening work).
- A service for the whole machine (D3).
- A watcher for sessions made with `rookery session new`.
- Wakes for agents on other machines (feature 005, User Story 6).
- Any notice channel other than the existing `herdr-pane` and `tmux-pane` wake entries.
- The fix for a draft: decided, see D6 and FR-016.
