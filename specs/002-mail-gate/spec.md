# Feature Specification: Every Rookery send goes through one gate

**Feature Branch**: `002-mail-gate`.
**Created**: 2026-10-21.
**Updated**: 2026-10-23 (revision 2: local identity is bookkeeping, not security. See D24).
**Status**: Draft.
**Input**: User description: "Every Rookery send goes through one gate." The full input is
`SPEC_INPUT_mail-gate.md`: a read-only audit of 2026-10-21 and the user's decisions. The audit table below is a copy,
because the input file goes away after this spec exists.

**Built from (read, not re-derived)**: the audit and the decisions in `SPEC_INPUT_mail-gate.md`. Spec 001
(`specs/001-rookery-init-turn-block/`: FR-027, FR-030, FR-031, FR-033, FR-034, FR-038, FR-039, FR-040 and
`contracts/cli-and-hooks.md`). Constitution v1.2.1.

**What spec 001 did**: it gates mail on the block path only. The Stop hook, `rookery turn` and the outbox
scan validate each block. They resolve the recipient inside the sender's own Rookery session. They
quarantine a wrong recipient (`wrong-recipient`) and an off-team recipient (`off-team`), and they apply
the loop breaker (FR-039).

**What this feature changes**: the low-level send is a bare database insert. About ten other send paths
call it directly. These paths skip the gate. They take the sender name from the caller, and they stamp the
mail with the `legacy` session. This feature puts every send path through the same gate.

**Decision of 2026-10-23 (user)**: the first design had too many identity rules for agents. The user owns
every agent on the machine, and any local process can set `ROOKERY_SESSION`. So a local identity check is
not security. It is bookkeeping. The one real security boundary is the HTTP mailroom when it binds beyond
localhost. This revision keeps the core: one gate, the same screening, the loop limits, the quarantine, the
log, reads that never mark mail read, notices with no body, a local mailroom that needs a token, and
`rookery loop reset`. It removes the per-identity token system, the forged-sender check and the
`no-session` quarantine. Agents can now talk with less friction.

**Done before this feature (PR #12, 2026-10-21)**: PR #12 retired `rk`. `rookery send TO SAY` is the one CLI
send command. It builds a block and uses the block gate, takes the sender from the resolved identity (no
`--as`, no `--from`) and never creates an identity. This spec does not change `rookery send`, except to
move it onto the extracted gate (FR-002).

### Audit findings (input, 2026-10-21)

| Send path | Where (at audit time) | Problem |
|---|---|---|
| `rk send --as <name>` | `~/.local/bin/rk:52-55` | RETIRED by PR #12. `rookery send` replaces it and uses the block gate. |
| MCP `send` tool, stdio | `rookery_mcp.py:78-80, 133-139` | Sender = the server node. Recipient = any string. |
| MCP `send` tool, shared HTTP | `rookery_mcp.py:106-117` (`_node()`) | Sender comes from the unauthenticated `x-rookery-node` header. |
| `send_mail.py --from` | `send_mail.py:11` (flag), `:15` (send) | Any sender name. The default is `human`. |
| Mailroom `POST /send` (also `mailctl.py send --from`) | `mailroom_server.py:427-429, 465, 482-484` | No auth without `--token`. Binds `0.0.0.0`. Sender comes from the body of the call. |
| A2A `message/send`, `message/stream` | `mailroom_server.py:212, 353` | The caller supplies the sender suffix. |
| `node_runner` `MAILTO:`, REJECTED notice and auto-reply | `node_runner.py:217, 254, 327` | Free text from a model becomes mail with no gate. |
| System senders | `postmaster.py:519, 536`, `relay.py:76`, `rookery.py:398` (bounce) | Direct inserts. They name a system sender. |

Line numbers re-checked on 2026-10-21 against master `67dfcf2`. Only `turn_block.py:475, 534` (the block
path) pass a `session_id`. Every other caller writes a `legacy` row.

Also found:

- `terminal_node.py:66-74` types the mail BODY into a pane. It marks the mail read when it types it.
  There, herdr or tmux carries the mail. Everywhere else, the pane gets only a notice.
- The postmaster `@mention` scan (`postmaster.py:523-540`) has no status filter and no session filter.
  It copies the first 140 characters (`:538`) of held and quarantined mail to other nodes.
- Rookery stores bypass mail to an `ag-…` ID in another session as `pending`. `unread()`
  (`turn_block.py:787-804`) has no session check, so that mail shows in another session. Rookery shows an
  `ag-…` sender with the name of the real agent.
- Mail to an old node name that has no identity has no push path. Nothing ever shows it.
- The live mailroom database used the schema from before 0.7. PR #12 work migrated it on 2026-10-21
  (`rookery session new --name tab5`, proven on a copy: 125 rows kept, all `legacy`).
- The live tools (`rookery-mcp.service`, the mailroom) run from `~/Documents/code/rookery`. On 2026-10-21
  that checkout was on `feat/held-quarantine`, 58 commits behind master.
- Naming (audit of 2026-10-21): `inbox` has four meanings. MCP `check_inbox` and `await_message`
  read and mark read (`_drain`, `rookery_mcp.py:120`).
  `rookery inbox` and `mailctl inbox` change nothing, and `rookery ack` marks read. `ROOKERY_NODE` is both
  the legacy MCP node ID (`rookery_mcp.py:34`) and the 0.7 identity (`rookery_identity.py:439`). The docs
  (`README.md`, `docs/QUICKSTART.md`, `docs/COOKBOOK.md`, `docs/STORIES.md`, `docs/NODE_BRIEFING.md`) teach
  `mailctl` and `send_mail.py`. `cli_up.py:223` tells agents to call `rookery.send()`. `terminal_node.py:61`
  tells agents to reply with `send_mail.py`.
- `rookery identity show` ignores a `herdr-pane` binding and says "No current identity". `rookery send`
  resolves the same pane.

## Clarifications

### Session 2026-10-22

- Q: What happens to mail from a sender with no Rookery session? → A: Superseded 2026-10-23. Rookery now
  delivers it as `pending` in session `legacy` and logs a `no-session` warning. It does not quarantine or
  bounce it.
- Q: How does the mailroom check an HTTP token, when the database stores no token? → A: Superseded
  2026-10-23. The mailroom keeps the one shared token that it has today. The database stores no token.
- Q: An HTTP call has a valid token for A, but its body or header names B as the sender. What does
  Rookery do? → A: Superseded 2026-10-23. A token names no identity. The sender is the asked sender, and
  the log records it.
- Q: The user starts the mailroom with no token and no opt-in. What happens? → A: It refuses to start
  and exits non-zero. It prints the two choices: give a token, or give the no-auth opt-in.
- Q: MCP `check_inbox` marks mail read when it reads it. Does 002 change that? → A: Yes. A read never
  marks mail read on any path. Only an ack does. `check_inbox` stops marking mail read, and MCP gets an
  `ack` tool that takes mail IDs. `await_message` changes the same way. The CHANGELOG and the move path tell this.

### Session 2026-10-23

- Q: Local identity checks do not stop a local process that sets `ROOKERY_SESSION`. Do they stay? → A: No.
  The user owns every agent on the machine. Local identity is bookkeeping (D24). Rookery keeps the gate and
  drops the identity rules that add friction. The HTTP mailroom stays the security boundary.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No send path can skip the gate (Priority: P1)

The user runs several agents in one Rookery session. An agent can send mail in many ways: a block,
`rookery send`, the MCP tool, `send_mail.py`, the mailroom HTTP API (and `mailctl.py`), A2A, or a node runner. Each way gets the same checks
as the block path. When the sender has a session, Rookery quarantines mail to an unknown recipient and
sends a bounce. Rookery quarantines mail to a recipient in another session and sends a bounce. Rookery
holds a loop. When the sender has no session, Rookery delivers the mail as given, in session `legacy`.

**Why this priority**: the gate protects the user only when no path can go around it. One open path makes
every other check useless.

**Independent Test**: send one mail through each send path in the audit table. Send it to an unknown
name, to a peer in another session, and to a valid peer. A path with a session gives the same result as
the block path: Rookery quarantines the first two with a bounce and delivers the third. A path with no
session delivers all three in `legacy`, with a `no-session` warning in the log.

**Acceptance Scenarios**:

1. **Given** an agent in session S1, **When** it sends on any path to a name in no session, **Then**
   Rookery quarantines the mail as `wrong-recipient`. Rookery bounces it to the sender, as on the
   block path.
2. **Given** an agent in session S1, **When** it sends on any path to an identity only in S2, **Then**
   Rookery quarantines the mail as `off-team`. Rookery bounces it.
3. **Given** two agents in one session that mail each other through MCP, **When** a loop counter
   reaches its limit, **Then** Rookery holds the next mail. The reason is `loop`, as on the
   block path.
4. **Given** a valid recipient in the same session, **When** the agent sends through any send path,
   **Then** Rookery delivers the mail. Rookery stamps the sender's session on it.
5. **Given** a sender with no session, **When** it sends on any path, **Then** Rookery stores the mail
   `pending` in session `legacy`, with the recipient as given. The outcome is `sent`. The gate log
   records a warning with the reason `no-session`.

---

### User Story 2 - System names and bad names are refused, and the log shows who asked (Priority: P2)

Rookery takes the sender from the call: `--from`, the body of a call, a header, or else the resolved
identity. This is bookkeeping, not security. The gate refuses two kinds of sender: a system name and a
name that is not valid. The log records both the asked sender and the resolved principal, so a
mismatch is easy to see.

**Why this priority**: a system name in a mail sender field would look like Rookery itself. A bad name
can carry control text. The mismatch log gives the user the facts without a refusal that blocks agents.

**Independent Test**: send as `postmaster` through `send_mail.py --from`, the body of a mailroom call and
the A2A sender suffix. Rookery refuses each try. Send as `x; rm -rf ~`. Rookery refuses it as `bad-input`.
Send as another agent name. Rookery sends the mail and the log shows both names.

**Acceptance Scenarios**:

1. **Given** any caller, **When** it names a system sender (`postmaster`, `relay`, `rookery`) as the sender,
   **Then** Rookery refuses the send with the reason `system-name`, and stores no mail.
2. **Given** any caller, **When** the sender name does not match `^[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,64})?$`, **Then**
   Rookery refuses the send with the reason `bad-input`, and stores no mail.
3. **Given** agent A, **When** A runs `send_mail.py --from B`, **Then** the mail goes out as B. The gate
   log has `asked_sender` B and the resolved principal A.
4. **Given** the mailroom runs with a token, **When** an HTTP call with no token or a bad token reaches a
   route that needs a token, **Then** the mailroom answers 401 and stores no mail.

---

### User Story 3 - The mailroom is closed by default (Priority: P1)

The user starts the mailroom server with no options. It accepts only local connections, and it asks for the
shared token. To open it to the network, or to run it without a token, the user must say so explicitly.

**Why this priority**: today the default mailroom binds every network interface with no auth. Any host
that can reach the port can write mail. This is the one real security boundary of the feature.

**Independent Test**: start the mailroom with no options. Check that it refuses to start. Start it with a
token. Check that it listens only on `127.0.0.1`. Check that it refuses a send with no token. Start it with
the opt-in options, and check that it then binds as asked and prints a warning.

**Acceptance Scenarios**:

1. **Given** no options and no token, **When** the user starts the mailroom, **Then** it refuses to start
   and exits non-zero. It prints the two choices: give a token, or give the no-auth opt-in.
2. **Given** a token and no host option, **When** the user starts the mailroom, **Then** it binds only
   `127.0.0.1`.
3. **Given** the explicit opt-in for a public bind or for no auth, **When** the user starts the mailroom,
   **Then** it does as asked. It prints a warning that names the risk.

---

### User Story 4 - Mail stays inside its session (Priority: P2)

An agent sees only the mail of its own session, and the legacy mail that names it. No mail, notice or
excerpt of another session goes to it. This includes the `@mention` notice, and mail that a bypass path
stored before this feature.

**Why this priority**: the session is the boundary that spec 001 set. The leaks in the audit break it.

**Independent Test**: put mail for an `ag-…` ID of session S2 into the inbox from session S1, and put
held and quarantined mail that names `@C`. Check that no agent in another session sees the mail, a
count of it, or an excerpt of it.

**Acceptance Scenarios**:

1. **Given** pending mail of session S1, **When** an agent in session S2 asks for unread mail,
   **Then** neither the result nor the count includes that mail.
2. **Given** held or quarantined mail that mentions `@C`, **When** the postmaster scans for mentions,
   **Then** C gets no notice.
3. **Given** pending mail in session S1 that mentions `@C`, and C is in session S1, **When** the postmaster
   scans, **Then** C gets one notice. The notice names the sender and the recipient, and has no part of
   the body.
4. **Given** pending mail in session S1 that mentions `@C`, and C is in another session, **When** the
   postmaster scans, **Then** C gets no notice.
5. **Given** `legacy` mail to the name or agent ID of agent C, **When** C asks for unread mail, **Then**
   the result includes that mail. The filter does not lose mail from a sender with no session.

---

### User Story 5 - A pane gets a notice, never the mail body (Priority: P2)

A terminal node puts mail into a pane. It types only a short notice that tells the agent to run
`rookery inbox`. The agent reads the mail through the gate's read path. Typing the notice does not mark
the mail read.

**Why this priority**: typed text in a pane looks like a user prompt to the agent. A mail body typed
there is an injection path, and the node marks the mail read before the agent sees it.

**Independent Test**: send mail to a terminal node. Read the pane. It shows the notice, not the body. The
mail stays unread until the agent runs `rookery ack`.

**Acceptance Scenarios**:

1. **Given** new mail for a terminal node, **When** the node types into the pane, **Then** the pane shows
   a notice. The notice has the sender and the `rookery inbox` command, and no part of the body.
2. **Given** the node typed the notice, **When** the agent has not run `rookery ack`, **Then** the mail is
   still unread.

---

### User Story 6 - Existing users keep working (Priority: P2)

Agents use the MCP server, `mailctl.py` and `send_mail.py` on the live mailroom today. (`claude-tab5` and
`kimi-tab5` moved to `rookery send` in session `tab5` with PR #12.) After the change, these tools still
work, but through the gate. An agent with no Rookery session keeps sending: its mail goes to `legacy`.

**Why this priority**: the change must not silently break the user's working agents.

**Independent Test**: on a COPY of the live database, open the database with the new code. Check that no
row count changes. Then send mail from a node name that has no identity. Check that Rookery delivers it in
`legacy`.

**Acceptance Scenarios**:

1. **Given** the live database after PR #12, **When** the new code opens a copy, **Then** the copy keeps
   every row and the schema does not change. The gate works on the copy.
2. **Given** an old node name with no identity, **When** it sends through MCP or `send_mail.py`, **Then**
   Rookery delivers the mail `pending` in `legacy`. The log has a `no-session` warning.
3. **Given** `legacy` mail to the name of agent C, and C has an identity in an open session, **When** C
   asks for unread mail, **Then** C sees that mail.
4. **Given** an agent on MCP with two unread mails, **When** it calls `check_inbox` two times, **Then** it
   gets the same two mails both times. **When** it calls `ack` with one ID, **Then** the next
   `check_inbox` shows only the other mail.

---

### Edge Cases

- A system sender (`postmaster`, `relay`, `rookery`) sends a dead-letter, `CRED_GRANTED`, `MENTION` or
  bounce mail. It names itself as a system sender explicitly. The gate does not hold it. A non-system
  caller cannot use a system sender name (`refused`/`system-name`).
- A bounce goes to a sender that has no session (for example after `injection-suspect`). Rookery stores the
  bounce in the `legacy` session, so the sender sees it with `read_mail.py` or MCP. The bounce does not
  count toward a loop of a real session.
- A caller gives `--from` with a name that is not its own. The mail goes out under that name. The log
  records `asked_sender` and the resolved principal.
- A model in a node runner writes `MAILTO:` to a name that is not in its session. When the node has a
  session, Rookery quarantines the mail and bounces it to that node, as for any other path. When the node
  has no session, Rookery delivers the mail as given.
- The gate itself fails (database locked, bad config). The send path reports the failure and exits
  non-zero. It never falls back to a send with no gate.
- A recipient name matches identities in two sessions. Rookery uses only the sender's session. A sender with
  no session has no session to search, so the recipient stays as given.
- Old pending mail that a bypass path stored as `legacy` with an `ag-…` recipient. It stays in the
  database. Only the identity with that agent ID sees it.

## Requirements *(mandatory)*

**Phase requirements covered**: this feature is a hardening of phase 1 from the 2026-10-21 audit. It
has no RQ IDs of its own. It extends RQ-1.20, RQ-1.22, RQ-1.24 and RQ-1.25 (identity, resolution,
session, session boundary) to every send path. It keeps RQ-0.2 and RQ-0.3 (system senders, quarantine and
bounce) and RQ-1.19 (plain English).

### Functional Requirements

Removed IDs stay in the list, so that every other ID stays stable.

**One gate**

- **FR-001** (RQ-1.25): Rookery MUST have one gate that every send path uses. The gate resolves the
  sender. When the sender has a session, it resolves the recipient inside that session. It applies the loop
  breaker. Then it sends, holds or quarantines the mail, with a bounce when it quarantines.
- **FR-002**: The block path (Stop hook, `rookery turn`, outbox scan) and `rookery send` MUST use the same
  gate. Their observable results MUST NOT change: the spec 001 tests and `test_send.py` MUST pass without a
  change.
- **FR-003**: These send paths MUST use the gate: the MCP `send` tool (stdio and shared HTTP),
  `send_mail.py`, mailroom `POST /send` and A2A `message/send` and `message/stream`. The node runner
  (`MAILTO:`, the REJECTED notice and auto-reply) MUST also use it. `mailctl.py` calls `POST /send`.
- **FR-004**: No send path MAY insert mail without the gate, except the system senders in FR-012. A test
  MUST find each direct call to the low-level insert and fail when an unlisted caller exists.
- **FR-005** (RQ-1.25): The low-level insert MUST need a session ID. It MUST NOT use `legacy` as a
  default. A caller with no session ID gets a failure. The gate passes `legacy` explicitly for a sender
  with no session.
- **FR-006**: The loop breaker of spec 001 FR-039 MUST apply to every send path, not only to the block
  path. This replaces the FR-039 sentence that says the node send paths do not count. FR-037 gives the reset for paths with no hook.
- **FR-007**: If the gate fails, the send path MUST report the failure, store nothing, and exit non-zero.
  It MUST NOT send without the gate.

**The sender**

- **FR-008** (RQ-1.22): The sender of a mail is the asked sender (`--from`, the body `sender`, or a header)
  when the caller gives one. Else it is the agent ID of the resolved identity. Else it is the raw name of
  the path. Local paths (`rookery send`, stdio MCP, `send_mail.py`, node runner) resolve an identity as in
  spec 001 FR-027. A local principal exists only when its session is `open` and the identity exists in it.
  HTTP paths (mailroom, A2A, shared MCP) have no principal. The session of the mail is the session of the
  principal when one resolves, else `legacy`. The gate refuses a sender that is a system name
  (`refused`/`system-name`). It refuses a sender that does not match `^[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,64})?$`
  (`refused`/`bad-input`). A refusal stores no mail. A local path prints the reason and exits 2.
- **FR-009**: Removed 2026-10-23. A local identity check is bookkeeping, not security (D24). The log shows
  a mismatch (FR-030).
- **FR-010**: Removed 2026-10-23. The mailroom keeps one shared token. It has no per-identity token.
- **FR-011**: No send path MAY register a new node name or create an identity when it sends (as
  `rookery send` already does not). `rookery` MUST stay the one CLI for mail. A new send or read command
  MUST be a `rookery` subcommand, and MUST NOT take a sender flag.
- **FR-012** (RQ-0.2): The system senders (`postmaster`, `relay`, `rookery`) MUST pass their sender
  explicitly to the gate. Only Rookery's own processes MAY use these names. Rookery MUST refuse a send
  from any other caller that names a system sender, as in FR-008.

**The recipient and the session**

- **FR-013** (RQ-1.25): When the sender has a session, the gate MUST resolve the recipient only inside
  that session. A name in no session gives `wrong-recipient`, as in spec 001 FR-030. A name only in another
  session gives `off-team`, as in spec 001 FR-033. Both get a bounce (RQ-0.3). A sender with no session
  has no recipient checks: the gate stores the recipient as given. A recipient with `@` is a remote address: the gate skips the recipient checks and stores it as given. The gate MUST NOT rewrite a recipient
  name into an identity ID unless the name matches an identity in the sender's session.
- **FR-014**: A sender with no resolvable session MUST have its mail delivered. Rookery stores the mail
  `pending` in session `legacy`, with the outcome `sent`, as the code does today. The gate log MUST record
  a warning with the reason `no-session`. Rookery MUST NOT quarantine or bounce the mail for this reason.
- **FR-015**: The unread query MUST return mail stamped with the reader's session, and also `legacy` mail
  addressed to the reader's name or agent ID. It MUST NOT return other mail. Counts and notices MUST use
  the same filter. The HTTP reads are the one exception: the mailroom has no identity, so `/inbox` returns
  mail to the named node in any session, as today (D24).
- **FR-016**: Rookery MUST show the sender of a mail as the gate stored it. When the stored sender is the
  agent ID of an identity in the reader's session, Rookery shows the name of that identity. The gate log
  MUST record the asked sender and the resolved principal.

**Mention notices**

- **FR-017**: The postmaster MUST send a mention notice only for mail that is not held or quarantined. The mentioned identity
  MUST be in the same session as the sender.
- **FR-018**: A mention notice MUST NOT contain any part of the mail body. It names the sender, the
  recipient and the topic, and tells the agent that it cannot read that mail.

**Panes**

- **FR-019**: A terminal node MUST type only a notice into a pane: the sender, the topic and the
  `rookery inbox` command. It MUST NOT type the body.
- **FR-020**: A terminal node MUST NOT mark mail read when it types the notice (a case of FR-032). Only `rookery ack` marks
  mail read (spec 001 FR-031).
- **FR-021**: Rookery MUST NOT need herdr. Herdr and tmux stay optional wake carriers, set by binding
  kind (spec 001 RQ-1.23).

**The mailroom server**

- **FR-022**: The mailroom MUST bind `127.0.0.1` by default. This includes `rookery serve`. FR-023 and FR-024 apply to it too.
- **FR-023**: The mailroom MUST need its one shared token by default (`--token`, or `$ROOKERY_TOKEN`). With
  no token and no opt-in, it MUST refuse to start and exit non-zero. It MUST print the two choices: give a
  token, or give the no-auth opt-in. One function, `check_start(args)`, MUST hold the start rules. Both
  `mailroom_server.main` and `rookery serve` MUST call it.
- **FR-024**: A public bind (a host other than `127.0.0.1`, `::1` or `localhost`) and a start with no auth
  MUST each need an explicit opt-in (`--allow-public`, `--allow-no-auth`). Each opt-in MUST print one
  `WARNING` line at start.

**Screening**

- **FR-025**: This feature MUST NOT add a model to the gate. Content screening keeps its current seam
  (`security.inspect_inbound`) and `mail_screen.py`. The gate MUST call that seam on every send path, in
  the same place for each path.

**Migration and existing users**

- **FR-026** (constitution I): This feature MUST NOT change the schema. It adds no table and no column. It
  adds no quarantine reason. A test on a COPY of the live database MUST show that opening it with the new
  code changes no row count.
- **FR-027**: The MCP server, `mailctl.py` and `send_mail.py` MUST keep working, with or without an
  identity in a session.
- **FR-028**: Removed 2026-10-23. A sender with no session now sends. No report of unmigrated senders is
  needed.
- **FR-029**: The docs and the CHANGELOG MUST give the move path for the current MCP, `mailctl.py` and
  `send_mail.py` users in the same PR (constitution, Development Workflow). Every doc that teaches
  `mailctl.py`, `send_mail.py` or `read_mail.py` MUST label it legacy and give the `rookery` command.
  `cli_up.py:223` and `terminal_node.py:61` MUST point to `rookery send`. `plugin_herdr/cli.py:201-210` MUST NOT tell agents to use `mailctl.py` or `send_mail.py`. The CHANGELOG and the move
  path MUST tell MCP users that `check_inbox` no longer marks mail read, and that they must call `ack`
  (FR-032).

**Observability**

- **FR-030** (constitution VI): The gate MUST log every refusal, quarantine, hold and `no-session` warning.
  The log MUST show the send path, the resolved principal, the sender, the asked sender (if any), the
  recipient, the session, the outcome, the reason, the warning and `decided_by`.
- **FR-031** (RQ-1.19): The bounce texts, the refusal messages, the pane notice and the mention notice
  MUST pass `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt`.

**Reads**

- **FR-032**: A read MUST NOT mark mail read on any path. Only an ack marks mail read. MCP
  `check_inbox` and `await_message` (both call `_drain`, `rookery_mcp.py:120`) MUST stop marking mail read. MCP MUST have an `ack` tool that takes mail IDs and marks
  read only the mail of the caller's identity. `check_inbox` MUST end with the IDs to give to `ack`.

**Routes, relays, nodes and loops**

- **FR-033**: Removed 2026-10-23. A token names no identity, so A2A has nothing to scope to. `tasks/get`
  behaves as today.
- **FR-034**: Removed 2026-10-23. The mailroom has one shared token. It has no scopes and no route table.
  `/approve`, `/invite`, `/join` and `GET /join/<id>` behave as today.
- **FR-035**: Removed 2026-10-23. There is no relay token. `relay.py` behaves as today.
- **FR-036**: Removed 2026-10-23. A managed node stays a node name. It is not an identity (D22 removed).
- **FR-037**: Rookery MUST have the human command `rookery loop reset [NAME | --legacy NAME] [--json]`. It resets the loop counters of the pairs of NAME in the current session. With `--legacy`, it resets the `legacy` counters of the raw NAME, so a sender with no session can send again. It logs the reset to the gate log. A held loop mail leaves the hold with `mail_screen.py release`. A loop-held send sends no bounce, as in spec 001 FR-039.

### Key Entities

- **Send path**: one way to send mail. The 9 paths of SC-001 are the block, `rookery send`, MCP stdio, MCP HTTP, `send_mail.py`,
  mailroom `POST /send`, `mailctl.py send`, A2A and the node runner. A system sender is listed apart. The gate records the path on each decision.
- **Principal**: what the gate resolves on a local path. It is the identity from spec 001 FR-027, with its
  session. An HTTP path has no principal. A local path with no identity has none either.
- **Gate decision**: the outcome of one send: `sent`, `quarantined` (with reason and bounce), `loop-held`, `refused` (with reason), `db-busy` or `send-failed`, plus `decided_by`. A `sent` outcome can carry the warning `no-session`. A `refused`, `db-busy` or `send-failed` outcome stores nothing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For each of the 9 send paths in T012, T014, T015, T016 and T018 (block, `rookery send`, MCP stdio, MCP HTTP, `send_mail.py`, mailroom `POST /send`, `mailctl.py send`, A2A, node runner), run 3 tries (unknown name, other session, valid peer). That is 9 x 3 = 27 cases, and 27 of 27 pass. The cases sit in five files: `test_paths_local.py` (6), `test_paths_mcp.py` (6), `test_paths_send_mail.py` (3), `test_paths_mailroom.py` (9) and `test_paths_node_runner.py` (3). There is no fourth try. A path with a session gives `wrong-recipient`, `off-team` and `sent`. A path with no session gives `sent` in `legacy` for all 3 tries, with the `no-session` warning. System senders are tested on their own (T008, T019).
- **SC-002**: A search of the code finds 0 calls to the low-level insert outside the gate and the listed
  system senders.
- **SC-003**: Removed 2026-10-23. It tested forged senders, which Rookery no longer refuses.
- **SC-004**: An agent in one session sees 0 mails, 0 counts and 0 excerpts from another session, in a test with mail in two sessions. Mail in `legacy` shows only to the name or agent ID that it names. The HTTP reads are out of this count: the shared token holder reads by name in any session (D24).
- **SC-005**: The mailroom started with no options accepts 0 connections from other hosts and 0 sends
  with no token.
- **SC-006**: A pane notice contains 0 characters of the mail body.
- **SC-007**: Opening a copy of the live database with the new code keeps 100% of the rows (count before = count
  after) and changes no schema. The spec 001 tests pass on the copy.
- **SC-008**: Removed 2026-10-23. It tested the `no-session` bounce, which no longer exists.
- **SC-009**: On every read path (`rookery inbox`, `rookery peek`, MCP `check_inbox`, `read_mail.py`,
  `await_message`, `mailctl inbox`, `GET /inbox`, A2A `tasks/get`, the pane notice), a read marks 0 mails read. Only an ack marks mail read.
- **SC-010**: A sender with no session sends mail to agent C. C's unread list shows it, in a test with C in
  an open session. 0 such mails are lost.

## Assumptions

- **No-session senders deliver (changed 2026-10-23)**: the gate cannot check a sender with no session.
  Rookery delivers its mail in `legacy` and logs a warning (FR-014). There is no quarantine and no bounce.
- **Mention notice (choice made here)**: the input allows "only pending mail in the same scope" or "drop
  the excerpt". This spec does both (FR-017, FR-018).
- **Token model (changed 2026-10-23)**: the mailroom keeps the one shared token that it has today. It is a
  gate for the network, not an identity. The database stores no token.
- **Local identity is bookkeeping (D24)**: all agents run as one Unix user, and the user owns every agent. A
  local agent can set `ROOKERY_SESSION` or `ROOKERY_NODE` to any name, or open the database file. This
  feature does not try to stop that. The log records the asked sender and the principal. The only
  security boundary is the mailroom when it binds beyond localhost. Stronger local checks need RQ-2.9.
- **`--from` stays** as an accepted flag of `send_mail.py` and `mailctl.py`. When given, it is the sender.
  `rk` and its `--as` are gone (PR #12).
- The `legacy` session keeps the old rows. Mail from a sender with no session goes there.
- No model judge in this feature (spec 001 Assumptions: a later phase).
- The 0.7 agent skill (`skills/rookery/SKILL.md`) is on master (PR #10). PR #12 added `rookery send` to it.
- Dependencies: spec 001 (sessions, identities, the block gate, the loop breaker). PR #12 (`rookery send`,
  the end of `rk`). PR #14 (quarantine and bounce), the postmaster, the terminal node, the mailroom server.
