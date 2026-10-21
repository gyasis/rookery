# Feature Specification: `rookery init` and the rookery turn block

**Feature Branch**: `001-rookery-init-turn-block`
**Created**: 2026-10-15
**Status**: Draft
**Input**: User description: "Rookery 0.7.0 phase 1: `rookery init` and the rookery turn block. Agents send
mail by ending their message with a fenced `rookery` JSON block (never by calling a tool). A pure-code Stop
hook parses the last block and sends it through Rookery. `rookery init` upserts a managed ROOKERY section into
the harness context file and MERGES SessionStart / UserPromptSubmit / Stop hooks into .claude/settings.json."

**Built from (read, not re-derived)**: PRD `~/dev/prd/scratch/rookery_peer_mesh_2026-10-15.md` (phase 1,
decisions log). Requirements `okf/requirements/rookery-0-7.md` (RQ-1.1 to RQ-1.27, conflicts C1–C9 resolved
"option B"). Design `okf/design/turn-block.md`, `rookery-stays-in-context.md`, `harness-contract.md`,
`mail-flow.md`, `icm-rookery-workspace.md`. Risks `okf/risks/register.md` (R5, R6, R7 are this phase's proofs).
All `okf/` paths are under `~/Documents/code/inter-agent-comms/okf/`.

**Already built (PR #14, on this branch — reused, not rebuilt)**: `held` / `quarantined` statuses,
`ROOKERY_HOLD=1`, quarantine with fixed reasons + JSON bounce with `suggest`, unquarantine as a correction
label, `mail_screen.py`, 11 tests.

**Not this phase**: `rookery up`, the `ROOKERY_ROOT` folder scaffold, launch flags (phase 2, C8); screener
chain, thread table, B3 thread closing (phase 3, C4).

**Pulled forward from phase 2** (user, 2026-10-15): Rookery sessions, the agent identity registry,
bindings and identity resolution (FR-025 to FR-029, FR-032 to FR-036). Phase 2 still sets `ROOKERY_SESSION`
and `ROOKERY_NODE` at launch (RQ-2.8) and builds the full workspace (RQ-2.4).

**Not this phase, either**: a remote mailroom (`ROOKERY_URL`); see FR-035. One monitor per Rookery session
is phase 3; phase 1 only stamps the session ID.

## Clarifications

### Session 2026-10-15

- Q: How does a session get its identity when several agents share one project? → A: Rookery owns the
  identity. Each agent has a stable ID and a name. Bindings (`kind:value` pairs) tie an identity to any pane,
  session or tool, and Rookery never interprets them. A configured list of environment variables and the
  harness session ID give the bindings. Resolution order: `ROOKERY_NODE`, then a matching binding, then a new
  identity. A configured wake command per binding kind replaces built-in herdr code. This work moves from
  phase 2 into phase 1 (RQ-1.20 to RQ-1.23). The R8 proof moves into this PR.
- Q: What happens to mail addressed to a name that no identity has? → A: Code quarantines it with the
  reason `wrong-recipient`. The bounce `suggest` field lists the closest existing names.
- Q: Which command do agents use to read their mail? → A: New `rookery inbox` (read only, with an ack line),
  `rookery ack` (mark read) and `rookery peek` (read only) in the `rookery` CLI, with identity resolution as in FR-027 and `--json`. All
  notices point to them. `rk` stays as it is.
- Q: What is the unit that agents work in? → A: A Rookery session. Rookery generates its ID
  (constitution IX). It is one team, with one monitor (phase 3) and one boundary. An identity belongs to one
  session. Each new session gives each agent a new ID. Names are unique inside a session. Bindings resolve
  only inside the current session.
- Q: Can an agent mail an agent in another Rookery session? → A: No. Code quarantines the mail with the
  reason `off-team` and sends a bounce.
- Q: Can a new session continue the work of an old one? → A: Only when the user asks for it:
  `rookery session new --from <session>` copies the newest `HANDOFF.md` files. It never copies mail.
- Q: Does phase 1 support a remote mailroom (`ROOKERY_URL`)? → A: No. Phase 1 is local only. With
  `ROOKERY_URL` set, the hooks send nothing, keep each block in `outbox/failed/` with the reason
  `remote-unsupported`, and log it. Remote support is a later phase.
- Q: What about panes that are open before init, and sessions that are not agents? → A: Rookery is opt-in
  per Rookery session. Without a current Rookery session, every hook does nothing. Panes that are open
  before init never join: the user starts a new Rookery session with new panes and agents.
- Q: Must an agent read its mail before it ends a turn? → A: No. Rookery is mail, not a phone call. A
  phone call interrupts, and mail waits until the reader chooses to read it. Rookery never blocks a stop and
  never pushes into a running turn. The stop gate is removed (FR-017, RQ-1.14). The postmaster pushes mail
  only to an idle agent (FR-018). An identity with no wake binding gets no push in phase 1. Its mail waits
  for the prompt notice or `rookery inbox`.

### Session 2026-10-16

- Q: Should `rookery inbox` mark mail read? → A: No. `rookery inbox` shows mail and changes nothing.
  `rookery ack ID…` marks it read. A truncated pipe lost mail (FR-031).
- Q: May an agent create or join an identity in an ended session? → A: No. Every create and join path
  refuses an ended session. The CLI (`rookery identity new | bind | link`) and `rookery turn` print a message
  on stderr and exit 3. The hooks print nothing, log `session-ended` and exit 0. `resolve()` raises an
  error. Reading mail stays allowed (FR-032).
- Q: May the user end the `legacy` session? → A: No. `rookery session end legacy` refuses with a message
  and exit 2. The `legacy` session holds the mail from before sessions (FR-032, FR-034).
- Q: May a hook write anything when no session matches? → A: At most one log line. The hook prints nothing.
  It writes one `no-session` or `unknown-session` log line and no other file. With no Rookery environment,
  it writes nothing (FR-015, SC-010).
- Q: May an agent send mail to itself? → A: Yes. Rookery does not block it. The mail follows the normal
  send path.

### Session 2026-10-20

- Q: Should rookery init grant agents access to mail and herdr pane control? → A: Yes. init merges a
  permissions.allow list for the rookery mail commands and herdr pane control into .claude/settings.json.
  The list only pre-approves; it blocks nothing (FR-013).
- Q: Should the list let agents type into panes (`herdr pane send-keys`, `send-text`)? → A: No. An agent
  that mail tricks must not type into a shell pane. Agents wake agents with `herdr agent prompt` (FR-013).
- Q: When does a block go to the outbox? → A: Only after a failed send. A turn first sends the mail
  directly. If the process dies between the parse and the send, the block is lost (FR-005).
- Q: May carry-over write into a successor's folder, against "one writer per folder"? → A: Yes, once.
  Carry-over writes `HANDOFF.md` into the successor identity's folder at the link. This is an exception
  to constitution principle VII (constitution 1.2.1).
- Q: What is `settings` in `rookery init --json` for a harness with no hook settings file? → A: `null`.
  Codex, pi and omp have no such file. Their agents get mail through `rookery turn` or the postmaster
  push.
- Q: What stops agents from mailing each other in a loop? → A: Two code rules. The postmaster pushes only
  mail that needs action (ask, handoff, recruit, decline); status and answer mail waits for the next turn.
  More than 10 mails on one task between agents with no human prompt in between are held with reason loop
  (FR-038, FR-039).

### Session 2026-10-21

- Q: What happens to an agent left `busy` by a killed turn? → A: Busy older than busy_stale_s (30 min) is stale; a session start (startup or clear) resets it; an optional herdr probe may correct it sooner. Rookery does not depend on herdr (FR-040).

## User Scenarios & Testing *(mandatory)*

Actors: **the user** (one person running the user's own agents in herdr/tmux panes) and **Rookery** (mailroom +
postmaster). **An agent** is a Claude Code, Codex, pi or omp session. **A peer agent** is the recipient.

### User Story 1 - An agent sends mail by writing a block, and only then (Priority: P1)

An agent finishing a turn wants a peer to act. It ends its final message with a fenced `rookery` block naming
the recipient, task, kind and a short message with file pointers. When the turn stops, Rookery sends exactly
that mail. When the agent is simply talking with the user and writes no block, Rookery sends nothing.

**Why this priority**: This is the whole contract of the release. Without it agents cannot ask peers and the
user remains the "phone book". Getting the no-block case wrong (R6) would turn every chat into mail.

**Independent Test**: Feed fixture transcripts to the stop-time parser. No block → 0 mail. One valid block →
1 mail with matching fields. Two blocks → Rookery sends only the last. Malformed → 1 quarantined row with reason
`malformed` and a bounce to the sender (R6).

**Acceptance Scenarios**:

1. **Given** a turn whose final message has no `rookery` block, **When** the turn stops, **Then** Rookery
   creates no mail and no mail record changes.
2. **Given** a turn ending with a valid block `{"to":"law-counsel","task":"TASK-12","kind":"ask",…,"status":"in-progress"}`.
   **When** the turn stops, **Then** Rookery submits one mail from this identity to `law-counsel` about `TASK-12`
   through its normal send path (so `ROOKERY_HOLD` applies).
3. **Given** a final message containing two blocks, **When** the turn stops, **Then** Rookery acts only on the last block.
4. **Given** a block that is not valid JSON, misses a mandatory field, or uses a system-only kind
   (`veto`, `takeover`, `bounce`). **When** the turn stops, **Then** Rookery records a mail row as `quarantined`
   with reason `malformed`. The sender receives a bounce saying what was wrong. Rookery drops nothing.

---

### User Story 2 - Hand-offs happen only when the work is done (Priority: P1)

An agent working a long task ends many turns while background work runs. Each turn it writes a block with
`status: in-progress`; Rookery keeps the task's hand-off summary current but does not hand the task to anyone.
When the agent finishes and writes `status: done`, the hand-off mail goes out and the summary says `done`.

**Why this priority**: A premature "done" hands a half-finished task to a peer (R7). The hand-off summary is
also what lets any agent pick up the task after a restart or compaction.

**Independent Test**: Fixture turns: `in-progress` → `HANDOFF.md` rewritten, 0 hand-off mail; `done` →
`HANDOFF.md` rewritten with `status: done` and 1 hand-off mail (R7). `rg -l TASK-12` over the agent's
workspace lists the hand-off file.

**Acceptance Scenarios**:

1. **Given** a block with `kind: handoff`, `status: in-progress`, **When** the turn stops, **Then** the task's
   `HANDOFF.md` is rewritten from the block and Rookery sends no hand-off mail.
2. **Given** a block with `kind: handoff`, `status: done`, **When** the turn stops, **Then** Rookery sends the hand-off mail
   and `HANDOFF.md` frontmatter reads `status: done`.
3. **Given** any block for `TASK-12`, **When** Rookery writes the summary, **Then** it carries frontmatter
   `type: Handoff`, `task: TASK-12`, `agent`, `updated`, `status`. It also carries the sections *Last thing I did*,
   *State now*, *What is left*, *Open threads*.

---

### User Story 3 - The user installs Rookery into a project without losing anything (Priority: P1)

The user runs `rookery init` in a project that already has a `CLAUDE.md` and a `.claude/settings.json` full of
dev-kid and personal hooks. Afterwards the context file has a managed ROOKERY section and the settings file
has Rookery's three hooks added beside every existing hook. Running it again changes nothing.

**Why this priority**: Without init, nothing else connects to the project. An init that clobbers dev-kid's or the user's hooks
breaks the user's existing tooling (constitution VII).

**Independent Test**: Run init against a copy of this repo's dev-kid `.claude/settings.json` and a `CLAUDE.md`
with surrounding text. Every pre-existing hook and key stays present and unchanged. Rookery's hooks appear
once. Text outside the markers stays byte-identical. A second run produces byte-identical files.

**Acceptance Scenarios**:

1. **Given** a `CLAUDE.md` with user text and no ROOKERY markers, **When** init runs, **Then** init
   adds a `<!-- ROOKERY START -->` … `<!-- ROOKERY END -->` section and leaves all other text unchanged.
2. **Given** a `CLAUDE.md` with an old ROOKERY section, **When** init runs, **Then** init replaces only the text between the
   markers.
3. **Given** a settings file with dev-kid hooks on `UserPromptSubmit`, `Stop` and other events. **When** init
   runs, **Then** init adds Rookery's `SessionStart`, `UserPromptSubmit` and `Stop` entries and preserves every existing
   entry in place.
4. **Given** init has already run, **When** it runs again, **Then** no duplicate hook entries or sections exist.
   Both files are byte-identical to the first run's output.
5. **Given** a settings file that is not valid JSON, **When** init runs, **Then** it changes nothing and exits
   non-zero. It says which file is invalid and where.

---

### User Story 4 - Agents notice their mail without having to remember (Priority: P2)

A peer's answer arrives while an agent is busy or chatting with the user. On the agent's next prompt, Rookery tells it
"N unread from X about TASK-…". The agent reads the mail when it chooses. When its turn ends and the agent is idle,
the postmaster pushes a notice as its next prompt. The notice points to `rookery inbox`. Rookery never interrupts a running turn and never blocks a stop:
this is mail, not a phone call. When there is no mail, Rookery
injects nothing at all. When an agent session restarts, Rookery re-registers it and gives it its newest hand-off summary and
unread mail.

**Why this priority**: Instructions decay with long sessions and compaction (R5). Layers 1, 3 and 5 of
"Rookery stays in context" make mail visible without relying on the model's memory.

**Independent Test**: With 0 unread → prompt hook emits 0 bytes. With 2 unread from `researcher` about
`TASK-12` → prompt hook emits one line naming count, sender and task, and the stop is not blocked. The
postmaster pushes a notice only after the Stop hook marks the agent idle. Session start for an identity with a `HANDOFF.md` → the identity appears in the roster and the
session-start hook injects the newest summary + unread list.

**Acceptance Scenarios**:

1. **Given** no unread mail and no new failed block, **When** the user submits a prompt, **Then** the hook adds
   nothing to the context.
2. **Given** unread mail, **When** the user submits a prompt, **Then** the context gets one short notice with
   count, sender(s) and task ID(s). The mail bodies are not pasted.
3. **Given** unread mail, **When** the agent ends its turn, **Then** Rookery does not block the stop. The
   mail waits.
4. **Given** delivered mail for an idle agent. **When** the postmaster runs, **Then** it pushes a notice as
   the agent's next prompt (into its live pane, through the wake command of its binding). The notice
   points to `rookery inbox` and holds no mail body.
5. **Given** a relaunched session for the identity `researcher`, **When** it starts, **Then** the identity `researcher` is in
   `rookery identity list` and the newest `HANDOFF.md` plus the unread-mail summary are in its context.
6. **Given** new mail for an agent whose turn runs, **When** the postmaster runs, **Then** it pushes nothing.
   It pushes the notice after the Stop hook marks the agent idle.

---

### User Story 5 - Hookless harnesses and mid-task mail use the same contract (Priority: P3)

A Codex, pi or omp agent has no Stop hook. Its wrapper reads the same block from the turn output and sends it
the same way. An agent that must ask a peer before its turn ends writes the same JSON as a file in its
`outbox/`, and a watcher sends it.

**Why this priority**: The constitution's "any harness" rule (III). Lower priority because the Claude Code path
covers the user's main panes, and the live Codex adapter depends on R19.

**Independent Test**: Fixture headless-turn outputs through the wrapper produce the same results as the Stop
hook fixtures (no block / valid / malformed). Dropping a valid JSON file in `outbox/` sends one mail once. Rookery quarantines a
malformed file with reason `malformed`. Restarting the watcher does not resend.

**Acceptance Scenarios**:

1. **Given** a headless turn output ending in a valid block. **When** the wrapper processes it, **Then** the
   wrapper sends one mail exactly as the Stop hook would send it.
2. **Given** a valid file written to `outbox/`, **When** the watcher sees it, **Then** the watcher sends one mail and
   marks the file processed (kept, not deleted).
3. **Given** the watcher restarts, **When** it scans `outbox/`, **Then** already-processed files are not resent.

---

### Edge Cases

- A `rookery` block quoted in the *middle* of the final message as an example. A real block follows at the
  end → Rookery uses only the last block. A message whose only block is an example → Rookery sends it. The managed
  section tells agents to put example blocks in a different fence language.
- Block present but `task` contains whitespace, `/`, `..` or other path characters → `malformed` (the task ID
  becomes a folder name; no path escape).
- `read` entry that holds pasted content (multi-line, or not a path), or a block larger than the allowed size →
  `malformed`. The bounce states the limit. Silent truncation never happens (R16 groundwork).
- `read` path outside the shared root → `malformed`.
- Recipient not in the roster → quarantined as `wrong-recipient` by code, with a bounce that suggests the
  closest names (FR-030). A name that exists but is the wrong agent is the phase-3 screener's job.
- `thread` present → carried unchanged into the mail record; no thread table exists yet (C4).
- The send path is unreachable at stop time → Rookery writes the block to the identity's outbox for the watcher to retry.
  Rookery logs the error and does not crash the agent's session. The watcher tries once. If that send also
  fails, Rookery keeps the file as failed and tells the agent in its next notice (FR-037).
- The database is locked at stop time → the Stop hook still finds the identity, because it only reads.
  It puts the block in the outbox. A Stop with a session but no identity keeps the block in
  `<root>/unresolved/` and logs `no-identity`.
- Mail arrives while the agent's turn runs → nothing interrupts the turn. The postmaster pushes the notice
  after the turn ends. An interrupted turn has no Stop event, so the agent stays busy. Until its next turn
  ends, the prompt notice is the only signal.
- Two `rookery init` runs at once on the same project → the files end valid and complete (no half-written file).
- An agent mails itself (`to` is its own name) → Rookery sends the mail like any other. It lands in the
  agent's own inbox. Rookery does not block it.
- An agent tries to join or create an identity in an ended session → Rookery creates nothing. The CLI
  and `rookery turn` exit 3. A hook prints nothing, logs `session-ended` and exits 0 (FR-032). The user ends the `legacy` session → Rookery refuses with exit 2 (FR-032).
- Context file absent → init creates it containing only the managed section.
- Harness is Codex / pi / omp → init writes the section into `AGENTS.md`. Init touches no Claude settings.
- Two agent sessions in one project and one Rookery session → each presents its own bindings, so each
  resolves to its own identity.
- Same pane, new Rookery session → Rookery searches for the pane binding only in the new session. It matches
  nothing there, so the agent gets a new identity with no old mail, idle state or `HANDOFF.md`.
- Inside one Rookery session, a session presents a binding that another identity holds → the binding moves
  to the identity that `ROOKERY_NODE` names. Without `ROOKERY_NODE`, the session joins the identity that
  holds the binding (a resume).
- `ROOKERY_NODE` holds an ID that does not exist in the current session → Rookery logs the error and
  resolves by bindings.
- A Claude Code session in an initialized project with no current Rookery session → every hook emits
  nothing and changes nothing. The user's own sessions stay as they were before init.
- A pane that was open before `rookery init` → it never joins. Init says so in its output.
- `to` names an agent that exists only in another Rookery session → quarantined as `off-team` (FR-033).

## Requirements *(mandatory)*

**Phase requirements covered**: RQ-1.1, RQ-1.2, RQ-1.3, RQ-1.4, RQ-1.5, RQ-1.6, RQ-1.7, RQ-1.8, RQ-1.9.
Also RQ-1.10, RQ-1.11, RQ-1.12, RQ-1.13, RQ-1.14, RQ-1.15, RQ-1.16, RQ-1.17, RQ-1.18.
Also RQ-1.19, RQ-1.20, RQ-1.21, RQ-1.22, RQ-1.23, RQ-1.24, RQ-1.25, RQ-1.26, RQ-1.27 (all 27).
Conflicts C1, C2, C4, C6 and C7 apply here. Conflicts C3, C5, C8 and C9 belong to later phases. This spec respects them by not contradicting them.

### Functional Requirements

**The block (contract)**

- **FR-001** (RQ-1.1): An agent MUST be able to send mail solely by ending its final message with a fenced
  block whose fence language is `rookery`. Rookery needs no send tool call from the agent and expects none.
- **FR-002** (RQ-1.2, C1): The block MUST be ONE JSON object with mandatory fields `to`, `task`, `kind`, `say`,
  `status` and optional fields `thread`, `read` (list), `self_check` (object: `task_complete`, `gap`,
  `gap_in_my_scope`, `evidence`) and `artifacts` (list of `{path, note}`). The retired `call` object is NOT
  accepted.
- **FR-003** (RQ-1.2, C2): `kind` MUST be one of `ask, answer, decline, handoff, status, recruit`; `veto`,
  `takeover` and `bounce` are system-only and an agent block using them is `malformed`. `status` MUST be one
  of `done`, `in-progress`.
- **FR-004** (RQ-1.3): Every `read` entry MUST be a single-line path inside the shared Rookery root, never
  pasted content. Rookery MUST reject a block or `read` entry over the allowed size as `malformed`. It MUST state the limit
  in the bounce and never silently truncate or pass the block or the entry.

**Sending at stop time**

- **FR-005** (RQ-1.4): At the end of each turn, a pure-code step (no model) MUST parse the LAST `rookery`
  block of the turn's final message. No block → Rookery sends nothing and creates or changes no mail record.
  The step sends a valid block directly. It writes the block to the identity's `outbox/` only after a send
  fails. Known window: if the process dies between the parse and the send, Rookery loses the block.
- **FR-006** (RQ-1.5): Rookery MUST record a malformed block as a mail row with status `quarantined`, reason
  `malformed` and `decided_by` naming the parser. Rookery MUST send the sender a JSON bounce saying what was wrong.
  Rookery MUST NOT drop the block.
- **FR-007** (RQ-1.6): A block with `status: done` MUST send its mail (a `handoff` reaches the recipient as
  the hand-off). A block with `status: in-progress` MUST update the task's `HANDOFF.md` and MUST NOT send a
  `handoff` mail. Rookery MUST send every other kind (`ask`, `answer`, `status`, `decline`, `recruit`) regardless
  of `status`, so an agent can ask a peer while its own task is still open. (Clarified 2026-10-15, user,
  option A: Rookery suppresses only the hand-off. Register R7 already said this. `turn-block.md` ("sends
  nothing") and RQ-1.6 ("no mail") said all mail. Both changed to match.)
- **FR-030** (RQ-1.20, C3): If no Rookery session has an identity named `to`, Rookery MUST record the mail
  as quarantined with the reason `wrong-recipient`. Its `decided_by` MUST name the parser. The bounce
  `suggest` field MUST list up to three names in the sender's session that are closest to `to`. A pure-code lookup makes this decision, so no
  model runs.
- **FR-031** (RQ-1.13, RQ-1.14, RQ-1.22): The `rookery` CLI MUST have three commands. `rookery inbox` shows
  the unread mail of the resolved identity in the current Rookery session and changes no mail row. It ends with the line
  `Mark read: rookery ack ID ...`. `rookery ack ID ...` marks read only the named mail that is unread mail of the resolved identity.
  It skips all other IDs, reports them and exits 1. `rookery peek`
  shows the same mail and changes nothing. All three MUST resolve the identity as in FR-027. `inbox`, `peek` and `ack` MUST support `--json`. The managed section, the
  prompt notice and the pane notice MUST name `rookery inbox` as the way to read mail.
  No command MAY show the mail of any identity other than the resolved one: there is no `--as` flag and no
  `--all` flag. Each read MUST log the identity and the bindings that Rookery used to resolve it. This phase MUST NOT
  change `rk`.
- **Security note (phase 1 scope)**: FR-031 scopes reads; it does not enforce them. All agents run as one
  Unix user, so an agent can set `ROOKERY_NODE` to another name or open the database file directly. Phase 1
  makes such a read visible in the log, not impossible. Enforcement needs a per-identity secret checked by a
  broker API and no direct file access for agents (RQ-2.9).
- **FR-008** (C4): Rookery MUST carry a `thread` value unchanged into the mail record. This phase introduces no thread table or
  thread-closing rule.

**Hand-off summary and task-ID findability**

- **FR-009** (RQ-1.7, C6): Every turn that ends with a valid block MUST rewrite
  `runs/<TASK-ID>/HANDOFF.md` in the identity's workspace from that block. The file MUST have frontmatter `type: Handoff`,
  `task`, `agent`, `updated`, `status` and sections *Last thing I did*, *State now (what exists, with paths)*,
  *What is left*, *Open threads*.
- **FR-010** (RQ-1.8): Every file Rookery writes for a task MUST carry the task ID in its path or file name and
  a `task:` frontmatter line (Markdown) or a `task` key (JSON). A plain-text search for the task ID therefore lists it. The managed section MUST tell
  agents to do the same for files they write. A task ID that is not safe as a single folder name is `malformed`.

**Mid-task mail**

- **FR-011** (RQ-1.9): A watcher MUST send each JSON file placed in the identity's `outbox/` (same shape as the
  block) exactly once. The watcher MUST use the same validation and give the same outcomes as FR-002–FR-007. Processed and failed files MUST
  be kept and marked, never deleted, and never resent after a watcher restart.

**`rookery init`**

- **FR-012** (RQ-1.10): `rookery init` MUST upsert a section between `<!-- ROOKERY START -->` and
  `<!-- ROOKERY END -->` into the harness context file (Claude Code → `CLAUDE.md`; Codex, pi, omp →
  `AGENTS.md`), creating the file if absent. The section MUST state how to emit the block, how an agent learns its
  identity (FR-027), and the shared Rookery root path. It MUST also state that its rules apply only when
  Rookery gives the agent an identity at session start. Re-running MUST replace only the text between the markers.
- **FR-013** (RQ-1.11): For Claude Code, init MUST merge Rookery's `SessionStart`, `UserPromptSubmit` and
  `Stop` hooks into `.claude/settings.json`. It MUST NOT remove, reorder or alter any existing hook or key
  (dev-kid's or the user's). Re-running MUST NOT duplicate entries. If init cannot parse the existing file,
  it MUST change nothing and exit non-zero naming the file and the parse error.
  Init MUST also merge a `permissions.allow` list into the same file. The list holds these rules:
  `Bash(rookery inbox:*)`, `Bash(rookery peek:*)`, `Bash(rookery ack:*)`, `Bash(rookery identity show:*)`,
  `Bash(rookery turn:*)`, `Bash(herdr pane read:*)`, `Bash(herdr agent list:*)`, `Bash(herdr agent prompt:*)`,
  `Bash(herdr agent start:*)` and `Bash(herdr agent wait:*)`. The list only pre-approves these commands. It
  blocks nothing else. The list MUST NOT hold `herdr pane send-keys` or `herdr pane send-text`: an agent that
  mail tricks must not type into a shell pane. Init MUST keep every existing `permissions` key and every existing `allow` entry in
  its order, and add only the missing rules. It MUST NOT change `deny`, `ask` or `defaultMode`. If a rule is
  in `deny` or `ask`, init MUST NOT add it to `allow` and MUST report it as skipped. If `permissions` is not
  an object, or `allow`, `deny` or `ask` is not a list, init MUST change nothing and exit non-zero.
- **FR-014**: init MUST report what it did (files created / section inserted / section replaced / hooks added /
  already present) in a machine-readable form and a human line. The human line MUST say that open agent
  sessions do not join. It MUST also tell the user to start a new Rookery session with new panes and agents.

**Staying in context**

- **FR-015** (RQ-1.12): The session-start hook MUST resolve and register the identity (FR-027). It MUST also inject the
  identity's newest `HANDOFF.md` and a summary of its unread mail when either exists. With no matching
  session, the hook prints nothing and writes at most one log line (`no-session` or `unknown-session`),
  and no other file. With no Rookery environment, it writes nothing (SC-010).
- **FR-016** (RQ-1.13): The prompt-submit hook MUST inject one short notice ("N unread from X about TASK-…")
  ONLY when unread mail or a new failed block (FR-037) exists. With neither it MUST emit zero bytes. It MUST NOT paste mail bodies and
  MUST NOT mark mail read.
- **FR-017** (RQ-1.14): The Stop hook MUST NOT block a stop for any reason: not for unread mail and not for
  a missing block. Unread mail waits until the agent reads it or goes idle (FR-018). The Stop hook records
  that the agent is idle. (Changed 2026-10-15, user: Rookery is mail, not a phone call, so the stop gate is
  removed. This supersedes C7.)
- **FR-018** (RQ-1.15): The postmaster MUST push a notice of released mail as the recipient's next prompt, ONLY when
  the recipient is idle: its last turn ended and no new turn started. It MUST never push into a running turn.
  The notice points to `rookery inbox` and never holds a mail body.
  It MUST use the wake command of a binding (FR-028). An identity without such a binding or without idle
  state gets no push in phase 1. Rookery logs `no-wake-binding` or `no-idle-state`, and the mail waits for
  the prompt notice or `rookery inbox`.
- **FR-040**: A killed turn never writes `idle`, so `busy` can stay forever. The postmaster MUST treat a
  `busy` state as stale, and so as idle for the push, when the last sign of life is older than
  `busy_stale_s` (configuration, default 1800 seconds). It MUST log `busy-stale` once for each agent and
  `since`. The last sign of life is the later of `since` and the modification time of the file named by
  `transcript` in `state/turn.json`. The prompt hook stores `transcript` from the `transcript_path` field
  of its input. A long turn that still writes its transcript is therefore not stale. An unreadable
  transcript is ignored. A `since` that is not a number, is a boolean or NaN, or is more than 300 seconds
  in the future is not trusted: the modification time of `turn.json` takes its place. The session-start
  hook MUST write `idle` when its `source` is `startup`, `clear` or missing, and MUST log `state-reset`
  (reason `session-start`) when the state was `busy`. It MUST keep a `busy` state for `compact` and
  `resume`, which can fire inside a turn. An optional probe MAY correct `busy` sooner: only when the
  identity has a `herdr-pane` binding (the first one only), the postmaster runs inside a herdr pane (`HERDR_ENV=1`) or with `ROOKERY_HERDR=force`, and the configuration
  `herdr_probe` is true (default), the postmaster asks for the status of the pane (2 seconds at most, one
  listing every 5 seconds for all identities). The turn must look busy for 60 seconds or more, and the
  status `idle` or `done` must show on two probes at least 10 seconds apart in the same turn, with no other
  status between them. Then the push goes and Rookery logs `busy-corrected` (source `herdr`). Any other answer, a
  failure or a timeout leaves the stale rule in force. The probe attaches through the plugin registry.
  Rookery does not depend on herdr, and an install without herdr makes no herdr call. A missing
  `state/turn.json` is still `no-idle-state`.
- **FR-037** (RQ-1.9, RQ-1.13): Rookery MUST tell the sender about each of its blocks that ends as failed
  (`send-failed`, `remote-unsupported` or `interrupted`). The next session-start or prompt notice names the
  task, the reason and the file path, once for each block. The agent can then fix the cause and send the
  block again.
- **FR-038**: The postmaster MUST push a notice only for mail that needs action. A block of kind `ask`,
  `handoff`, `recruit` or `decline` needs action. A bounce from `rookery` also needs action, because it
  tells the sender that its block failed. Mail of kind `status` MUST wait for the recipient's next turn.
  Mail of kind `answer` MUST also wait, unless it answers an open `ask`. An ask is open when
  the recipient sent it to the answerer on the same task. It stays open until that agent sends one
  answer. Waiting mail stays `pending` and the next prompt notice counts it, but it never starts
  a turn. Rookery MUST never push mail that an identity sent to itself. Old mail with a plain-text body
  or no `kind` still pushes. A mail that Rookery does not push MUST NOT stop a later mail from pushing.
- **FR-039**: Rookery MUST stop an agent-to-agent loop on the block path (the Stop hook, `rookery turn`
  and the outbox scan). The send paths that work from nodes (`rookery_mcp`, `send_mail`,
  `mailroom_server`) do not count. Rookery keeps three counters since the last human prompt. The first
  counts mails on one task between two identities, or one identity and itself, in both directions. The
  second counts all mails between the same two identities on any task, so a new task name does not
  escape the first. The third counts bounces to one agent. When a counter reaches its limit, Rookery
  stores the next mail (or bounce) with status `held` and reason `loop`. The limits are the keys
  `loop_limit` (default 10) and `loop_pair_limit` (default 30, three times `loop_limit`) in
  `rookery_config.default.json`. The bounce counter uses `loop_limit`. The check runs at send time in
  the shared send path. Rookery logs `loop-held`. In the Stop hook and `rookery turn`, Rookery keeps the
  block in `outbox/failed/`, so the next prompt notice names it as failed (`loop-held`). Rookery sends no
  bounce mail for it, because that mail would feed the loop. Every later mail past a limit also gets
  status `held`. A human prompt resets the counters of the identity that got the prompt. A prompt that
  starts with `[rookery] new mail for` is a postmaster push, not a human prompt, and resets nothing.
  Leading white space and a bracketed-paste wrapper do not hide the prefix.
- **FR-019** (RQ-1.18): The contract MUST survive context compaction, and a long-session live run (R5) MUST prove it. The contract lives in the context file the harness
  re-reads, reinforced by FR-015–FR-017.

**Any harness, one send path**

- **FR-020** (RQ-1.16): For hookless harnesses (Codex, pi, omp) a wrapper MUST parse the same block from a
  headless turn's output with the same parser. It MUST produce identical outcomes to FR-005–FR-009. A live Codex
  adapter MUST remain disabled until R19 verification passes.
- **FR-021** (RQ-1.17): Every agent mail from the Stop hook, the wrapper and the outbox watcher MUST go through
  Rookery's normal send path. This makes `ROOKERY_HOLD` and broker rules apply to every harness. Hooks are only an
  extra early check, never the sole path.

**Constitution-driven**

- **FR-022** (constitution IV, VI): Hooks, wrapper and watcher MUST use no model. Rookery MUST log every outcome (the full list is in
  `contracts/cli-and-hooks.md`) with the identity, the task and the reason in a
  machine-readable form. A hook failure MUST NOT crash or hang the agent's session and MUST NOT lose a block.
- **FR-023** (constitution I, VII): Mail state lives only in the Rookery database; `HANDOFF.md`, `runs/` and
  `outbox/` live under the Rookery root, not in the host repo. The host repo gains only the managed section and
  the merged hook entries.

- **FR-024** (RQ-1.19, constitution VIII): This phase's spec, plan, tasks, docs and CHANGELOG MUST pass
  `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt` before merge. The text
  that Rookery gives to agents (the managed section, bounces, hook notices, error messages) MUST also pass it before merge.

**Rookery sessions and agent identity (pulled forward from phase 2; constitution IX)**

- **FR-032** (RQ-1.24): A Rookery session is the unit that agents work in. Rookery MUST generate each
  session ID and never reuse it. `rookery session new [--name N] | list | show | end` MUST manage sessions,
  with `--json` output. Without `--name`, the name is the session ID. Rookery never reuses a session
  name, also after the session ends. A session MAY have bindings, as an identity does (FR-026). An ended session stays in
  the database with its identities and mail. Rookery MUST refuse every path that creates or joins an
  identity in an ended session (the hooks, `rookery turn`, `resolve()`, `rookery identity new | bind |
  link`). The refusal prints a message on stderr and exits 3 in the CLI, the same code as `rookery turn`
  for a session that has ended. Reading mail (`inbox`, `peek`, `ack`) of an identity in an ended session
  stays allowed. `rookery session end legacy` MUST refuse with a message and exit 2: the `legacy` session
  holds the mail from before sessions.
  Rookery sends no block from an ended session. The block fails with reason `session-ended`. The Stop hook keeps the
  block under the identity's `outbox/failed/`, logs `session-ended` and exits 0, as the outbox scan does.
- **FR-025** (RQ-1.20, RQ-1.25, R8): Each agent in a Rookery session MUST have an identity with a stable ID
  and a name. Rookery generates the ID and never reuses it, so each new session gives each agent a new ID.
  A name MUST be unique inside its session. The same name MAY exist in other sessions as a different
  identity. The roster is the source of truth. Mail addresses an identity by its name inside the sender's
  session.
- **FR-026** (RQ-1.21): An identity MAY have zero or more bindings. A binding is a `kind:value` pair, for
  example `herdr-pane:w1:p2Q`, `tmux-pane:%3` or `claude-session:<uuid>`. Rookery stores and matches
  bindings, but it MUST NOT interpret them. A configured list of environment variables gives the bindings of
  a session, and the harness session ID from the hook input is one more binding. A new tool therefore needs
  a configuration entry, not a code change. Inside one session, a binding value belongs to one identity at
  a time. A new bind moves the value and logs the move. Rookery MUST match bindings only inside the current
  session.
- **FR-027** (RQ-1.22, RQ-1.24): Rookery MUST resolve an agent session in two stages.
  First, the current Rookery session:
  1. `ROOKERY_SESSION` names a Rookery session by name or by ID, or
  2. a session binding that the agent session presents matches a Rookery session.
  3. When neither gives a session, every hook MUST do nothing: no output, no registration, no state file.
     Rookery logs one line, `no-session` (or `unknown-session`), and nothing else. This makes Rookery opt-in per Rookery session.
  Then, the identity inside that Rookery session:
  1. `ROOKERY_NODE` names an identity by name or by ID. A new name creates the identity with that name.
     An unknown ID does not resolve. Rookery logs it and continues with step 2.
  2. A binding that the agent session presents matches an identity.
  3. Rookery creates a new identity, binds every binding that the agent session presents, and tells the
     agent its name and ID at session start.
  Inside a Rookery session, no agent runs without an identity. Every hook MUST use the resolved identity
  for each send, notice and push.
- **FR-028** (RQ-1.23): To push mail into a live session, the postmaster MUST run the wake command of the
  binding kind. The configuration defines that command. The postmaster fills in the binding value and the
  notice text. Rookery
  MUST NOT contain code for one specific terminal tool.
- **FR-029** (RQ-1.20): `rookery identity new | bind | show | list` MUST let the user create, name and
  re-bind identities in a session by hand, with `--json` output. The schema changes for sessions,
  identities, bindings and the session ID on mail MUST be additive. Run each one on a COPY of the live
  database before merge (constitution I).
- **FR-034** (RQ-1.25): Rookery MUST stamp the sender's Rookery session ID on every mail row that it
  writes. Rows that exist before this phase MUST belong to one `legacy` session, so `rk` and the MCP server
  keep working on them.
- **FR-033** (RQ-1.25): Sometimes `to` names an identity that exists only in another Rookery session.
  Then Rookery MUST record the mail as quarantined with the reason `off-team` and send a bounce. Mail never crosses a
  session boundary in phase 1.
- **FR-036** (RQ-1.26): `rookery session new --from <session>` MUST copy the newest `HANDOFF.md` of each
  agent name in the old session. The copy goes into the workspace of the same name in the new session. It
  MUST NOT copy mail, idle state or bindings. It MUST log each copied file. Each new identity that `--from`
  creates MUST record `previous_id`: the ID of the identity with the same name in the old session. So the
  history of an agent stays traceable across sessions. `rookery identity link --previous <id>` MUST set
  the same link by hand. A link never moves or merges mail.
  Carry-over is the one exception to "one writer per folder" (constitution VII): it MAY write `HANDOFF.md`
  into the successor identity's folder, once, at the link.
- **FR-035** (RQ-1.27): Phase 1 supports only a local mailroom. When `ROOKERY_URL` exists, each adapter MUST
  send nothing, MUST keep each block in `outbox/failed/` with the reason `remote-unsupported`, and MUST log
  it.

### Key Entities

- **Rookery block**: the one end-of-turn JSON object (FR-002). Belongs to one identity, one task, optionally one thread.
- **Mail record**: an existing mailroom row; this phase adds rows created from blocks/outbox files, including
  `quarantined`/`malformed` ones with `decided_by`.
- **Rookery session**: the unit that agents work in (one team, one monitor in phase 3, one boundary).
  Rookery generates its ID. It has a name, a state (`open` or `ended`) and zero or more bindings.
- **Identity**: an agent inside one Rookery session: stable ID, a name that is unique in that session,
  zero or more bindings. Resolved at session start (FR-027).
- **Binding**: a `kind:value` pair that ties an identity to a pane, session or tool. Rookery does not
  interpret it. A configured wake command per kind pushes mail into it.
- **Hand-off summary** (`runs/<TASK-ID>/HANDOFF.md`): per identity and task, overwritten every turn with a block.
- **Outbox file**: a block written as a file for mid-task sending; states: new, sent, failed.
- **Managed section**: the marker-delimited contract text in the harness context file.
- **Hook registration**: Rookery's three entries inside the harness settings, coexisting with others.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (R6): Across the fixture transcript set, conversations without a block produce 0 mail (100%).
  Across that set, each valid block produces exactly 1 mail, and each malformed block produces exactly 1 `malformed` quarantine
  row with a bounce.
- **SC-002** (R7): An `in-progress` hand-off produces 0 hand-off mail and an updated summary in 100% of
  fixtures; `done` produces exactly 1.
- **SC-003** (R5): In a live session after at least one compaction, the agent
  emits a valid block when anyone first asks it to hand off.
- **SC-004**: Running init three times on a project with dev-kid hooks leaves 100% of pre-existing hooks and
  text unchanged. It also leaves the files byte-identical after runs 2 and 3.
- **SC-005**: With no unread mail and no new failed block, the per-prompt notice adds 0 bytes on 100% of
  prompts. With unread mail, the notice is one line.
- **SC-006**: Rookery blocks 0 stops and pushes 0 notices into a running turn, in 100% of fixtures.
- **SC-007**: This spec and tasks.md cite every phase-1 requirement RQ-1.1–RQ-1.27 (`spec-pane cover`
  passes). R5, R6, R7 and R8 move to `proven` in the register in the same PR.
- **SC-008**: Searching an agent's workspace for a task ID lists every file Rookery wrote for that task.
- **SC-009** (R8): Two agent sessions that start in one project and one Rookery session with different
  bindings get two distinct identities in the roster. Each one states its own name and ID correctly when
  asked.
- **SC-010**: Take a Claude Code session in an initialized project with no current Rookery session. It
  gets 0 bytes from every hook and causes 0 roster or mail changes.
- **SC-011**: The same pane in a new Rookery session gets a new agent ID and sees 0 messages from the old
  session. Rookery quarantines mail from one Rookery session to a name in another as `off-team` in
  100% of fixtures.

## Assumptions

- **Identity source**: phase 1 owns sessions and identity (FR-025 to FR-029, FR-032 to FR-036). The configured binding sources start with
  the variables the user's tools set today (`HERDR_PANE_ID`, `TMUX_PANE`). They are configuration data, not
  code.
- **Workspace location before phase 2**: Rookery creates `runs/<TASK-ID>/` and `outbox/` on demand under
  `~/.rookery/projects/<project>/agents/<session>/<name>/` (constitution VII), so a new Rookery session
  starts with an empty workspace. The full workspace scaffold
  (`_config/`, stage folders, generated CLAUDE.md) is phase 2.
- Planning sets the size limit for a block / `read` entry from the tev1 ~2,050-token envelope (R16). The bounce
  states it. The screener itself is phase 3.
- "Unread" means delivered to this identity and not yet fetched via its inbox; `held` and `quarantined` mail is not
  unread for the recipient.
- Phase 1 has no headless wake for identities (FR-018). Existing postmaster nodes keep their own wake. The
  wake command per binding kind is the new part.
- `rookery` already has an `up` subcommand (remote peer node); phase 2 must resolve that name clash. Not
  touched here.
- A model-based mail judge (a tier-1 model decides send or drop) is deferred to a later phase.
- Personal use only; the managed section and hooks never handle sign-in tokens.
- Dependencies: Rookery mailroom and send path, PR #14 quarantine/bounce, the roster, the postmaster.
