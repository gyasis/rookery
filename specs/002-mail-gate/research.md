---
type: Research
task: 002-mail-gate
feature: Every Rookery send goes through one gate
updated: 2026-10-23
revision: 2
---

# Research: every Rookery send goes through one gate

Each decision names its evidence. "Code" means a file:line in this worktree at master `e2c4657`. A
read-only inventory of 2026-10-22 checked each line number. "Spec" means `spec.md` in this folder.

Three facts from the inventory change the audit table:

- The mailroom server is `mailroom_server.py`. A2A is inside it. There is no separate A2A module.
- The screening seam is `security.inspect_inbound` (`security.py:74`). Only A2A (`mailroom_server.py:209,
  341`) and the node runner (`node_runner.py:249`) call it. The block path does not call it.
- `turn_block._dispatch_block` (`turn_block.py:602`) does not resolve the sender. Its caller resolves
  the sender and passes `ctx["identity"]`.

## D1. One gate module

- **Decision**: Add `mail_gate.py`. Move the checks that follow `validate()` out of
  `turn_block._dispatch_block` into `mail_gate.submit()`: the recipient lookup, `off-team`,
  `wrong-recipient`, the loop breaker, the send, the quarantine and the bounce. `turn_block` keeps
  `validate()`, `HANDOFF.md`, and the park step of the Stop hook.
  `_dispatch_block` calls `submit()`. It passes the hand-off check (`handoff-pending`) as `pre_store`: a callable
  that gets the resolved recipient row after check 6 (screening) and returns `None` or `("held", reason)`. A block that does
  not parse (`malformed`, `turn_block.py:~475-477`) goes through `mail_gate.quarantine_malformed` (the moved
  `_hold_and_quarantine`). `turn_block` never calls `rookery.send` for it.
- **Rationale**: FR-001 asks for one gate, and FR-002 asks for no change on the block path. The block
  checks already exist and have tests (`test_turn_block.py`, `test_send.py`). A move keeps those tests as
  the proof. A gate inside `turn_block.py` would make every send path import the block parser.
- **Alternatives considered**: (a) Every path builds a block and calls `dispatch_block`, as
  `rookery send` does. Rejected: HTTP and MCP calls have no `task` field, and `validate()` would reject
  them as malformed. (b) Put the checks in `rookery.send()`. Rejected: `rookery.py` is the storage
  layer and has no identity code. The bounce in `quarantine()` calls `send()`, so a gate there recurses.

## D2. The low-level insert needs a session ID

- **Decision**: `rookery.send()` (`rookery.py:223`) must get `session_id` as a keyword argument.
  The `or "legacy"` at `rookery.py:231` goes. A call with no session ID raises `TypeError`. The gate passes `"legacy"` explicitly for a sender with no session, and for a bounce to it.
- **Rationale**: FR-005. A missing argument fails at the call, not as a silent `legacy` row.
- **Note**: the `inbox.session_id` column keeps `DEFAULT 'legacy'` (`rookery.py:146`). A schema change
  to remove a default is not additive (constitution I). The code rule closes the path.

## D3. System senders

- **Decision**: Add `mail_gate.system_send(conn, sender, recipient, body, topic, session_id)`. It
  refuses a sender that is not in `rookery.SYSTEM_SENDERS` (`rookery.py:213`). The postmaster
  (`postmaster.py:519, 536`) and `relay.py:76` call it. The bounce inside `rookery.quarantine()`
  (`rookery.py:398`) stays a direct `send()`: it is the storage layer and always passes the row's session.
- **Gate check**: `submit()` refuses a sender whose name is a system sender name, asked or raw
  (FR-012). `rookery_identity.new_identity()` refuses a system sender name, so no
  identity can take one.
- **Rationale**: FR-004 needs a short list of direct callers that a test can check. The list is
  `mail_gate.py` and `rookery.py`.

## D4. The insert-caller test

- **Decision**: `test_gate_scan.py` parses every non-test `.py` file at the repo root with `ast`. It
  resolves aliases (`import rookery as X`, `from rookery import send [as s]`) and finds each call to `send` on the
  `rookery` module. It fails on `getattr(<rookery alias>, "send")`. It scans the repo root and `plugin_herdr/`.
  It fails when the file is not `mail_gate.py` or `rookery.py`. It also fails on any
  `INSERT INTO inbox` text outside `rookery.py`. It fails for a call to `rookery.quarantine` outside
  `mail_gate.py`, `rookery.py` and `mail_screen.py` (a human tool on an existing row). It allow-lists
  `UPDATE inbox SET recipient` in `relay.py` only (re-addressing local mail, not a send). It fails for a call
  to `mail_gate.system_send` outside `postmaster.py`, `relay.py`, `rookery.py` and `mail_gate.py`. A text scan
  fails when `cli_up.py`, `terminal_node.py` or `plugin_herdr/cli.py` tell agents to use `send_mail.py` or
  `rookery.send(` (FR-029).
- **Rationale**: FR-004 and SC-002. An AST check finds aliases that a text search misses.

## D5. How each path finds its sender

| Path | Principal | Asked sender (becomes the sender) |
|---|---|---|
| block, `rookery send` | `rookery_identity.resolve(create=False)` (no change) | none |
| MCP stdio | `resolve(create=False)` on the server's environment | none |
| `send_mail.py` | `resolve(create=False)` on its environment | `--from` |
| node runner | `resolve(create=False)` for `--node-id` in `ROOKERY_SESSION`, else none | none |
| mailroom `POST /send`, `mailctl.py send` | none | body `sender` |
| A2A `message/send`, `message/stream` | none | `a2a-<metadata.from>`, as today |
| MCP shared HTTP | none | the node name of the header `x-rookery-node`, as today |
| system sender | the explicit name (D3) | none |

- **Rule** (FR-008): the sender is the asked sender when given, else the agent ID of the principal, else
  the raw name of the path. The session is the session of the principal, else `legacy`. The gate refuses a
  system name (`refused`/`system-name`) and a name that fails `^[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,64})?$`
  (`refused`/`bad-input`). It stores nothing for a refusal. Local paths exit 2. HTTP paths answer 403
  (`system-name`) or 400 (`bad-input`). The log records `asked_sender` and `principal`, so a mismatch is
  visible (D24).
- **`principal_from_env`** returns `(session, identity)` only when the session is `open` and the identity
  exists. A session found with no identity, or a session that is not open, gives `None`. Then `submit`
  delivers the mail in `legacy` with the warning `no-session` (D6).
- **No principal**: a local path with no session, or any HTTP call, has no principal. The gate does not
  check the recipient (FR-013).
- **No registration** (FR-011): the MCP `_register` call on first use (`rookery_mcp.py:93-104`) goes.
  No path calls `resolve(create=True)`.

## D6. `no-session` delivers

- **Decision**: a sender with no resolvable session gets its mail stored `pending` in the `legacy`
  session, with the outcome `sent`. This is what the code does today. The gate log records a warning with
  the reason `no-session`. There is no quarantine, no bounce and no new quarantine reason.
- **Recipient**: with no session, the gate does not run `off-team` or `wrong-recipient`. It stores the
  recipient as given. It does not rewrite a name into an identity ID. The block path and `rookery send`
  always have a session, so `test_turn_block` and `test_send` do not change.
- **Reads**: an identity reads the `legacy` mail to its name or agent ID through `unread` (D11). A reader
  with no identity uses `unread_legacy(raw_name)`.
- **Loop**: the counters use the sender's session, `legacy` when there is none. A bounce to a sender with
  no session (for example after `injection-suspect`) uses `bounce_limit`, as `quarantine()` does now
  (`rookery.py:394`). It never counts toward a loop of a real session.
- **Rationale**: the user owns every agent (D24). A refusal or a quarantine here only adds friction.
- **Alternatives considered**: quarantine with a bounce and setup commands (the first design, clarify
  Q1 of 2026-10-22). Rejected on 2026-10-23: agents that have no session stop working until a human sets
  one up.

## D7. The one shared HTTP token

- **Decision**: the mailroom keeps the one shared token that exists today. It comes from `--token`, else
  from `$ROOKERY_TOKEN` (`mailroom_server.py:482-484`, `security.DefaultPolicy`). A call with no token or a
  bad token on a route that needs one gets 401. The token names no identity. The database stores no token.
  There is no `http_tokens` table, no `rookery token` command, no scope and no route table.
- **Rationale**: the HTTP mailroom is the one real security boundary (D24). One shared secret closes it
  when it binds beyond localhost. Per-identity tokens add friction and do not stop a local agent that owns
  the machine.
- **Caller side**: `mailctl.py` and the MCP remote mode already send `Authorization: Bearer
  $ROOKERY_TOKEN` (`mailctl.call`). Nothing changes for them.
- **Routes**: `security.PUBLIC_PATHS` stays the list of routes that need no token. `/approve`, `/invite`,
  `/join` and `relay.py` behave as today. `HardenedPolicy.authorize` (`policy_hardened.py`) keeps working
  as today.

## D8. Mailroom start

- **Decision**: `--host` default changes to `127.0.0.1` (`mailroom_server.py:465`). With no token and no
  `--allow-no-auth`, the mailroom exits 2. It prints the two choices: give a token (`--token` or
  `$ROOKERY_TOKEN`), or give `--allow-no-auth`. The mailroom needs `--allow-public` for any host other than
  `127.0.0.1`, `::1` or `localhost`. Each opt-in prints one `WARNING:` line at start. With a token set,
  `--allow-no-auth` has no effect. The rule "a start with `--token` exits 2" of the first design is gone.
- **`rookery serve`** (H2): `cli_serve.py:88-101` builds its own `ThreadingHTTPServer`, and `rookery_cli.py:41-42`
  defaults `--host` to `0.0.0.0` and takes `--token`. The start rules move into one function,
  `mailroom_server.check_start(args)`. `main()` and `cli_serve.cmd_serve` both call it. `rookery serve --host`
  defaults to `127.0.0.1`. Serve keeps `--token` and gets `--allow-public` and `--allow-no-auth`.
- **No-auth mode**: the mailroom has no token check, as it has none today. Every route answers. The warning
  at start names the risk.
- **Rationale**: FR-022 to FR-024 and clarify Q4. This is the one real security boundary (D24).
- **Other routes**: `/inbox`, `/thread`, `/claim` and `/ack` read and change only `legacy` rows of the named
  node (D11).

## D9. Screening on every path

- **Decision**: `submit()` calls `security.get_policy().inspect_inbound(sender, recipient, body)` before
  `BEGIN IMMEDIATE`, so a slow custom policy does not hold the write lock. Check 6 uses the verdict. A deny
  becomes a quarantine with reason `injection-suspect` and a bounce. The A2A calls at `mailroom_server.py:209, 341` go.
  `node_runner.screen()` stays, because it screens mail that the node reads.
- **Rationale**: FR-025 says one place for every path. The default policy returns ALLOW
  (`security.py:74`), so the block path results do not change (FR-002).
- **Change for A2A**: a denied A2A message now gets a quarantined row and a bounce, not only a
  JSON-RPC error. The A2A answer still carries error -32004.

## D10. Loop breaker on every path

- **Decision**: `submit()` runs the `turn_block._send_counted` logic (`turn_block.py:522`) for every
  path. The `task` key is the block `task`. For other paths it is the `topic`, or `-` when the topic is
  empty. The pair counter `("~pair", a, b)` catches loops that change topics.
- **Rationale**: FR-006. The pair counter already exists, so the gate needs no new counter.

## D11. Reads

- **Decision**: add `mail_gate.unread(conn, identity)` and `mail_gate.ack(conn, identity, ids)`.
  `unread` returns mail with the identity's session and agent ID as recipient, plus `legacy`
  mail to the identity's name or agent ID (FR-015). A filter on the session alone would lose mail from a
  sender with no session. It adds this to the query of `turn_block.unread` (`turn_block.py:787`). `turn_block.unread` calls it, so the prompt hook and `rookery inbox` get the
  filter. `ack(conn, identity, ids, *, from_inflight=False)` holds the logic of `cmd_ack` (`cli_rookery_session.py:371`):
  only `pending` rows to the caller move to `done`. `rookery ack` keeps `from_inflight=False`, so its output
  does not change (`inflight` counts as already read, `cli_rookery_session.py:392-394`). HTTP `/ack` passes
  `from_inflight=True`: pending and inflight `legacy` rows of the named node go to `done`. HTTP `/claim`
  moves only pending `legacy` rows of the named node to `inflight`.
- **Users**: MCP `check_inbox` and `await_message` call `unread` and return bodies. They never ack
  (FR-032). A new MCP tool `ack(ids)` calls `ack`. `read_mail.py` reads through `unread`. Mailroom `/inbox` reads through `unread_by_name`: mail to that name in any session, as today. Mailroom `/ack` uses `ack_by_name` on the same rows, because HTTP has no identity. The shared token holder is trusted (D24). The node runner keeps
  its raw-name reads.
- **Reader with no identity**: MCP stdio and `read_mail.py` read `legacy` rows to their raw node name.
  That is how an agent with no session sees mail that others send to its name.
- **Sender name** (FR-016): `unread` joins `identities` on the sender's agent ID within the session.
  A sender that is not an agent ID shows only for system senders and `legacy` rows.

## D12. Mention notices

- **Decision**: the mention scan (`postmaster.py:~498-540`) selects only `status='pending'` rows. It
  resolves `@name` with `identities` in the row's `session_id`. It sends the notice with
  `system_send` in that session. The text is `MENTION: <sender> named you in mail to <recipient>, topic
  <topic>. You cannot read that mail.` The body slice `[:140]` goes.
- **Rationale**: FR-017, FR-018 and SC-004.

## D13. Panes

- **Decision**: `terminal_node.run()` types the notice from `postmaster._notice` (`postmaster.py:102`).
  It stops calling `R.claim` and `R.ack` (`terminal_node.py:70, 74`). `terminal_node` has no state file today. It uses the postmaster pushed-id set
  (`postmaster._load_pushed` at `postmaster.py:166`, `_write_pushed` at `:380`). So one mail gets one notice in total
  across `terminal_node` and `wake_identities`. The reply hint at `terminal_node.py:61` names
  `rookery send`.
- **Rationale**: FR-019, FR-020 and SC-006. `_notice` is already capped at 400 characters and strips
  pane control text.

## D14. Node runner

- **Decision**: the node runner sends `MAILTO:`, the REJECTED notice and the auto-reply through
  `submit()` with path `node-runner`. Its principal is `principal_from_env` for `--node-id` in
  `ROOKERY_SESSION`, else none (the mail goes to `legacy`). The auto-reply (`node_runner.py:327`) skips
  system senders (`rookery.SYSTEM_SENDERS`) (H15). The node keeps its raw-name reads
  (`R.fetch_undelivered`): a node is not an identity (D22 removed). `screen()` keeps its ack of a rejected
  mail. That ack is the node's own read of its own mail.

## D15. Logs

- **Decision**: add stream `gate` to `turn_block._LOG_FILES` (`turn_block.py:21`):
  `<root>/logs/gate.jsonl`, with the same rotation (1 MB, 3 files). Each record has `path`, `principal`,
  `sender`, `asked_sender`, `recipient`, `session`, `outcome`, `reason`, `warning`, `msg_id` and
  `decided_by` (FR-030). The gate
  log path is `<ROOKERY_ROOT>/logs/gate.jsonl`, else `~/.rookery/logs/gate.jsonl`. `turn_block.project_root`
  (`turn_block.py:46`) needs a project name when `ROOKERY_ROOT` is unset. The mailroom and the shared MCP
  server have none, and `log_event` swallows errors (`turn_block.py:93`). A log write failure prints one line to
  stderr. It still never raises.
- **Rationale**: one file holds every gate decision, so an agent can read one stream.

## D16. Unmigrated sender report

Removed 2026-10-23. A sender with no session now sends, so no report is needed (FR-028 removed).

## D17. Exit codes for local paths

- **Decision**: `send_mail.py` uses the codes of `rookery send` (`cli_hook.py:606`): 0 sent, 1 not sent
  (quarantined or held), 2 refused or bad input, 4 database busy. A sender with no session exits 0
  (D6). A gate failure exits non-zero and stores nothing (FR-007).

## D18. Live deployment

- **Fact**: `rookery-mcp.service` and the mailroom run from `~/Documents/code/rookery`, on
  `feat/held-quarantine`, 58 commits behind master (2026-10-21).
- **Decision**: this feature does not deploy. The quickstart lists the steps. The user decides when to
  move that checkout to master.

## D19. Performance

- **Decision**: no new target. The gate adds lookups on keyed columns only: `identities` by
  `(session_id, name)` and `loop_counts` by primary key. The spec 001
  target (a hook in under 1 s on 10,000 rows) stays. `test_hooks.HookTiming` checks it.

## D20. Route table and token scopes

Removed 2026-10-23. The mailroom has one shared token, so it has no scopes and no route table (FR-034 removed).

## D21. Federation: the relay token

Removed 2026-10-23. Nothing forges a sender now, so `relay.py` needs no special token (FR-035 removed). It posts as today.
`relay.py:91` `UPDATE inbox SET recipient` stays. It re-addresses local mail and is not a send. The AST scan allow-lists it (D4).

`relay.py:102-105` posts the sender `<orig>@<self>` to the peer `/send`. The sender pattern allows one `@<peer>` part, so this mail passes the gate. `relay.py` does not change.

## D22. Nodes are identities

Removed 2026-10-23. A node name does not need an identity, because no check needs one (FR-036 removed). The
node runner, the terminal node and the postmaster wake keep their raw-name reads.

## D23. Loop reset for paths with no hook

- **Evidence**: FR-006 extends spec 001 FR-039. Its only reset is a human prompt (`cli_hook.py:436`). MCP, HTTP
  and node agents may have no hook.
- **Decision**: add `rookery loop reset [NAME] [--json]`. It is a human command. It resets the counters of the
  pairs of NAME in the current session and logs to the gate log. A held loop mail leaves the hold with the
  existing `mail_screen.py release` (`mail_screen.py:65`). The log record of a loop-held send and
  `rookery inbox` of the SENDER do not change: no bounce, as in FR-039.

## D24. Local identity is bookkeeping, not security

- **Decision**: on one machine where the user owns every agent, a local identity check is not security. Any
  process can set `ROOKERY_SESSION` or `ROOKERY_NODE`, or open the database file. So Rookery drops the rules
  that exist only to police local names: per-identity tokens, the sender-mismatch refusal, the quarantine for a
  sender with no session, token scopes, the relay token and nodes as identities (D5 to D7, D20 to D22). The gate still
  refuses a system name and an invalid name. It records both the asked sender and the resolved principal.
- **Rationale**: the rules added friction for agents and gave no real protection. The one real security
  boundary is the HTTP mailroom when it binds beyond localhost. That boundary keeps its shared token, its
  local bind by default and its explicit opt-ins (D8).
- **Trade-off**: on the local machine one agent can send under the name of another agent. The gate log
  records the asked sender and the principal, so the user can see it. Strong local checks need RQ-2.9, a
  later phase.
- **Keeps**: one gate for every path, the screening, the loop limits, the quarantine, the log, reads that
  never mark mail read, notices with no body, and `rookery loop reset` (D23).
## Out of scope (recorded)

- Splitting `ROOKERY_NODE` (legacy MCP node ID and 0.7 identity). Clarify left it for a later feature.
- MCP `roster` lists all nodes. It is not a send or a read of mail. After `_register` goes, `roster()` lists only registered nodes.
- The A2A agent card stays built from `nodes` (`mailroom_server.py:44`). It is not a send or a read of mail.
- A2A push URLs are chosen by the caller, so a push can reach an inside host (SSRF risk). A later phase must limit push URLs. This feature does not change A2A task reads.
- `rookery identity show` ignores a `herdr-pane` binding (spec, Also found). It is a separate bug.
