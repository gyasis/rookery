# Feature Specification: Event-driven mail wake

**Feature Branch**: `005-mail-wake`
**Created**: 2026-10-27
**Status**: Draft
**Input**: User description: "Event-driven mail wake (feature 005). The full input is SPEC_INPUT_mail-wake.md at the repo root: read it and every file it names (R-SK4). Copy the locked decisions D1-D4 into the spec as decisions. Keep Q1-Q7 as open questions marked for /speckit-clarify, research and plan — do not decide them in the spec. Branch 005-mail-wake already exists (do not create another). Short name: mail-wake."

## Background

An agent sees new mail only at a turn boundary. If agent A sends mail to agent B while B is idle at its prompt, B does not see the mail. The mail waits until a person types in the pane of B.

Rookery has one tool that wakes an idle agent: the postmaster. It reads the whole mailroom every 0.5 seconds. When it finds mail that needs action for an idle agent, it types one notice into the pane of that agent. That notice starts one turn.

Source of the need: `okf/design/mail-wake.md` (user, 2026-10-25). This feature is a follow-up to Rookery 0.7.0 and is not in `requirements/rookery-0-7.md`, so it has no RQ IDs.

### Done before this feature (evidence: `master` 2cea954, tag `v0.7.0`)

- The postmaster wake logic (`postmaster.py` `wake_identities`). It has the push gate (`_needs_push`) and the record of announced mail (`postmaster/pushed/`). It also has the back-off from 30 s to 600 s, `busy-stale` and the herdr probe. Feature 002 and the busy-stale fix.
- The mail gate (`mail_gate.submit`): every send path stores mail through it. Feature 002.
- The Stop hook (`cli_hook.stop`) writes the state `idle` when a turn ends. Feature 001.
- The screener (`screener.run`) reads mail that has no decision every 2 seconds. A live check can hold a mail as `held` with reason `screening`. Feature 004.
- `rookery up` makes sessions, identities and panes. It starts no postmaster and no screener. Feature 003.

### Found 2026-10-27

No postmaster runs on the machine of the user. Only the mailroom service and the MCP service run. So today Rookery never wakes an idle agent.

## Decisions

Locked by the user on 2026-10-27 (AskUserQuestion). The plan must not reopen them.

- **D1. The gate and the Stop hook start the wake.** The gate stores a mail that needs action for an idle recipient. Then it starts one short wake for that recipient, detached from the send. The Stop hook starts the same wake when the agent has unread mail that needs action. No process runs between mails. The design is the same on Linux and on the Mac.
- **D2. The postmaster becomes the slow sweep.** Its wake logic stays the one wake code path: the push gate, the back-off, `busy-stale` and the herdr probe. Its loop default changes from 0.5 seconds to a safety-net sweep of 5 minutes or more. The sweep reads only the database and calls no model.
- **D3. The screener uses the same event.** The event "mail stored" starts screening, so the screener stops its 2 second poll. The screener keeps a slow sweep as its own safety net.
- **D4. No `nudge_s`.** A per-agent delay before a wake is not in this feature.

## Open questions

These go to `/speckit-clarify`, research and plan. The spec does not decide them.

- **Q1.** When a live screener check holds a mail as `screening`, the wake must come after the screener delivers the mail, not at the store. Which event wakes the recipient in that case? And in shadow mode?
- **Q2.** The mailroom HTTP `/send` stores mail on the mailroom machine. The recipient can be on a different machine. Is the wake local only in this feature, with the sweep for other agents?
- **Q3.** A detached wake needs the config and the root of the project. The sender can be the MCP server or the mailroom service, not a shell in the session. How does the wake get them?
- **Q4.** How does Rookery make sure that only one wake for an agent runs at a time? Options: a lock file, a database row, or the back-off record in the database.
- **Q5.** Does `rookery up` start the slow sweep for each session, does one sweep run for each machine, or does a user service run it?
- **Q6.** What is the default sweep interval (5 minutes or more)? Is it for each session or for all sessions?
- **Q7.** Migration: what happens to a user who starts `postmaster.py --poll 0.5` by hand?

## User Scenarios & Testing *(mandatory)*

### User Story 1 - New mail wakes an idle agent at once (Priority: P1)

Agent A sends an ask to agent B. B is idle at its prompt. B starts a turn and reads the ask. No person types in the pane of B, and no process polls.

**Why this priority**: This is the gap. Today the mail of an idle agent waits for a person.

**Independent Test**: In a scratch project with a fake pane command, store an ask for an idle identity. Check that one wake ran for that identity with the pane command of the binding. Check that no loop process ran.

**Acceptance Scenarios**:

1. **Given** B is idle and has a wake binding, **When** A sends B an ask, **Then** one wake runs for B. The log shows `pushed` with the mail ID.
2. **Given** B is busy, **When** A sends B an ask, **Then** no wake runs. B gets the mail at its next turn boundary, as today.
3. **Given** B is idle, **When** A sends B a mail of kind `status`, **Then** no wake runs (push gate).
4. **Given** the pane command fails, **When** A sends B an ask, **Then** the send still succeeds, and the log shows `wake-failed` with the reason.

---

### User Story 2 - An agent that ends a turn with unread mail gets woken (Priority: P1)

Agent B is busy. A sends B an ask during the turn. B ends the turn and does not read the ask. The Stop hook sees unread mail that needs action and starts one wake. B starts a new turn and reads the ask.

**Why this priority**: Without this, mail that arrives during a turn waits until a person types. This is the second half of the gap.

**Independent Test**: Store an ask for a busy identity, then run the Stop hook for it. Check that the state becomes `idle` and one wake ran.

**Acceptance Scenarios**:

1. **Given** B has an unread ask that no notice announced, **When** the Stop hook of B runs, **Then** one wake runs for B.
2. **Given** B has no unread mail that needs action, **When** the Stop hook of B runs, **Then** no wake runs.
3. **Given** the Stop hook starts a wake, **When** the wake is slow or fails, **Then** the Stop hook still returns at once and prints nothing.

---

### User Story 3 - Idle costs nothing, and a lost event is still caught (Priority: P1)

A team of agents is idle for an hour with no mail. Nothing calls a model, and no process wakes more often than the sweep interval. Sometimes an event gets lost, for example when a person kills the wake process. Then the slow sweep finds the waiting mail and wakes the agent.

**Why this priority**: Constitution Principle X. The user said that a poll every 0.5 seconds is wrong. A check runs at most every 5 minutes.

**Independent Test**: Run the sweep with an injected clock. Check that it reads only the database, calls no model, and wakes an agent whose mail waited with no wake.

**Acceptance Scenarios**:

1. **Given** no mail arrives, **When** an hour passes, **Then** the number of model calls is 0. The sweep ran at most once for each interval.
2. **Given** a mail that needs action waits with no wake, **When** the sweep runs, **Then** it wakes the recipient once.
3. **Given** a notice already announced a mail, **When** the sweep runs, **Then** it does not wake the recipient again for that mail.

---

### User Story 4 - The screener starts when mail arrives (Priority: P2)

The screener no longer reads the database every 2 seconds. When the gate stores a mail that has no decision, the screener runs for that mail. A slow sweep catches a mail that it missed.

**Why this priority**: D3. The same rule as for the wake, applied to the second polling loop.

**Independent Test**: Store a mail in a session with screening on. Check that one screening pass ran for that session, with no 2 second loop.

**Acceptance Scenarios**:

1. **Given** screening is on, **When** the gate stores a mail, **Then** one screening pass runs for that session.
2. **Given** a screening pass runs for the session, **When** a second mail arrives, **Then** no second pass runs at the same time. The second mail still gets a decision.
3. **Given** the screening pass failed to start, **When** the screener sweep runs, **Then** the mail gets a decision.

---

### User Story 5 - `rookery up` sets it all up (Priority: P2)

The user runs `rookery up TEAM_FILE`. The event wake works for the new session with no other command. The user does not start a postmaster or a screener by hand.

**Why this priority**: The user said that `rookery up` sets this up for each session.

**Independent Test**: Run `rookery up --no-launch` on a scratch team file. Check what it configures for the wake and the sweep. Use no real herdr or tmux.

**Acceptance Scenarios**:

1. **Given** a team file, **When** the user runs `rookery up`, **Then** the session gets a working event wake and a sweep, as Q5 decides.
2. **Given** `rookery up` ran twice for the same session, **When** mail arrives, **Then** the agent gets one wake, not two.

### Edge Cases

- Two events fire for the same agent at almost the same time (the gate and the Stop hook, or two senders). Only one wake runs. The other drops or joins it.
- The agent starts a turn between the check "idle" and the wake. The wake must not type into a busy pane, as the postmaster rules say today.
- The wake process cannot find the pane (the pane closed). The send still succeeds. The log shows `wake-failed`, and the back-off applies.
- The database is busy when the wake runs. The wake gives up quietly. The sweep catches the mail.
- A mail goes to `legacy` or to a closed session. No wake runs.
- The sender sends to itself. No wake runs.
- A loop between two agents: each wake starts a turn that sends mail that wakes the other. The loop breaker of feature 002 still holds the mail at its limit, and a held mail starts no wake.
- Rookery releases a mail from `held` or `quarantined` later. It needs a wake at that moment (Q1 covers the screener case).
- The machine sleeps or restarts. Events during that time are lost. The sweep catches the mail after the restart.

## Requirements *(mandatory)*

**Phase requirements covered**: none. This feature is outside `requirements/rookery-0-7.md`. The source is `okf/design/mail-wake.md` and decisions D1–D4.

### Functional Requirements

- **FR-001**: The gate stores a mail with status `pending`. When the push gate says the mail needs action, Rookery MUST start a wake for the recipient. (D1)
- **FR-002**: The wake MUST start detached from the send. The send MUST return before the wake runs. A failed, slow or missing wake MUST NOT fail, delay or change the send. (D1)
- **FR-003**: When the Stop hook writes `idle`, Rookery MUST check the pending mail of that agent. If mail needs action and no notice announced it, Rookery MUST start a wake. The Stop hook MUST still never block and never print. (D1)
- **FR-004**: The wake MUST use the existing wake rules of the postmaster. These are the push gate, the record of announced mail, the idle check, `busy-stale`, the herdr probe and the back-off. There MUST be one wake code path. (D2)
- **FR-005**: At most one wake for an agent MUST run at a time. A second event for the same agent while a wake runs MUST NOT type a second notice for the same mail. (Q4 decides how.)
- **FR-006**: A wake MUST NOT start for an agent with no pending mail that needs action. It MUST NOT start for a self-send, for mail in `legacy`, or for a closed session.
- **FR-007**: The postmaster loop MUST become a slow sweep. Its default interval MUST be 5 minutes or more. It MUST read only the database and call no model. (D2, Q6)
- **FR-008**: The sweep MUST wake an agent whose mail needs action and got no wake. It MUST NOT wake an agent again for mail that a notice already announced.
- **FR-009**: When the gate stores a mail that needs screening, Rookery MUST start one screening pass for that session, detached from the send. (D3)
- **FR-010**: At most one screening pass MUST run for a session at a time. A mail that arrives during a pass MUST still get a decision.
- **FR-011**: The screener MUST NOT poll every 2 seconds by default. It MUST keep a slow sweep of 5 minutes or more as a safety net. (D3)
- **FR-012**: When a live screener check holds a mail, the wake MUST NOT run at the store. The recipient MUST get a wake when the mail becomes `pending`. (Q1 decides the event.)
- **FR-013**: `rookery up` MUST configure the event wake and the sweep for the session. The user starts nothing by hand. (Q5 decides the form.)
- **FR-014**: Every wake decision MUST write one log line with the outcome and the reason, in the existing logs. Outcomes: `pushed`, `wake-failed`, `no-idle-state`, `no-wake-binding`, and a new reason for a dropped duplicate wake.
- **FR-015**: Core code MUST NOT name a pane tool. Pane commands MUST stay in the wake config and the plugins. (Principle IX)
- **FR-016**: A path of this feature MUST NOT call a model. The number of wakes MUST grow with the mail, not with the number of agents times the number of agents. (Principle X)
- **FR-017**: A user who still starts the postmaster with `--poll 0.5` MUST keep working, as Q7 decides.
- **FR-018**: The tests MUST NOT run real herdr or tmux, call a model, or open the live database.

### Key Entities

- **Wake event**: A notice that an agent may need a wake. It comes from the gate (mail stored) or the Stop hook (turn ended). It names the session and the agent.
- **Wake**: One short run of the existing wake logic for one agent. It ends with one outcome: `pushed`, skipped with a reason, or `wake-failed`.
- **Wake lock**: The record that a wake for an agent runs now. It stops a second wake for the same agent (Q4).
- **Sweep**: A slow, model-free pass over the database that catches a lost event, for the wake and for the screener.
- **Screening event**: A notice that a session has mail with no decision. It comes from the gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An idle agent with a wake binding gets a notice within 5 seconds after another agent sends it an ask. No person types, and no loop polls.
- **SC-002**: An agent that ends a turn with an unread ask gets a notice within 5 seconds after the turn ends.
- **SC-003**: During one idle hour with no mail, the number of model calls is 0. Each background process wakes at most once for each sweep interval (5 minutes or more).
- **SC-004**: With 2, 5 and 10 agents, the number of wakes equals the number of mails that need action, not a product of agent counts.
- **SC-005**: Ten events for the same agent within 1 second give at most one notice for each new mail.
- **SC-006**: With the wake on, a send takes at most 50 ms more than the same send with the wake off.
- **SC-007**: When the event is lost, the sweep wakes the agent within one sweep interval.
- **SC-008**: A mail that a live screener check holds gets no wake until it becomes `pending`.
- **SC-009**: After `rookery up`, a send to an idle agent wakes it with no other command.
- **SC-010**: The full test suite passes with no real herdr or tmux, no model call and no access to the live database.

## Assumptions

- The wake config (`wake` in `~/.rookery/config.json`) and the bindings from features 001 and 003 stay the way to reach a pane.
- An agent that runs through hooks (Claude Code) reads its mail at a turn boundary, as today. This feature only adds the wake.
- The push gate of feature 002 stays the rule for which mail needs action.
- One wake is one agent turn. That turn is the cost of reading a real mail, so Principle X allows it.
- Remote agents on other machines are out of scope unless Q2 decides otherwise. The sweep still covers agents that the local mailroom can reach.
- A process that blocks and uses no CPU between events would also meet Principle X. D1 picked the gate and the Stop hook, because then no process runs at all.
