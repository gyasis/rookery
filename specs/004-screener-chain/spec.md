# Feature Specification: the screener chain and the broker gates (shadow mode first)

**Feature Branch**: `004-screener-chain`.
**Created**: 2026-10-24.
**Status**: Draft.
**Input**: "The screener chain and the broker gates, shadow mode first (phase 3, the last phase
of Rookery 0.7)." The full input is `SPEC_INPUT_screener-chain.md`. It holds the user's decisions of 2026-10-24 and
a survey of the code on `master` (dd56f00). The input file is not committed, so this spec copies what it needs.

**Built from (read, not re-derived)**: the OKF bundle `~/Documents/code/inter-agent-comms/okf/`:
`design/screener-chain.md` (main), `design/review-tiers.md`, `design/supervised-peer-asking.md`,
`design/mail-flow.md`, `design/turn-block.md`, `design/rookery-sessions.md`, `requirements/rookery-0-7.md`
(phase 3, conflicts C2 to C4), `risks/register.md` (R15 to R18), `risks/injection-and-credentials.md`,
`roles/comms-monitor.md`, `roles/worker-and-peer.md`. The decision backends are in
`~/Documents/code/codebook/decision-backends.md`, and the model dossier is
`~/Documents/code/model-dossiers/tev1.md`. Specs 001, 002 and 003. Constitution v1.2.1. The PRD is
`~/dev/prd/scratch/rookery_peer_mesh_2026-10-15.md`, section 2, "Phase 3".

**What this feature is**: phase 3 of Rookery 0.7, and the last phase. A screener looks at each mail that an agent
sends. It decides one of four results: deliver, wrong recipient, task already done, or unneeded. It starts in
shadow mode: it records each decision and blocks nothing. The user compares the decisions with their own calls.
The user switches a check on only after the comparison. The broker also gets threads (B3), a budget of asks for
each task, and duplicate-question suppression (B2).

**Why now**: specs 001 to 003 built the turn block, the one gate for every send, held and quarantined mail, and
the team workspace. Today a human screens mail by hand with `mail_screen.py`, or nobody screens it. Agents can
ask the wrong peer, ask about a task that someone has finished, or ask the same question again. When the team merges phase 3, Rookery
gets its first version tag, `v0.7.0` (PRD open item).

### Done before this feature (evidence: `master` dd56f00)

| Item | Where | State |
|---|---|---|
| T0 checks with no model: bad input, off-team, wrong recipient, the injection hook, the loop breaker | `mail_gate.py:165-207` | done (RQ-3.1 in part) |
| Malformed blocks go to quarantine | `mail_gate.quarantine_malformed`, `turn_block.py:382-415` | done (RQ-3.1 in part) |
| Release, quarantine with bounce and `suggest`, unquarantine | `rookery.py:409-457` | done (RQ-3.3 in part) |
| The manual screening command, with a label export | `mail_screen.py`, `rookery.screening_labels` | done (RQ-3.11 in part) |
| Inbox columns `verdict`, `reason`, `decided_by`, `decided_at`, `session_id` | `rookery.py:157-185` | done (RQ-3.4 in part) |
| Reason codes are canonical (C3): wrong-recipient, task-already-done, unneeded, off-team, malformed, injection-suspect, loop | `rookery.py:380-383` | done |
| Kinds (C2): agents emit ask, answer, decline, handoff, status, recruit; the system emits veto, takeover, bounce | `turn_block.py:226-227` | done |
| The block carries `thread`, `self_check` and `artifacts` | `turn_block.py:225-229` | done (the `thread` value is not stored) |
| Block size limits (4096 bytes, 1200 characters of `say`) | `turn_block.py:216-224` | done (R16 in part) |
| B1: the gate delivers only to a named identity in the sender's session | `mail_gate._find_recipient` | done (RQ-3.9 in part) |
| Loop breaker: hold, bounce, reset on a human prompt (002 FR-038) | `rookery.py:276-293`, `mail_gate.py:81-95` | done; R18 built and tested |
| The postmaster skips held and quarantined mail | `postmaster.py:471-475` | done |
| A global hold for all agent mail, `ROOKERY_HOLD=1` | `rookery.py:250-264` | done (RQ-3.7 in part) |

### Superseded items

- RQ-3.1 "loop (hops) → quarantine". The 002 loop breaker holds the mail and bounces it. It does not quarantine
  it. R18 is proven on the loop breaker, so the loop breaker stays (D2).
- `screener-chain.md` "Built so far: held/quarantine statuses — Gitea PR #14". The team merged that PR. The code is on
  GitHub `master` now, and Gitea mirrors it.

## Decisions

The user made these decisions on 2026-10-24. The user locked these decisions.

- **D1. The screener runs after the gate stores the mail.** The gate stores mail as it does today. A screener reads new
  mail, decides, and records the decision. In shadow mode it changes no status. Sending never waits for a model.
  When the model host is down, only the screening record is late. A check in live mode holds and releases mail
  through the existing release and quarantine functions.
- **D2. The 002 loop breaker stays, and it is the B2 hop limit.** 002 FR-038 supersedes RQ-3.1 "loop → quarantine".
  R18 is proven on the loop breaker. B2 adds a budget of asks for each task and duplicate-question
  suppression.
- **D3. This phase builds T0, T1 (the `tev1:4b` decision model) and the `hunch --vote 6` second opinion. T2 is
  a configured command that runs in batches.** The code names no T2 model (constitution IX style). If the user sets no T2
  command, an escalation waits with the status `escalated` for the boss or the user.
- **D4. Every new check starts in shadow mode.** These checks record what they would do and block nothing.
  The checks are the injection pattern set, thread close (B3), the ask budget, duplicate suppression, and the model decisions.
  Each check stays in shadow mode until the user switches it on in the configuration. The T0 checks that act
  today (off-team, wrong recipient, malformed, the loop breaker) do not change.

## Open questions

These questions are for `/speckit-clarify`, research and plan. The spec does not decide them.

- **Q1**: The shape of the decision record, and how the T0, T1, `hunch` and T2 records of one message link.
- **Q2**: The thread record: its key, how the `thread` value of a block maps to it, who closes a thread, and
  how `_threads/INDEX.md` changes to use it. Today `rookery_views.py:447` groups mail by topic and pair.
- **Q3**: Where the `escalated` status sits in the postmaster scan and in `_decide`, and who resolves it.
- **Q4**: Where the user sets the review mode (`pre` hold or `post` audit) for each peer (RQ-3.7).
- **Q5**: One screener for each session (RQ-3.14). Either one process filters by session, or one process runs for each
  session.
- **Q6**: The shadow comparison: how Rookery records the user's calls, how it measures agreement over 150 or more
  decisions, and how it shows the result (R15, RQ-3.6). Thresholds for each task.
- **Q7**: The label export for SIO and NanoJev (RQ-3.11): the file format only, or also a feed. Training a model
  is out of scope.
- **Q8**: The worker gates G1 to G5 from `self_check` (RQ-R.1) and the peer gates P0 and P1 (RQ-R.2). What does the
  code check in this phase? What stays in the personas?
- **Q9**: A check in live mode when the model host is down. Does the mail wait (fail closed) or go
  through (fail open)? After how long?

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The screener decides each agent mail and records it (Priority: P1)

An agent sends a mail to a peer. The mail goes through the gate, and the gate stores it, as today. The screener reads it a
short time later. The screener runs the new code checks first. Then it asks the decision model one closed question
with four answers: deliver, wrong recipient, task already done, or unneeded. It records the answer, the confidence
and who decided. In shadow mode the mail goes to the recipient as before.

**Why this priority**: this is the core of phase 3. Every later step needs recorded decisions.

**Independent Test**: with a fake decision backend, send three mails. One goes to the correct peer. One goes to a peer
outside its scope. One has `self_check.task_complete` set to true. Each mail gets one decision record. The gate
delivers all three mails, because the checks are in shadow mode.

**Acceptance Scenarios**:

1. **Given** the screener is in shadow mode and the fake backend answers "deliver", **When** an agent sends
   mail, **Then** one decision record exists. It has the label `deliver` and `decided_by` `screener:tev1:4b`.
   The mail status is the same as with no screener.
2. **Given** the fake backend answers "wrong recipient", **When** the screener decides, **Then** it asks a second
   closed question over the sender's session roster. It records the suggested peer.
3. **Given** the model host does not answer, **When** the screener runs, **Then** it records a failure with the
   cause and no label. The gate delivers the mail as before.
4. **Given** an envelope is larger than the model input limit, **When** the screener builds it, **Then** it
   records `envelope-too-large` and makes no model call. It never records `deliver` for that mail.
5. **Given** the screener stops and starts again, **When** it runs, **Then** it decides each stored mail once.
   It decides no mail twice and skips no mail.

---

### User Story 2 - The user compares the decisions and switches a check on (Priority: P1)

The user looks at the decisions of the screener. The user marks each one right or wrong, or uses the existing
release, quarantine and unquarantine commands, which also count as the user's call. Rookery shows the agreement
for each check and each task. When a check agrees well enough over 150 or more compared decisions, the user
switches it to live mode in the configuration.

**Why this priority**: constitution IV and risk R15. A model must not block mail before a shadow run against the
user's own calls.

**Independent Test**: record 150 decisions with a fake backend. Mark 140 of them right. The agreement report
shows 150 compared and 93% agreement. With 149 compared decisions, the command to switch the model check live
refuses and gives the number that is missing.

**Acceptance Scenarios**:

1. **Given** decisions exist, **When** the user marks a decision right or wrong, **Then** Rookery stores the
   call with the user's name and the time.
2. **Given** the user unquarantines a mail, **When** the report runs, **Then** that action counts as the user's
   call against the decision for that mail.
3. **Given** fewer than 150 compared decisions for a model check, **When** the user asks to switch that check to
   live mode, **Then** Rookery refuses. It says how many more compared decisions it needs.
4. **Given** the user exports the labels, **When** the export runs, **Then** each line holds the envelope
   pointers, the screener decision, the user's call and `decided_by`.

---

### User Story 3 - A thread gets one review, and a closed thread stays closed (Priority: P1)

An agent opens a thread with an ask. The screener reviews the first message of the thread. Later messages on the
same thread skip the model, except for exceptions (a decline, a repeat ask, a hop limit). When the boss, the user
or a live veto closes the thread, the gate denies every later message on it (B3). In shadow mode Rookery records "would
deny" and delivers the mail.

**Why this priority**: threads stop repeated reviews, and B3 is the gate that shuts a line between two agents.

**Independent Test**: send three mails with the same `thread` value. The first mail gets a model decision. The
second and third get a "thread approved" record and no model call. Close the thread. A fourth mail gets a "would
deny" record in shadow mode, and a refusal with a bounce in live mode.

**Acceptance Scenarios**:

1. **Given** a block with `thread` set, **When** the gate stores the mail, **Then** the mail keeps the thread
   value. The thread exists with the status `held` or `open`.
2. **Given** a closed thread and B3 in live mode, **When** an agent sends on that thread, **Then** the gate
   refuses the mail. The sender gets a bounce that names the closed thread. The gate never wakes the recipient.
3. **Given** a closed thread and B3 in shadow mode, **When** an agent sends on that thread, **Then** the gate
   delivers the mail. A "would deny" record exists.
4. **Given** threads exist, **When** Rookery writes the views again, **Then** `_threads/INDEX.md` lists each real
   thread with its status. It does not mix real threads with the old grouping by topic and pair.

---

### User Story 4 - Shaky decisions get a second opinion, and hard ones go up a tier (Priority: P2)

The decision model answers with a confidence. A high confidence acts. A confidence in the review band goes to a
second opinion, `hunch --vote 6`. If the two agree, the decision acts. If they disagree, the mail goes to T2. T2 is
a configured command that runs in batches. With no T2 command, the mail waits with the status `escalated` for the
boss or the user.

**Why this priority**: the second opinion and T2 make live mode safe. They matter only when a check is live.

**Independent Test**: with fake backends, a review-band answer and an agreeing second opinion give one record of
each and one decision. A disagreeing second opinion with no T2 command gives the status `escalated` in live mode
and an "escalate" record in shadow mode.

**Acceptance Scenarios**:

1. **Given** a T1 confidence above the accept threshold of the task, **When** the screener decides, **Then** it
   makes no second-opinion call.
2. **Given** a T1 confidence in the review band, **When** the screener decides, **Then** it calls the second
   opinion. It records both answers and the final result.
3. **Given** the user configures a T2 command, **When** escalations exist, **Then** Rookery runs the command once for
   each batch, and it records each T2 answer with `decided_by` `t2:<command name>`.
4. **Given** a mail has the status `escalated`, **When** the postmaster scans, **Then** it never delivers that
   mail and never wakes the recipient for it.
5. **Given** a mail has the status `escalated`, **When** the boss or the user releases or quarantines it,
   **Then** the status changes and `decided_by` names that person.

---

### User Story 5 - The broker stops repeat asks, asks over budget, and injection (Priority: P2)

An agent asks the same peer the same question again on the same task. An agent asks more times than the task
allows. A mail holds text that looks like a prompt injection. Each check records what it would do. In live mode
the check holds the mail and bounces it to the sender, or puts it in quarantine with the reason
`injection-suspect`.

**Why this priority**: B2 and R17. The loop breaker already covers ping-pong mail (R18), so these checks are the
next layer.

**Independent Test**: send the same ask twice inside the window. The second one gets a "duplicate" record. Send
asks past the budget. The extra ask gets a "budget" record. Send the injection fixture. It gets an
`injection-suspect` record. In live mode the injection fixture goes to quarantine, and the recipient is never
woken.

**Acceptance Scenarios**:

1. **Given** the ask budget of a task is 3, **When** the fourth ask on that task arrives, **Then** the screener
   records "over budget". In live mode it holds the mail and bounces it.
2. **Given** an earlier ask exists in the window, **When** an ask arrives with the same sender, recipient, task and
   question text, **Then** the screener records "duplicate".
3. **Given** a live check and a mail that matches the injection pattern set, **When** the gate stores the mail,
   **Then** the mail goes to quarantine. The reason is `injection-suspect`. The postmaster does not wake the
   recipient (R17).
4. **Given** two agents trade mail on one task, **When** the count reaches the loop limit,
   **Then** the loop breaker holds the next mail. This is as in 002 (R18).

---

### User Story 6 - Code applies the worker and peer gates from the flags (Priority: P3)

An agent ends its turn with a block. The block has `self_check` flags. The code reads the flags. An ask from an
agent that says its task is complete is an anomaly. An ask about a gap that the agent says is in its own scope is
an anomaly. A peer that gets a mail on a closed thread is never woken (P0).

**Why this priority**: the gates make the model's job smaller. The personas already state the scope, so this is
the last layer.

**Independent Test**: send an ask with `self_check.task_complete` set to true. The screener records a G1 anomaly.
Send an ask with `gap_in_my_scope` set to true. The screener records a G3 anomaly.

**Acceptance Scenarios**:

1. **Given** a block with `kind: ask` and `self_check.task_complete: true`, **When** the screener runs, **Then**
   it records the anomaly `g1-task-complete`.
2. **Given** a block with `kind: ask` and `self_check.gap_in_my_scope: true`, **When** the screener runs,
   **Then** it records the anomaly `g3-gap-in-scope`.
3. **Given** a peer answers with `kind: decline` and names a peer, **When** the gate stores the mail, **Then** the
   suggested peer is in the record.

---

### Edge Cases

- The model host is down for a long time. The backlog grows. The status command shows the backlog size and the
  age of the oldest mail that has no decision. Delivery in shadow mode does not change.
- The model returns a label outside the four labels, or no label. Rookery records a failure, never a label.
- The model answers, but the answer has reasoning text. Rookery records it as a failure of the thinking-off rule.
- A mail is from a system sender (postmaster, relay, rookery). The screener never screens it.
- A mail is already in quarantine because a T0 check acted. The screener does not screen it again.
- A mail has no `thread` value. The screener reviews it as a single message.
- A mail has a `thread` value that another session uses. Threads belong to one session. The two threads are
  separate.
- The roster of the sender's session has only the sender and the recipient. The wrong-recipient second question
  has no other peer. Rookery records `wrong-recipient` with no suggestion.
- The user switches a check to live mode, and then back to shadow mode. Mail that the live check held stays held
  until a person releases it. The change is in the log.
- `ROOKERY_HOLD=1` holds all agent mail. The screener records decisions for held mail too. A shadow decision
  does not release held mail.
- Two screeners run for the same session by mistake. Each mail still gets one decision record.
- A T2 command fails or times out. The mail stays `escalated`, and the failure is in the log.

## Requirements *(mandatory)*

**Phase requirements covered**: RQ-3.1, RQ-3.2, RQ-3.3, RQ-3.4, RQ-3.5, RQ-3.6, RQ-3.7. Also RQ-3.8, RQ-3.9,
RQ-3.10, RQ-3.11, RQ-3.12, RQ-3.13, RQ-3.14 (all 14). Also RQ-R.1 and RQ-R.2. Conflicts C2, C3 and C4 apply
here. Risks R15 to R18 need their proof tests here. In 002, the team built and tested R18.

### Functional Requirements

**Decisions D1 to D4**

- **FR-001**: Sending a mail MUST NOT wait for a screener or a model. The screener MUST read mail after the gate
  stores it (D1).
- **FR-002**: The 002 loop breaker MUST stay as it is: hold, bounce, and reset on a human prompt. It is the B2
  hop limit. The screener MUST NOT put mail in quarantine with the reason `loop` (D2, RQ-3.1 superseded).
- **FR-003**: The screener MUST use T0 code checks, the T1 decision model and the `hunch --vote 6` second
  opinion. T2 MUST be a configured command. The code MUST NOT name a T2 model. With no T2 command, an escalation
  MUST wait with the status `escalated` (D3, RQ-3.3).
- **FR-004**: Each new check MUST have a mode: `shadow`, `live` or `off`. The default MUST be `shadow`. The
  mode MUST come from the configuration. The T0 checks that act today MUST NOT change (D4).

**Screening (RQ-3.1 to RQ-3.5, RQ-3.14)**

- **FR-005**: The screener MUST decide each stored agent mail once. After a restart it MUST continue from the
  first mail that has no decision. It MUST NOT screen mail from a system sender, or mail that a T0 check put in
  quarantine.
- **FR-006**: The new T0 checks MUST run with no model. They are the injection pattern set, duplicate questions, the ask
  budget, a closed thread, and the worker-gate anomalies. Each check MUST write its own record (RQ-3.1).
- **FR-007**: T1 MUST ask the decision model one closed question with exactly four labels: `deliver`,
  `wrong-recipient`, `task-already-done`, `unneeded`. Thinking MUST be off (RQ-3.2).
- **FR-008**: For `wrong-recipient`, the screener MUST ask a second closed question over the roster of the
  sender's session, without the sender and the first recipient. The answer MUST fill `suggest` (RQ-3.3).
- **FR-009**: The screener MUST route each T1 answer by confidence bands for each task: accept, review, and
  reject. A review-band answer MUST go to the second opinion. Agreement acts. Disagreement escalates. Plain
  `hunch` with no vote MUST NOT stand behind an automatic accept (RQ-3.3).
- **FR-010**: Each record MUST have `decided_by` in one of these forms: `screener:tev1:4b`, `hunch:vote6:<model>`,
  `t2:<command name>`, `human:<name>`, `t0:<check>` (RQ-3.4).
- **FR-011**: The envelope MUST hold pointers, not payloads: the sender and recipient scope cards, the acceptance
  criteria of the task, the `self_check` flags and the ask. The envelope MUST fit the model input limit, with
  the source of that limit cited in the research. An envelope that is too large MUST give the record
  `envelope-too-large` and no model call. It MUST NOT pass as `deliver` (RQ-3.5, R16).
- **FR-012**: A model call that fails, times out, or gives an answer outside the labels MUST give a failure
  record with the cause. It MUST NOT give a label.
- **FR-013**: Screening MUST stay inside one session. A decision for a mail MUST use only the roster, the scope
  cards and the criteria of that mail's session (RQ-3.14).

**Shadow mode and the comparison (RQ-3.6, RQ-3.11, R15)**

- **FR-014**: A decision in shadow mode MUST NOT change the status of a mail, and MUST NOT change what the
  postmaster delivers (RQ-3.6).
- **FR-015**: The user MUST be able to mark a decision right or wrong, or give the correct label. A release,
  quarantine or unquarantine by a person MUST also count as that person's call for the mail.
- **FR-016**: Rookery MUST report the agreement for each check and each task: the number of compared decisions,
  the agreement rate and the disagreements by label. The report MUST have a JSON form and a text form.
- **FR-017**: Rookery MUST refuse to switch a model check to live mode with fewer than 150 compared decisions for
  that check. The refusal MUST give the number that is missing (constitution IV, R15).
- **FR-018**: The label export MUST write one JSON line for each compared decision: the envelope pointers, the
  screener decision, the user's call and `decided_by` (RQ-3.11).

**Review mode (RQ-3.7)**

- **FR-019**: A live check MUST support the review mode `pre` (hold the mail until the decision) as the default.
  The mode `post` (deliver, then audit) MUST be available for each peer.

**Threads and B3 (RQ-3.8, C4)**

- **FR-020**: The gate MUST store the `thread` value of a block with the mail. A new thread value MUST make a
  thread with a status: `held`, `open`, `approved` or `closed`. A thread MUST belong to one session.
- **FR-021**: After the first message of a thread gets a review, later messages on that thread MUST skip T1.
  These exceptions MUST still get a review: a decline, a repeat ask, and a hop-limit hold.
- **FR-022**: A closed thread MUST deny every later message on it (B3). In live mode the gate MUST refuse the mail
  and bounce it to the sender. In shadow mode Rookery MUST record "would deny" and deliver the mail.
- **FR-023**: `_threads/INDEX.md` MUST list the real threads with their status. Mail with no thread MUST NOT
  appear as a thread.

**B1 and B2 (RQ-3.9)**

- **FR-024**: B1 MUST stay as it is. The gate delivers only to the named recipient in the sender's session.
- **FR-025**: The ask budget of a task MUST come from the configuration or the team file. An ask over the budget
  MUST give an "over budget" record. In live mode the gate MUST hold the mail and bounce it.
- **FR-026**: An ask with the same sender, recipient, task and normalized question as an earlier ask inside a
  configured window MUST give a "duplicate" record. In live mode the gate MUST hold the mail and bounce it.

**Verdicts and escalation (RQ-3.10, RQ-3.12, C3)**

- **FR-027**: The monitor verdicts MUST map to the existing reason codes. APPROVE → release. VETO_DONE →
  quarantine `task-already-done`. VETO_WRONG_PEER → quarantine `wrong-recipient` with `suggest`. VETO_UNNEEDED →
  quarantine `unneeded`. VETO_OFF_TEAM → quarantine `off-team` (RQ-3.10).
- **FR-028**: Rookery MUST add the status `escalated` for TAKEOVER and hold-for-human. The postmaster MUST NOT
  deliver mail with that status. The boss or the user MUST be able to release it or put it in quarantine
  (RQ-3.12).
- **FR-029**: Rookery MUST show an escalation to the boss of the session and to the user. The notice MUST carry no mail body
  (002 rule: notices carry no body).

**Worker and peer gates (RQ-R.1, RQ-R.2)**

- **FR-030**: The code MUST read the `self_check` flags of a block. An ask with `task_complete: true` MUST give
  the anomaly `g1-task-complete`. An ask with `gap_in_my_scope: true` MUST give the anomaly `g3-gap-in-scope`
  (RQ-R.1).
- **FR-031**: A mail on a closed thread MUST NOT wake its recipient when B3 is live (P0). A `decline` that names
  a peer MUST keep that peer as the suggestion (P1, RQ-R.2).

**Text, records and safety (RQ-3.13)**

- **FR-032**: Every text that Rookery gives to an agent or a person in this feature MUST pass
  `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt`. The texts are bounce reasons, verdict texts, notices, the spec,
  the plan, the tasks and the docs (RQ-3.13).
- **FR-033**: Each decision MUST write one record and one gate-log line. A status command MUST show these items, as JSON and
  as text. They are the backlog size, the age of the oldest mail with no decision, whether the model host answers, and each
  check's mode.
- **FR-034**: A screener call MUST NOT send a token cap that has no cited source. The live check MUST measure
  the emitted reasoning, to prove that thinking is off.
- **FR-035**: The schema change MUST be additive. A test MUST check it on a copy of the live database.
- **FR-036**: Tests MUST NOT call a model. They MUST use a fake backend.

### Key Entities

- **Decision record**: one answer for one mail from one tier or check. It holds the mail, the tier or check, and the
  label or anomaly. It also holds the confidence, the mode (shadow or live), `decided_by`, the time, and a failure cause when
  there is no label. The link between the records of one mail is open (Q1).
- **User call**: the user's judgment of a decision: right, wrong, or the correct label. It holds who and when.
  A release, quarantine or unquarantine by a person also makes one.
- **Thread**: a conversation between agents in one session. It holds its key, its session, its status (`held`,
  `open`, `approved`, `closed`) and who closed it. Its exact shape is open (Q2).
- **Envelope**: the short input of one decision: pointers, scope cards, acceptance criteria, `self_check` and the
  ask. It is never stored as a payload copy of agent work.
- **Check mode**: `shadow`, `live` or `off` for each check, from the configuration.
- **Escalation**: a mail with the status `escalated`, waiting for the boss or the user.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In shadow mode, 0 mail statuses change because of a screener decision, over a test run of 50 mails
  of every kind.
- **SC-002**: With the model host up, each agent mail gets its T1 record (a label or a failure) within 60
  seconds after the gate stores it.
- **SC-003**: Sending a mail takes no longer with the screener on than with it off. The send path makes 0 model
  calls.
- **SC-004**: Each stored agent mail gets exactly one T1 record, also after a restart of the screener in the
  middle of a batch.
- **SC-005**: The command to switch a model check to live mode refuses at 149 compared decisions and accepts at
  150.
- **SC-006**: The injection fixture gives an `injection-suspect` record in shadow mode. In live mode it goes to
  quarantine, and the recipient is not woken (R17).
- **SC-007**: An envelope over the model input limit gives `envelope-too-large` and 0 model calls. It never gives
  `deliver` (R16).
- **SC-008**: With B3 live, 0 mails on a closed thread reach the recipient. With B3 in shadow mode, each one has
  a "would deny" record.
- **SC-009**: The postmaster delivers 0 mails with the status `escalated`.
- **SC-010**: An A↔B ping-pong fixture on one task stops at the loop limit (R18).
- **SC-011**: `spec-pane cover` finds all 16 phase IDs (RQ-3.1 to RQ-3.14, RQ-R.1, RQ-R.2) in spec.md and in
  tasks.md.
- **SC-012**: Every text named in FR-032 scores 2.0 or less on `ste-lint`.
- **SC-013**: One live check on the model host records a T1 decision for a real mail, with 0 emitted reasoning
  tokens.
- **SC-014**: The OKF risk register shows R16, R17 and R18 as proven, and R15 as built. R15 becomes proven only
  after a shadow run of 150 decisions with real mail, after this feature merges.

## Assumptions

- The decision model is `tev1:4b`. It runs on the Mac Studio through the governed lane only. Its capability is
  `decision`, not `thinking` (dossier, read from the server on 2026-10-12). The research reads this from the
  server again before the first call.
- The model input limit is about 2,050 tokens (dossier: the model build fixes Ollama `num_ctx` at 2050).
  The research finds the exact number.
- The vendor has not tested `tev1` against prompt injection (dossier). So the injection check is a T0 code check,
  not a model decision.
- PromptChain `decision_step` gives the decision call, the bands and the backends. The research reads it before
  the plan.
- Training a NanoJev head is out of scope. It needs 150 or more labels first.
- The Codex adapter is out of scope (R19).
- The screener is personal-use software on one machine, like the rest of Rookery. A local identity is
  bookkeeping, not security (002 D24).
