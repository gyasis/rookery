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

## Clarifications

### Session 2026-10-27

- Q: Which event wakes the recipient of a screened mail? → A: Every change to `pending` fires the wake: the gate when it stores the mail, the screener when it delivers it, and the release commands.
- Q: How does an agent on another machine get woken? → A: It pulls. The mailroom runs on one machine. A remote agent checks the mailroom when its turn ends and after it sends a mail. Then it checks again every 5 minutes, at most 4 times, and stops until its next turn or send. A check calls no model. Only mail that needs action starts a wake on the machine of the agent.
- Q: How does Rookery stop two wakes for the same agent? → A: A lock file for each agent, taken with a non-blocking OS lock. A second wake exits and logs `wake-busy`. The back-off record moves to a file, so it lasts between short wakes.
- Q: Who runs the slow sweep? → A: One sweep for each session. `rookery up` starts it, and it stops when the session closes. The user said a check runs at most every 5 minutes, so the interval is 300 seconds or more (Q6).
- Q: How much of session naming and resume goes into 005? → A: Local resume of the Rookery session only. `rookery up` takes `--session NAME`. A team file with no name gets a three-word name. A second run with the same name resumes the session with one sweep. Rookery does not resume the conversation of an agent: it stays harness-agnostic. Each agent starts again and gets the Rookery instructions through its own harness (a system prompt addition, hooks or native commands), and reads its state from its mail. A remote machine joining the same session goes to a later spec.

## Decisions

Locked by the user on 2026-10-27 (AskUserQuestion). The plan must not reopen them.

- **D1. The gate and the Stop hook start the wake.** The gate stores a mail that needs action for an idle recipient. Then it starts one short wake for that recipient, detached from the send. The Stop hook starts the same wake when the agent has unread mail that needs action. No process runs between mails. The design is the same on Linux and on the Mac.
- **D2. The postmaster becomes the slow sweep.** Its wake logic stays the one wake code path: the push gate, the back-off, `busy-stale` and the herdr probe. Its loop default changes from 0.5 seconds to a safety-net sweep of 5 minutes or more. The sweep reads only the database and calls no model.
- **D3. The screener uses the same event.** The event "mail stored" starts screening, so the screener stops its 2 second poll. The screener keeps a slow sweep as its own safety net.
- **D4. No `nudge_s`.** A per-agent delay before a wake is not in this feature.

## Open questions

These go to `/speckit-clarify`, research and plan. The spec does not decide them.

- **Q1.** Answered 2026-10-27: every change to `pending` fires the wake. See FR-012.
- **Q2.** Answered 2026-10-27: a remote agent pulls, with a bound. See User Story 6 and FR-019 to FR-021.
- **Q3.** Answered by research R3 (2026-10-27): the wake reads the root from the `rookery-root` binding of the session, else from `ROOKERY_ROOT`. With neither, it logs `no-root` and the sweep covers the mail.
- **Q4.** Answered 2026-10-27: a lock file for each agent with a non-blocking OS lock. See FR-005.
- **Q5.** Answered 2026-10-27: one sweep for each session, started by `rookery up`. See FR-013 and FR-022.
- **Q6.** Answered 2026-10-27 by Q5: the sweep is for each session. The default interval is 300 seconds, and Rookery refuses a smaller value outside tests.
- **Q7.** Answered by research R9 (2026-10-27): a `--poll` below 300 is raised to 300 with one notice, and the old command keeps working.

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

1. **Given** a team file, **When** the user runs `rookery up`, **Then** the session gets a working event wake and one sweep for the session.
2. **Given** `rookery up` ran twice for the same session, **When** mail arrives, **Then** the agent gets one wake, not two.

---

### User Story 6 - An agent on another machine checks the mailroom when it needs to (Priority: P2)

The mailroom runs on one machine. Agent R works on a different machine. R ends a turn and sends a mail. Then R checks the mailroom for its own mail. If mail that needs action waits, the machine of R wakes R. If no mail waits, R checks again 5 minutes later, at most 4 times. Then R stops checking until its next turn or send.

**Why this priority**: The user hosts the mailroom on one machine and runs agents on others. A remote agent must not poll all the time (Principle X).

**Independent Test**: Use a fake mailroom and an injected clock. End a turn of a remote identity. Count the checks and the wakes.

**Acceptance Scenarios**:

1. **Given** an ask waits for R in the mailroom, **When** the turn of R ends, **Then** R checks at once and gets one wake.
2. **Given** no mail waits for R, **When** the turn of R ends, **Then** R checks at once and then at most 4 more times, 5 minutes apart. Then it stops.
3. **Given** R checks again and an ask arrived meanwhile, **When** the check runs, **Then** R gets one wake, and the follow-up checks stop.
4. **Given** the mailroom does not answer, **When** R checks, **Then** the turn of R still ends normally. The log shows the failure, and the follow-up checks continue up to the limit.

---

### User Story 7 - The user starts or resumes a session by its name (Priority: P2)

The user runs `rookery up team.json`. The team file has no session name, so Rookery makes a name of three words and prints it. Later the user runs `rookery up team.json --session NAME` with that name. Rookery resumes the session: it keeps the mail, the identities and the worktrees. It starts only the agents whose pane is not alive. It does not start a second sweep. Each agent that starts again gets the Rookery instructions through its harness, as at the first start.

**Why this priority**: The sweep is for each session (Q5), so the session name is the handle that the user starts and resumes. The resume is harness-agnostic: Rookery resumes its own session, not the conversation of the agent.

**Independent Test**: Run `rookery up --no-launch` twice on a scratch team file, the second time with `--session NAME`. Check that the session, identities and sweep lock are the same, and that no second sweep started.

**Acceptance Scenarios**:

1. **Given** a team file with no session name, **When** the user runs `rookery up`, **Then** Rookery makes a three-word name and prints it.
2. **Given** an open session NAME, **When** the user runs `rookery up TEAM_FILE --session NAME`, **Then** Rookery reuses the session and starts only the agents whose pane is not alive.
3. **Given** a sweep runs for NAME, **When** the user runs `rookery up` again for NAME, **Then** no second sweep starts.
4. **Given** the team file names a session and the user passes `--session`, **When** the user runs `rookery up`, **Then** `--session` wins.
5. **Given** the session NAME has ended, **When** the user runs `rookery up --session NAME`, **Then** Rookery refuses, as today.

### Edge Cases

- Two events fire for the same agent at almost the same time (the gate and the Stop hook, or two senders). Only one wake runs. The other drops or joins it.
- The agent starts a turn between the check "idle" and the wake. The wake must not type into a busy pane, as the postmaster rules say today.
- The wake process cannot find the pane (the pane closed). The send still succeeds. The log shows `wake-failed`, and the back-off applies.
- The database is busy when the wake runs. The wake gives up quietly. The sweep catches the mail.
- A mail goes to `legacy` or to a closed session. No wake runs.
- The sender sends to itself. No wake runs.
- A loop between two agents: each wake starts a turn that sends mail that wakes the other. The loop breaker of feature 002 still holds the mail at its limit, and a held mail starts no wake.
- A person releases a mail from `held` or `quarantined` later. The release starts the wake (FR-012).
- The machine sleeps or restarts. Events during that time are lost. The sweep catches the mail after the restart.

## Requirements *(mandatory)*

**Phase requirements covered**: none. This feature is outside `requirements/rookery-0-7.md`. The source is `okf/design/mail-wake.md` and decisions D1–D4.

### Functional Requirements

- **FR-001**: The gate stores a mail with status `pending`. When the push gate says the mail needs action, Rookery MUST start a wake for the recipient. (D1)
- **FR-002**: The wake MUST start detached from the send. The send MUST return before the wake runs. A failed, slow or missing wake MUST NOT fail, delay or change the send. (D1)
- **FR-003**: When the Stop hook writes `idle`, Rookery MUST check the pending mail of that agent. If mail needs action and no notice announced it, Rookery MUST start a wake. The Stop hook MUST still never block and never print. (D1)
- **FR-004**: The wake MUST use the existing wake rules of the postmaster. These are the push gate, the record of announced mail, the idle check, `busy-stale`, the herdr probe and the back-off. There MUST be one wake code path. The back-off record MUST last between wakes, in a file next to the record of announced mail. (D2, Q4)
- **FR-005**: At most one wake for an agent MUST run at a time. A wake MUST take a lock for that agent without waiting. If another wake holds the lock, the new wake MUST exit and log `wake-busy`. Before the holder releases the lock, it MUST check the pending mail once more, so that mail that arrived during its run is not lost. The OS MUST release the lock when a wake ends or crashes. (Q4)
- **FR-006**: A wake MUST NOT start for an agent with no pending mail that needs action. It MUST NOT start for a self-send, for mail in `legacy`, or for a closed session.
- **FR-007**: The postmaster loop MUST become a slow sweep for one session. Its default interval MUST be 300 seconds. A smaller interval MUST be raised to 300 with one notice, except with an injected clock in tests. It MUST read only the database and call no model. (D2, Q5, Q6, Q7)
- **FR-008**: The sweep MUST wake an agent whose mail needs action and got no wake. It MUST NOT wake an agent again for mail that a notice already announced.
- **FR-009**: When screening is on for the session, and the gate stores a mail that needs screening, Rookery MUST start one screening pass for that session, detached from the send. Screening is off by default, so an update starts no model call that the user did not choose. (D3, research R6)
- **FR-010**: At most one screening pass MUST run for a session at a time. A mail that arrives during a pass MUST still get a decision.
- **FR-011**: The screener MUST NOT poll every 2 seconds by default. It MUST keep a slow sweep of 5 minutes or more as a safety net. (D3)
- **FR-012**: Each step that changes a mail to `pending` MUST start the wake for the recipient: the gate when it stores the mail, the screener when it delivers a held mail, and the release commands. A mail that is not `pending` MUST NOT start a wake. (Q1)
- **FR-013**: `rookery up` MUST start the sweep for the session, so that the user starts nothing by hand. When `rookery up` runs again for the same session, it MUST NOT start a second sweep. A lock for each session MUST guard this. (Q5)
- **FR-014**: Every wake decision MUST write one log line with the outcome and the reason, in the existing logs. Outcomes: `pushed`, `wake-failed`, `no-idle-state`, `no-wake-binding` and `wake-busy`.
- **FR-015**: Core code MUST NOT name a pane tool. Pane commands MUST stay in the wake config and the plugins. (Principle IX)
- **FR-016**: The wake, the sweep and the remote check MUST NOT call a model. The screening event runs only when screening is on, and the bounds of spec 004 apply to it. The number of wakes MUST grow with the mail, not with the number of agents times the number of agents. (Principle X)
- **FR-017**: A user who still starts the postmaster with `--poll 0.5` MUST keep working. The postmaster raises the interval to 300 seconds and prints one notice. (Q7)
- **FR-018**: The tests MUST NOT run real herdr or tmux, call a model, or open the live database.
- **FR-019**: An agent whose mailroom is on a different machine MUST check the mailroom for its own pending mail when its turn ends and after it sends a mail. (Q2)
- **FR-020**: After that check, the agent MUST check again every 5 minutes, at most 4 times. A new turn or send MUST restart the count. After the last check, nothing MUST run for that agent until its next turn or send. (Q2)
- **FR-021**: A remote check MUST call no model. It MUST start a wake on the machine of the agent only for mail that the push gate pushes. A failed check MUST NOT block the turn or the send. (Q2, Principle X)
- **FR-022**: The sweep of a session MUST stop when the session closes. One sweep MUST cover the wake and the screener for its session (D3). (Q5)
- **FR-023**: `rookery up` MUST accept `--session NAME`. It MUST win over the session name in the team file. (Clarification 5)
- **FR-024**: When neither the team file nor `--session` gives a name, `rookery up` MUST make a name of three words, as `rookery session new` does, and print it. (Clarification 5)
- **FR-025**: When `rookery up` runs for an open session that already exists, it MUST resume it. It MUST keep the mail, identities and worktrees, and start only the agents whose pane is not alive. (Clarification 5)
- **FR-026**: Rookery core MUST NOT resume the conversation of an agent and MUST NOT name a harness for it. An agent that starts again MUST get the Rookery instructions through the channel that its harness config names (a system prompt addition, hooks or native commands). (Clarification 5, Principle IX)

### Key Entities

- **Wake event**: A notice that an agent may need a wake. It comes from the gate (mail stored) or the Stop hook (turn ended). It names the session and the agent.
- **Wake**: One short run of the existing wake logic for one agent. It ends with one outcome: `pushed`, skipped with a reason, or `wake-failed`.
- **Wake lock**: One lock file for each agent. A wake holds it while it runs. The OS releases it when the wake ends or crashes (Q4).
- **Back-off record**: For each agent, the time before the next try after a failed wake. It lasts between wakes (Q4).
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
- **SC-011**: An idle remote agent makes at most 5 checks after one turn or send (1 at once and 4 follow-ups, 5 minutes apart). It makes 0 model calls.

## Assumptions

- A remote machine that joins the same session is out of scope. It needs a later spec: the session must then live in the mailroom, with remote identities, bindings and rules for who may join.
- The turn-end event comes from each harness: the Stop hook for Claude Code, the extension for pi. A harness with no turn-end event relies on the check after a send and on the sweep.
- The wake config (`wake` in `~/.rookery/config.json`) and the bindings from features 001 and 003 stay the way to reach a pane.
- An agent that runs through hooks (Claude Code) reads its mail at a turn boundary, as today. This feature only adds the wake.
- The push gate of feature 002 stays the rule for which mail needs action.
- One wake is one agent turn. That turn is the cost of reading a real mail, so Principle X allows it.
- The sweep wakes only agents whose pane is on the machine of the database. A remote agent uses the bounded pull (FR-019 to FR-021).
- A process that blocks and uses no CPU between events would also meet Principle X. D1 picked the gate and the Stop hook, because then no process runs at all.
