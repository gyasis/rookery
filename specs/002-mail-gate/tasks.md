---
type: Tasks
task: 002-mail-gate
updated: 2026-10-23
revision: 3
---

# Tasks: every Rookery send goes through one gate

**Input**: the design documents in `specs/002-mail-gate/`.
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/gate.md](contracts/gate.md) and [quickstart.md](quickstart.md).

**Tests**: MANDATORY (constitution II). Each live FR and each live SC gets the test that proves it, in this PR
(see the Requirement Trace in plan.md). Removed IDs need no test. Use fixtures, not mocks. Each test uses
a temporary database and a temporary Rookery root through `test_hooks.Base`. Each task writes its test first,
sees it fail, then writes the code. No task leaves a failing test.

**Revision 3 (2026-10-23)**: Restructured for dev-kid waves. Every task owns its tests and its code, and ends
with a `Verify:` command. The tasks that only wrote tests now sit inside the code tasks. The path matrix is split by path.
T050 and T051 are manual and moved to the last section.

**Revision 2 (2026-10-23)**: Removed the tasks for the token table, the token CLI, scopes and the route table.
Also removed: the relay token, managed nodes as identities, the unmigrated report, the `no-session` bounce,
the forged-sender tests and A2A scoping. Task IDs are not renumbered, so gaps are normal.

**Layout**: a flat Python repo. All paths start at the repo root. Use the standard library only.

## Format: `- [ ] T### [P?] [Story?] Description`

- **[P]**: the task can run in parallel. It uses a different file and has no open dependency in its phase.
- **[Story]**: the user story of the task (US1 to US6).
- **Needs T0xx first.**: an explicit dependency on a task in another file.
- **Verify:** the exact command that proves the task. It runs the tests of the task and the suites that the task could break.

## Rules for every task

- Read the research decision that the task names (Dn in [research.md](research.md)) before you write code.
- A task that writes a test also writes the code that makes it pass. No task writes a test for code that a later task builds.
  No `skip` or `expectedFailure` hides missing code. Skip a test only when an outside tool such as `ste-lint` is missing.
- Only `mail_gate.py` and `rookery.py` call `rookery.send()` (D3, D4). Every other caller uses
  `mail_gate.submit()` or `mail_gate.system_send()`.
- No send path calls `rookery_identity.resolve(create=True)` (FR-011).
- The gate never falls back to an ungated send. When it fails, it returns `db-busy` or `send-failed`. It
  stores nothing (FR-007).
- `security.get_policy()` keeps one policy for the process (`security.py:188`). A test that sets
  `ROOKERY_SECURITY` must set `security._POLICY = None` before and after the test.
- Do not change `test_turn_block.py`, `test_send.py`, `test_hooks.py`, `test_outbox.py` or `test_wake.py`
  (FR-002). The one allowed exception is in T021 (plan.md, "FR-002 and FR-005 conflict").
- Each text that an agent or the user reads must pass `ste-lint --max-score 2.0 --glossary ste-glossary.txt`
  (FR-031). This includes bounce notes, refusal texts, notices, warnings and errors.
- The existing suites are `test_turn_block test_send test_hooks test_outbox test_wake`. Each `Verify:` of a code task runs them.

---

## Phase 1: Setup

**Purpose**: the fixtures that later phases need.

- [x] T001 [P] Create `fixtures/policy_deny.py` with class `DenyPolicy(security.SecurityPolicy)`. Its `inspect_inbound` denies a body that contains `DENY-ME`, with reason `fixture-deny`. Else it returns `ALLOW`. Tests load it with `ROOKERY_SECURITY=fixtures.policy_deny:DenyPolicy` (`security.py:194`). Read `security.py` for the verdict type first.
  Verify: `ROOKERY_SECURITY=fixtures.policy_deny:DenyPolicy python3 -c "import security; print(type(security.get_policy()).__name__)"` (it prints DenyPolicy)
- [x] T002 [P] Create `fixtures/bin/tmux`, an executable script like `fixtures/bin/herdr`. It appends its arguments as one JSON line to `$ROOKERY_FIXTURE_LOG` and exits 0. Tests put `fixtures/bin` first on `PATH` (US5).
  Verify: `L=$(mktemp) && ROOKERY_FIXTURE_LOG=$L fixtures/bin/tmux a b && python3 -c "import json,sys; print(json.loads(open(sys.argv[1]).readline()))" $L`

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: the gate module and the block path on the gate. Every story calls
`mail_gate.submit()`.

- [x] T006 [P] Edit `turn_block.py`. Add stream `gate` → `<ROOKERY_ROOT>/logs/gate.jsonl` to `_LOG_FILES` (`turn_block.py:21`). With `ROOKERY_ROOT` unset, the path is `~/.rookery/logs/gate.jsonl`, because `project_root` (`turn_block.py:46`) needs a project name that the mailroom and the shared MCP server do not have. A failed log write prints one line to stderr and never raises (`log_event` swallows errors, `turn_block.py:93`) (D15). Write `test_gate_log.py` first. Cases: with `ROOKERY_ROOT` set, one record lands in `<root>/logs/gate.jsonl`. With `ROOKERY_ROOT` unset and a temporary `HOME`, it lands in `~/.rookery/logs/gate.jsonl`. A log folder that Rookery cannot write to prints one line to stderr and does not raise.
  Verify: `python3 -m unittest test_gate_log test_turn_block test_hooks -v`
- [x] T007 [P] Edit `rookery_identity.py`. `new_identity` (`rookery_identity.py:218`) refuses a name in `rookery.SYSTEM_SENDERS` with a `ValueError` that names the reason. `rookery identity new rookery` exits 2 with that text (D3). Add the cases to `test_identity.py` first: `new_identity(conn, sid, "rookery")` raises, and the CLI exits 2 with the reason.
  Verify: `python3 -m unittest test_identity test_hooks test_turn_block -v`
- [x] T008 Create `mail_gate.py` with `submit`, `system_send`, `quarantine_malformed` and `principal_from_env` (contract, D1, D3, D5, D6, D9, D10). Needs T001, T006 and T007 first. `principal_from_env` returns a principal only when the session is `open` and the identity exists. Copy these parts from `turn_block.py`. Do not rewrite them. They are the recipient lookup and `off-team`/`wrong-recipient` of `_dispatch_block` (`turn_block.py:602`), `_quarantine_recipient` (`:451`), `_hold_and_quarantine` (`:463`, now `quarantine_malformed`) and `_send_counted` (`:522`). T009 deletes the old copies. Add the sender rules and the order of checks of data-model.md: the sender is the asked sender, else the principal's agent ID, else the raw name. Refuse a system name and a bad name. With no principal, store `pending` in `legacy` with the `no-session` warning, and skip the recipient checks. Add the `pre_store` hook and the `gate` log. Call `security.get_policy().inspect_inbound` before `BEGIN IMMEDIATE`. `decided_by` defaults to `gate`. `mail_gate.py` imports `rookery_identity` inside functions only, as `turn_block.py` does. Write `test_gate.py` first (base: `test_hooks.Base`, sessions `alpha` and `beta`). Call `mail_gate.submit()` directly. Check each step of the order of checks:
  (1) An asked sender that is a system name (`rookery`) gives `refused`/`system-name`, with 0 new rows. (2) An asked sender `x; rm -rf ~` gives `refused`/`bad-input`, with 0 new rows. (3) A principal and an asked sender that differs from its identity give `sent`. The stored sender is the asked sender. The log record has `asked_sender` and `principal`, and they differ. (3a) No principal and asked `old-node` give a `sent` row, `pending`, in `legacy`, with the recipient as given. The log record has `warning: "no-session"`. There is no quarantine row and no bounce. With no principal, an unknown recipient and a recipient only in `beta` also give `sent` in `legacy`. The gate does not rewrite the recipient. (4) With a principal in `alpha`, a name only in `beta` gives `off-team`. (5) With a principal in `alpha`, an unknown name gives `wrong-recipient`. The gate stores a recipient name that matches an identity in `alpha` as that agent ID. A `pre_store` that returns `("held", "handoff-pending")` stores the mail held. (6) With `DenyPolicy`, the body `DENY-ME` gives `injection-suspect` and a bounce, for a sender with a session and for a sender with no session (the bounce goes to `legacy`). A policy that sleeps in `inspect_inbound` does not block a write on a second connection (screening runs before `BEGIN IMMEDIATE`). (7) Path `mcp-stdio`: send between two identities until the loop limit (`turn_block.loop_limits()`). The next send is `loop-held` with reason `loop` and no bounce. (8) A valid peer gives `sent`, `pending`, with the sender's `session_id`. Also check `principal_from_env`. A session with no identity, and a session that is not open, each give `None` and a `legacy` row with the `no-session` warning. A name in two sessions resolves in the sender's session only. A locked database: hold `BEGIN IMMEDIATE` on a second connection. The call gives `db-busy` and 0 new rows. Each call writes one record to the gate log with the fields of data-model.md. System senders: `system_send(conn, "alice", ...)` raises `ValueError`. Each of `postmaster`, `relay` and `rookery` sends through `system_send` and stores one row. The sender `alice@peer1` is `sent`, and `alice@@x` is `refused`/`bad-input`.
  Verify: `python3 -m unittest test_gate test_gate_log test_identity test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T009 Edit `turn_block._dispatch_block` (`turn_block.py:602`). Needs T008 first. It calls `mail_gate.submit(path="block", decided_by="turn-block-parser", pre_store=<handoff check>, ...)` after `validate()`. Keep `validate()`, the remote check, `write_handoff` and the park step. Pass the `handoff-pending` check as `pre_store`. A block that does not parse calls `mail_gate.quarantine_malformed` (`turn_block.py:~475-477`) and never `rookery.send`. Map the gate outcomes to the old outcomes. Delete the code that T008 copied into `mail_gate.py`. The existing suites must pass with no change to their files (FR-002). This task writes no new test: `test_gate` and the existing suites are the proof.
  Verify: `python3 -m unittest test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`

**Checkpoint**: the gate exists and the block path uses it. All suites are green.

---

## Phase 3: User Story 1 - No send path can skip the gate (P1) 🎯 MVP

**Goal**: every send path calls `submit()`.

**Independent Test**: each of the 9 paths gives a result for an unknown name, a peer in another session and a valid peer. A path with a session gives `wrong-recipient`, `off-team` and `sent`. SC-001 is 9 paths x 3 tries = 27 cases. The 27 cases are the sum over five test files. T012: block 3 and `rookery send` 3. T014: MCP 6. T015: `send_mail.py` 3. T016: mailroom `POST /send` 3, `mailctl.py send` 3 and A2A 3. T018: node runner 3.

- [x] T012 [US1] Needs T009 first. Create `gate_matrix.py`, a helper that has no tests of its own. It holds the 3 tries (unknown name, peer only in `beta`, valid peer) and the asserts. A path with a session (`ROOKERY_SESSION` set to `alpha`) gives `wrong-recipient`, `off-team` and `sent`. A path with no session gives `sent` in `legacy` for all 3 tries, with the `no-session` warning in the log. The helper also checks that the number of rows in `identities` does not change (FR-011). Write `test_paths_local.py` with the drivers for 2 paths: the block (`rookery turn`) and `rookery send`. Both already go through the gate after T009. That is 2 x 3 = 6 cases.
  Verify: `python3 -m unittest test_paths_local test_gate test_turn_block test_send test_hooks -v`
- [x] T014 [P] [US1] Needs T012 first. Edit `rookery_mcp.py`. The `send` tool calls `submit`. In stdio mode the principal is `principal_from_env`. In shared HTTP mode there is no principal. The header `x-rookery-node` is the asked sender, as today. Remove the `_register` call on first use (`rookery_mcp.py:93-104`) (D5). Write `test_paths_mcp.py` first, with the drivers for 2 paths. MCP stdio: import `rookery_mcp` with `ROOKERY_SESSION` and `ROOKERY_NODE` set, then call the `send` function. MCP shared HTTP: call `send` with a context that carries the `x-rookery-node` header. That is 2 x 3 = 6 cases, through the shared helper of T012.
  Verify: `python3 -m unittest test_paths_mcp test_paths_local test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T015 [P] [US1] Needs T012 first. Edit `send_mail.py`. The principal is `principal_from_env`, or none. Pass `--from` as `asked_sender`. Keep the raw name `human` when there is no principal and no `--from`. Use the exit codes of D17. Print the outcome and, for a bounce, its `note` and `suggest`. Write `test_paths_send_mail.py` first, with the driver for 1 path (subprocess): 3 cases through the shared helper of T012. Add one case for FR-007: hold `BEGIN IMMEDIATE` on a second connection, run the script, and assert exit 4 and 0 new rows.
  Verify: `python3 -m unittest test_paths_send_mail test_paths_local test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T016 [US1] Needs T012 first. Edit `mailroom_server.py`. `POST /send` (`:427-429`) calls `submit(path="mailroom", principal=None)`. The body is `{recipient, body, topic?, sender?}`. `recipient` is the key that the callers send (line 428, the mailctl script, MCP remote mode and the relay). Accept `to` as an alias. The body `sender` is the asked sender, as today. The answer is `{id, outcome, reason}`: `id` is the stored row, or null. `GET /inbox` stays as today (`fetch_undelivered`). T033 changes it. A2A `message/send` (`:212`) and `message/stream` (`:353`) call `submit(path="a2a", principal=None)`. The asked sender is `a2a-<metadata.from>`, as today. Remove their inline `inspect_inbound` calls (`:209`, `:341`). Call `policy.authorize` as today (D7, D9). Write `test_paths_mailroom.py` first. Start `mailroom_server.py` on a free local port with `--host 127.0.0.1 --token T`. Drivers for 3 paths: `POST /send`, the send command of the mailctl script (subprocess against that server) and A2A `message/send`. All 3 paths have no session, so each of the 3 tries gives `sent` in `legacy` with the `no-session` warning. That is 3 x 3 = 9 cases. T016 does not edit the mailctl script.
  Verify: `python3 -m unittest test_paths_mailroom test_paths_local test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T018 [P] [US1] Needs T012 first. Edit `node_runner.py`. `handle_directives` (`MAILTO:`, `:213-217`), the REJECTED notice in `screen` (`:254`) and the auto-reply (`:327`) call `submit(path="node-runner")`. The principal is `principal_from_env` for `--node-id` (`node_runner.py:341`) in `ROOKERY_SESSION`, else none. The node keeps its raw-name reads (`R.fetch_undelivered`, `:271`). The auto-reply skips system senders (`rookery.SYSTEM_SENDERS`) (D14). Write `test_paths_node_runner.py` first. The driver calls `node_runner.handle_directives` with a `MAILTO:` line, with `ROOKERY_SESSION` set to `alpha`: 3 cases through the shared helper of T012. Add one case: the auto-reply to a system sender stores no row.
  Verify: `python3 -m unittest test_paths_node_runner test_paths_local test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T019 [P] [US1] Needs T008 first. Edit `postmaster.py` and `relay.py`. `CRED_GRANTED` (`postmaster.py:519`), `MENTION` (`:536`) and the dead-letter (`relay.py:76`) call `system_send` with an explicit `session_id`. The `credential_requests` table has no session column, so `CRED_GRANTED` uses `legacy`. `MENTION` uses the session of the row that caused the mail, and `legacy` only when that row has none. Do not change how `relay.py` posts to the peer `/send` (D21). Move the `CRED_GRANTED` code into a function `postmaster._grant_mail(conn, request_row)` that the loop calls, so a test can call it. Write `test_system_mail.py` first. Cases: `_grant_mail` stores one `pending` row from `postmaster` in `legacy`. `relay._dead_letter` stores one row from `relay` to the sender, in the session of the dead row. A `MENTION` mail is stored in the session of its cause row.
  Verify: `python3 -m unittest test_system_mail test_gate test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T021 [US1] Needs T007, T008, T009, T014, T015, T016, T018 and T019 first. Edit `rookery.send()` (`rookery.py:223`). `session_id` becomes a keyword with no default. Remove `or "legacy"` (`:231`). Add `session_id="legacy"` to the call at `test_identity.py:181` and to the 12 calls in `test_held_quarantine.py` (plan.md, "FR-002 and FR-005 conflict"). Add a case to `test_gate.py`: `rookery.send(conn, "a", "b", "x")` raises `TypeError` (FR-005). Every caller is already converted by the tasks that this task needs, so no suite fails.
  Verify: `python3 -m unittest test_gate test_identity test_held_quarantine test_notices test_paths_local test_paths_mcp test_paths_send_mail test_paths_mailroom test_paths_node_runner test_system_mail test_turn_block test_send test_hooks test_outbox test_wake -v`
- [x] T013 [US1] Needs T009, T014, T015, T016, T018, T019 and T021 first. Create `test_gate_scan.py`. It holds an `ast` scan of every non-test `.py` file at the repo root and in `plugin_herdr/`. The scan resolves aliases (`import rookery as X`, `from rookery import send [as s]`). It fails for a call to `send` of the `rookery` module outside `mail_gate.py` and `rookery.py`. It fails on `getattr(<rookery alias>, "send")`. It fails for the text `INSERT INTO inbox` outside `rookery.py`. It fails for a call to `rookery.quarantine` outside `mail_gate.py`, `rookery.py` and `mail_screen.py` (a human tool on an existing row). It allow-lists `UPDATE inbox SET recipient` in `relay.py` only. It fails for a call to `mail_gate.system_send` outside `postmaster.py`, `relay.py`, `rookery.py` and `mail_gate.py`. It has an import scan: `mail_gate.py` does not import herdr code (FR-021) (D4, FR-004, SC-002). The scan passes by construction: the tasks that this task needs convert every caller (see the list of callers in the Notes). The text scan of agent hints is in T035 and T045.
  Verify: `python3 -m unittest test_gate_scan -v`

**Checkpoint**: no path skips the gate. `python3 -m unittest test_paths_local test_paths_mcp test_paths_send_mail test_paths_mailroom test_paths_node_runner test_gate_scan` runs the 27 cases of SC-001 and the scans.

---

## Phase 4: User Story 2 - System names and bad names are refused (P2)

**Goal**: the gate refuses a system name and an invalid name as the sender. The log shows who asked.

**Independent Test**: Rookery refuses each such try and stores 0 rows. A mismatch of sender and principal goes through and shows in the log.

- [x] T024 [US2] Needs T016 first. Edit `mailroom_server.py`. Map `refused` to 403 (`system-name`) or 400 (`bad-input`) with the contract body, and a missing or bad token to 401. In A2A, map the outcomes as the contract table says: `sent` gives a result with task state `submitted`. `quarantined` and `loop-held` give a result with state `rejected` and the reason in the status message. `refused` gives -32602. A policy `authorize` deny gives -32003. `db-busy` gives -32006. `send-failed` gives -32603. `message/stream` (`:341-353`) follows the same rules. Write `test_mailroom_gate.py` first (auth cases). With a token set, no token and a bad token get 401 with 0 new rows. A call that names `rookery` as the sender gets 403 and the body `{"error": "system-name", "detail": ...}`. A call with sender `x; rm` gets 400 `bad-input`. A call with another agent's name as sender gets 200, and the log record has `asked_sender`. The gate log has one record for each refusal. One case for each A2A mapping above.
  Verify: `python3 -m unittest test_mailroom_gate test_paths_mailroom test_gate -v`
- [x] T025a [US2] Needs T024 first. Edit `mailctl.py`. An `HTTPError` prints the `error` and `detail` of the server and exits 2 (400 or 403) or 1. `--from` stays as today. Label `mailctl loop` and `mailctl inbox` legacy in the help text. Write `test_mailctl.py` first. It starts the server as in T016. A send with `--from rookery` exits 2 and prints `system-name`. A send with a bad token exits 1. `--help` holds the word legacy.
  Verify: `python3 -m unittest test_mailctl test_mailroom_gate test_paths_mailroom -v`

---

## Phase 5: User Story 3 - The mailroom refuses outside calls by default (P1)

**Goal**: a local bind and the shared token by default, for `rookery serve` too. Each opt-in is explicit and prints a warning.

**Independent Test**: the start rules in the contract.

- [x] T028 [US3] Needs T024 first (both edit `mailroom_server.py`). Edit `mailroom_server.py`. Add `mailroom_server.check_start(args)`, the one function that holds the start rules and warnings of the contract. `main()` (`mailroom_server.py:463`) calls it. `--host` defaults to `127.0.0.1`. Add `--allow-public` and `--allow-no-auth`. Keep `--token` and `$ROOKERY_TOKEN` (`:482-484`). Write `test_mailroom_start.py` first (subprocess, free port, 10 s limit) for `mailroom_server.py`. No token and no option: exit 2, and the output names `--token` and `--allow-no-auth`. `--host 0.0.0.0` without `--allow-public`: exit 2. With a token and no host option, the server listens on `127.0.0.1` only. A connect to the host's LAN address fails. Skip this assert when the host has no LAN address. `--allow-no-auth` with no token and `--allow-public` each print one `WARNING:` line. A token with `--allow-no-auth` keeps auth on and prints no warning. In no-auth mode, a call with no token works (D8). With a token set, routes outside `security.PUBLIC_PATHS` answer 401 with no token, and `POST /join` and `GET /join/<id>` work with no token. `/approve` and `/invite` behave as today.
  Verify: `python3 -m unittest test_mailroom_start test_mailroom_gate test_paths_mailroom test_mailctl -v`
- [x] T028a [US3] Needs T028 first (it calls `check_start` and adds to `test_mailroom_start.py`). Edit `cli_serve.py` and `rookery_cli.py`. `cli_serve.cmd_serve` (`cli_serve.py:88-101`) calls `mailroom_server.check_start`. In `rookery_cli.py:41-42`, `rookery serve --host` defaults to `127.0.0.1`, and serve keeps `--token`. Add `--allow-public` and `--allow-no-auth` to serve. Add the same start cases of T028 to `test_mailroom_start.py` for `rookery serve`.
  Verify: `python3 -m unittest test_mailroom_start test_init test_hooks -v`

---

## Phase 6: User Story 4 - Mail stays inside its session (P2)

**Goal**: reads, counts and mention notices use only the reader's session, plus `legacy` mail to the reader.

**Independent Test**: mail in two sessions. No agent sees mail, a count or an excerpt of the other
session (SC-004). Legacy mail to the reader shows (SC-010).

- [x] T031 [P] [US4] Needs T008 and T009 first. Add `unread` and `unread_legacy` to `mail_gate.py` (D11). `unread` returns mail of the identity's session to its agent ID. It also returns `legacy` mail to its name or agent ID. It joins `identities` within the session for the sender name. `unread_legacy(raw_name)` returns `legacy` mail to the raw name. Edit `turn_block.py`: `turn_block.unread` (`turn_block.py:787`) calls `mail_gate.unread`. Write `test_reads.py` first. Insert with `rookery.send` in the test only: pending mail of `alpha` to an `ag-…` ID of `beta`, and pending mail of `beta` to that ID. `mail_gate.unread` for the `beta` identity returns only the `beta` row. `turn_block.unread_summary` counts only that row, and the prompt hook notice names only that sender (FR-015). A sender shows with its identity name (FR-016). `legacy` mail to the name or the agent ID of `beta`'s identity shows in its unread list and count. `legacy` mail to another name does not (FR-015, SC-010). `unread_legacy` returns `legacy` mail to the raw name only. The read paths `rookery inbox` and `rookery peek` are already proven read-only by line 369 of the hooks test and line 143 of the notices test (SC-009). Cite them in a comment of the test.
  Verify: `python3 -m unittest test_reads test_gate test_notices test_hooks test_turn_block test_send test_outbox test_wake -v`
- [x] T032 [P] [US4] Needs T008 and T019 first. Edit `postmaster.py` (`:~498-540`). Move the mention scan into a function `postmaster._mention_pass(conn, last_id, nodes)` that the loop calls, so a test can call it. Select only `status='pending'`. Resolve `@name` in `identities` of the row's `session_id`. Send the notice text of the contract with `system_send` in that session. Pass `<topic>` and `<sender>` through `turn_block.notice_field` (cap 64). Remove the body slice `[:140]` (D12). Write `test_mentions.py` first (FR-017, FR-018). Held mail with `@C` gives C no notice. Quarantined mail with `@C` gives C no notice. Pending mail with `@C`, C in the same session, gives one notice with no part of the body. Pending mail with `@C`, C in another session, gives no notice.
  Verify: `python3 -m unittest test_mentions test_system_mail test_wake test_hooks test_turn_block -v`
- [x] T033 [US4] Needs T031, T025a and T028a first (it edits `mail_gate.py` and `mailroom_server.py`). Add `unread_by_name` and `ack_by_name` to `mail_gate.py` (D11). `ack_by_name(conn, name, ids, *, from_inflight=True)` moves only the pending and inflight rows that `unread_by_name(name)` would return to `done`. It lists other IDs as skipped. It needs no identity. Edit `mailroom_server.py`. `/inbox` returns `unread_by_name(node, with_body=True)`. Its rows hold `recipient`, `status` and `body`, so `mailctl.py inbox` and `loop` keep working. `/claim` moves the pending rows of `unread_by_name` to `inflight`. `/ack` calls `ack_by_name`. `/thread` stays as today. Make A2A `tasks/get` read-only if it is not. Write the cases first in `test_mailroom_gate.py`. `GET /inbox?node=N` returns pending rows to N in `legacy` and in a session, and rows to the agent ID of an identity named N. It returns no row to another name, and it changes no status. `/ack` and `/claim` with the ID of a row to another name change nothing and list it as skipped. A `mailctl.py loop` case: `/claim` then `/ack` leaves 0 rows `inflight`. SC-009 read-path cases: `GET /inbox`, `mailctl.py inbox` and A2A `tasks/get` each run, then a status snapshot shows 0 rows changed.
  Verify: `python3 -m unittest test_mailroom_gate test_reads test_paths_mailroom test_mailctl test_mailroom_start -v`

---

## Phase 7: User Story 5 - A pane gets a notice, never the mail body (P2)

**Goal**: a terminal node types a notice and does not mark mail read.

**Independent Test**: the pane text holds no part of the body, and the mail stays `pending` (SC-006).

- [ ] T035 [P] [US5] Needs T002 first. Edit `terminal_node.py`. `run()` types `postmaster._notice` text only (`:72`). Remove the calls to `R.claim` (`:70`) and `R.ack` (`:74`). The node keeps its raw-name reads (`:66`). `terminal_node` has no state file today. Use the postmaster pushed-id set (`_load_pushed` and `_write_pushed` of the postmaster), so one mail gets one notice across `terminal_node` and `wake_identities`. The reply hint (`:61`) and the usage text in the docstring (`:6`) name `rookery send`, not the send_mail script (D13, FR-029). Write `test_pane.py` first. Put `fixtures/bin` first on `PATH`. Run one pass of `terminal_node.run` for a mail with body `SECRET-BODY-123`. The tmux line in `$ROOKERY_FIXTURE_LOG` holds the sender, the topic and `rookery inbox`, and does not hold `SECRET-BODY-123`. The mail is still `pending`. A second pass types no second notice for the same mail. When both `terminal_node.run` and `postmaster.wake_identities` run for the same mail, the pane gets one notice in total. Text scan: the source of `terminal_node.py` does not name the send_mail script.
  Verify: `python3 -m unittest test_pane test_wake test_hooks test_notices -v`

---

## Phase 8: User Story 6 - Existing users keep working (P2)

**Goal**: MCP, `mailctl.py` and `send_mail.py` work with or without an identity. An agent with no
session sends to `legacy`. A read never marks mail read.

**Independent Test**: on a copy of the live database, no row count changes. An old node name sends, and
the mail is `pending` in `legacy` (SC-007, US6 scenario 2).

- [ ] T038 [US6] Needs T033 and T021 first (`mail_gate.py` and `test_gate.py`). Add `ack(conn, identity, ids, *, from_inflight=False)` to `mail_gate.py` with the logic of `cmd_ack` (`cli_rookery_session.py:371`), plus the session filter. Also add `ack_legacy(conn, raw_name, ids)`. It moves only the pending rows that `unread_legacy(raw_name)` would return to `done`, and lists other IDs as skipped. Edit `cli_rookery_session.py`: `cmd_ack` calls `ack` with `from_inflight=False`. Its output and exit codes do not change (`inflight` counts as already read, `cli_rookery_session.py:392-394`). Write the cases first in `test_gate.py`. `ack` marks only the rows of the identity's session. `ack` with the ID of another identity's mail lists it as skipped. `ack_legacy` marks `legacy` mail to the raw name and lists a session row as skipped.
  Verify: `python3 -m unittest test_gate test_reads test_hooks test_notices test_turn_block test_send test_outbox test_wake -v`
- [ ] T039 [US6] Needs T038 and T014 first. Edit `rookery_mcp.py`. `check_inbox` and `await_message` call `unread(with_body=True)` and never ack. Remove `_drain` consumption (`rookery_mcp.py:120`). `check_inbox` ends with `Mark read: ack([<ids>])`. Add the tool `ack(ids)`. A reader with no identity uses `unread_legacy(NODE)`, and its `ack` tool calls `ack_legacy(NODE)` (D11). Write `test_mcp_gate.py` first. With two unread mails, `check_inbox` returns both, two times. `ack([first_id])` marks one read, and the next `check_inbox` returns only the other. `await_message` returns the mail and changes nothing. `ack` with the ID of another identity's mail lists it as skipped. In stdio mode with no session, `send` stores the mail in `legacy` and the outcome is `sent`. `check_inbox` returns `legacy` mail to the node name through `unread_legacy`. With no session, `ack([id])` marks that mail read, and `ack` with the ID of a session row lists it as skipped (FR-032, US6 scenario 4). SC-009: a status snapshot after `check_inbox` and `await_message` shows 0 rows changed.
  Verify: `python3 -m unittest test_mcp_gate test_paths_mcp test_gate -v`
- [ ] T040 [US6] Needs T038 first. Edit `read_mail.py`. Read through `unread`, or `unread_legacy` with no identity. `--ack` calls `mail_gate.ack`, or `ack_legacy` with no identity. `--since`, `--all` and `--json` stay (`read_mail.py:38-46`). `--all` shows done mail of the same identity and session only. Write `test_read_mail.py` first (subprocess). A read without `--ack` leaves a status snapshot unchanged (SC-009). `--ack` marks the rows read. A session reader does not see mail of another session. A reader with no identity sees `legacy` mail to its name. `--all` shows done mail of the same session only.
  Verify: `python3 -m unittest test_read_mail test_reads test_gate -v`
- [ ] T041a [US6] Needs T038 first (`cli_rookery_session.py` and `test_gate.py`). Add `rookery loop reset [NAME] [--json]` to `cli_rookery_session.py` (D23, FR-037). It resets the loop counters of the pairs of NAME in the current session, and logs one `loop-reset` record to the gate log. Write a case first in `test_gate.py`: after a loop hold, `loop reset` clears the counters, `mail_screen.py release` (`mail_screen.py:65`) frees the held mail, and the next send is `sent`. The sender gets no bounce, and `rookery inbox` of the sender does not change.
  Verify: `python3 -m unittest test_gate test_hooks test_turn_block test_send test_outbox test_wake -v`

---

## Phase 9: Polish and cross-cutting concerns

- [ ] T042 [P] Add `mail_gate.py` to `_PY_MODULES` in `bootstrap.py`. Needs T013, T025a, T028a, T033, T035, T039, T040 and T041a first (the build must hold all new code). `python3 bootstrap.py` only defines functions, so build the zipapp with `bootstrap.build_pyz` and run it from a folder outside the repo. The command `rookery loop --help` must work.
  Verify: `D=$(mktemp -d) && python3 -c "import bootstrap,sys; bootstrap.build_pyz(sys.argv[1])" $D/rookery.pyz && (cd / && python3 $D/rookery.pyz loop --help)`
- [ ] T043 [P] Needs T041a first. Add a `CHANGELOG.md` entry. It names these changes. The gate runs on every path. A sender with no session delivers to `legacy` with a log warning. Name the `127.0.0.1` default (also for `rookery serve`), the start rules (token or `--allow-no-auth`, `--allow-public`), MCP `check_inbox` no longer marks mail read, the MCP `ack` tool, and `rookery loop reset`. Say that the mailroom keeps its one shared token (`$ROOKERY_TOKEN`). Say that `roster()` lists only registered nodes, because `_register` goes. Label `mailctl.py`, `send_mail.py` and `read_mail.py` legacy (FR-029).
  Verify: `ste-lint CHANGELOG.md --max-score 2.0 --glossary ste-glossary.txt && grep -c "127.0.0.1" CHANGELOG.md`
- [ ] T044 [P] Needs T041a first. Edit `README.md`, `docs/QUICKSTART.md`, `docs/COOKBOOK.md`, `docs/STORIES.md` and `docs/NODE_BRIEFING.md` at the lines in the 2026-10-22 inventory. Label each `mailctl.py`, `send_mail.py` and `read_mail.py` use as legacy and give the `rookery` command. Add the new mailroom start rules where the docs start the server (FR-029). Also edit the scripts that start the mailroom with no token or on a public host (T028 found them): `a2a_demo.sh`, `demo_invite.sh`, `demo_multihost.sh`, `demo_hardened.sh`, `demo_federation.sh`, `demo_a2a_push.sh`, `demo_signed_card.sh` and the hint in `gen_cert.sh`. Each start of the mailroom gives `--token` (or `--allow-no-auth` for a local demo), and `--allow-public` with a public `--host`. Write `test_demo_starts.py` first. For each `*.sh` file, join the lines that end in a backslash. For each command that runs `mailroom_server.py` (not a `pgrep`, `pkill` or `echo` line, except the `gen_cert.sh` hint), assert it has `--token`, `ROOKERY_TOKEN` or `--allow-no-auth`, and `--allow-public` when `--host` is not local. Also run `bash -n` on each script.
  Verify: `python3 -m unittest test_demo_starts -v && for f in README.md docs/QUICKSTART.md docs/COOKBOOK.md docs/STORIES.md docs/NODE_BRIEFING.md; do ste-lint $f --max-score 2.0 --glossary ste-glossary.txt || exit 1; done`
- [ ] T045 [P] Needs T041a first. Edit `skills/rookery/SKILL.md`: the MCP `ack` tool and the legacy labels (`mailctl.py`, `send_mail.py`, `read_mail.py`). Edit `cli_up.py:223` to name `rookery send`. Edit `plugin_herdr/cli.py:201-210` (agent briefings that name `mailctl.py` and `send_mail.py`) to name `rookery send` (FR-029). Write `test_text_hints.py` first. A text scan fails when the source of `cli_up.py` or `plugin_herdr/cli.py` tells agents to use `send_mail.py` or `rookery.send(` (D4, FR-004, FR-029, SC-002).
  Verify: `python3 -m unittest test_text_hints test_init -v && ste-lint skills/rookery/SKILL.md --max-score 2.0 --glossary ste-glossary.txt`
- [ ] T046 [P] Needs T008, T025a, T028a, T032, T035 and T041a first (the texts must exist). Add a text-lint case to `test_gate.py` (FR-031). Write these texts into one temporary file: every bounce note (`wrong-recipient`, `off-team`), each refusal text (`system-name`, `bad-input`), the mention notice and the pane notice. Add the `rookery loop reset` text and the `mailctl` error message. Add the mailroom start messages and warnings. Run `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt`. Skip the case with a message when `ste-lint` is not on `PATH`. Fix each text that fails, in its own source file, in this task.
  Verify: `python3 -m unittest test_gate -v`
- [ ] T047 Needs T043, T044 and T045 first. Lint the prose (quickstart §5): `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `tasks.md`, `contracts/gate.md`, `CHANGELOG.md`, `README.md`, the four `docs/` files and `skills/rookery/SKILL.md`. Fix each failure.
  Verify: `for f in specs/002-mail-gate/*.md specs/002-mail-gate/contracts/*.md CHANGELOG.md README.md docs/QUICKSTART.md docs/COOKBOOK.md docs/STORIES.md docs/NODE_BRIEFING.md skills/rookery/SKILL.md; do ste-lint $f --max-score 2.0 --glossary ste-glossary.txt || exit 1; done`
- [ ] T048 Needs T042 first. Prove that opening a COPY of the live database changes nothing (quickstart §1, FR-026, SC-007). It edits no file. Run the commands of quickstart §1. Record the row counts before and after in the PR, and say that the schema is the same.
  Verify: `cp ~/Documents/code/rookery/rookery.db /tmp/rookery-copy.db && sqlite3 /tmp/rookery-copy.db "SELECT COUNT(*) FROM inbox" > /tmp/count-before.txt && sqlite3 /tmp/rookery-copy.db ".schema" > /tmp/schema-before.txt && ROOKERY_DB=/tmp/rookery-copy.db python3 -c "import rookery as R; R.connect()" && sqlite3 /tmp/rookery-copy.db "SELECT COUNT(*) FROM inbox" > /tmp/count-after.txt && sqlite3 /tmp/rookery-copy.db ".schema" > /tmp/schema-after.txt && diff /tmp/count-before.txt /tmp/count-after.txt && diff /tmp/schema-before.txt /tmp/schema-after.txt && ROOKERY_DB=/tmp/rookery-copy.db python3 -m unittest test_sessions_migration -v`
- [ ] T049 Needs T042, T046, T047 and T048 first. Run the full suite of quickstart §2 (`specs/002-mail-gate/quickstart.md`), plus `test_held_quarantine`, `test_identity` and `test_notices`. All cases must pass. `test_hooks.HookTiming` must stay under 1 s for each hook.
  Verify: `python3 -m unittest test_turn_block test_init test_hooks test_identity test_outbox test_wake test_send test_notices test_held_quarantine test_sessions_migration test_gate_log test_gate test_gate_scan test_paths_local test_paths_mcp test_paths_send_mail test_paths_mailroom test_paths_node_runner test_system_mail test_mailroom_gate test_mailroom_start test_mailctl test_mcp_gate test_reads test_mentions test_pane test_read_mail test_text_hints -v`

---

## After the waves (manual, with the user)

dev-kid does not schedule these steps. No test can check them. Do them only when the user says so.

1. Run an adversarial review of the whole diff (`/adversarial-audit`, files: `mail_gate.py` and every file in plan.md). Try each bypass by hand. A direct `sqlite3` insert is out of scope (spec Assumptions). Every Python path must hit the gate. For each confirmed finding, write a test, then fix it.
2. After step 1 and the last task above, do the live deployment of quickstart §6 (`specs/002-mail-gate/quickstart.md`), with the user. Do not mark it done from fixtures.

---

## Dependencies and execution order

### Phase dependencies

- **Phase 1 (Setup)**: no dependency.
- **Phase 2 (Foundational)**: T006 and T007 need nothing. T008 needs T001, T006 and T007. T009 needs T008. It blocks every story, because each story calls `submit()`.
- **US1 (Phase 3)**: T012 needs T009. T014, T015, T016 and T018 need T012. T019 needs T008. T021 needs T009, T014, T015, T016, T018 and T019. T013 needs T021 and the same tasks.
- **US2 (Phase 4)**: T024 needs T016. T025a needs T024.
- **US3 (Phase 5)**: T028 needs T024 (both edit `mailroom_server.py`). T028a needs T028. It does not need US2 for its own tests.
- **US4 (Phase 6)**: T031 needs T008 and T009. T032 needs T008 and T019. T033 needs T031, T025a and T028a.
- **US5 (Phase 7)**: T035 needs T002 only.
- **US6 (Phase 8)**: T038 needs T033 and T021. T039 needs T038 and T014. T040 and T041a need T038.
- **Phase 9 (Polish)**: T042 needs the last task of each story. T043 to T045 need T041a. T046 needs the text tasks. T047 needs T043 to T045. T048 needs T042. T049 needs T042, T046, T047 and T048.

### Story order

```text
T001 T002 T006 T007 ── T008 ── T009 ─┬─ T012 ─┬─ T014 ┐
                                     │        ├─ T015 ┤
          T019 (after T008) ─────────┼────────┼─ T016 ┼─ T021 ── T013
                                     │        └─ T018 ┘     │
                                     │                      │
        T016 ── T024 ──┬─ T025a ─┐                          │
                       └─ T028 ── T028a ─┤                  │
        T031 (T008, T009) ───────────────┴─ T033 ── T038 ──┬─ T039 (T014)
        T032 (T008, T019)                                  ├─ T040
        T035 (T002)                                        └─ T041a
        Polish: T042 T043 T044 T045 T046 ── T047, T048 ── T049
```

### Tasks that edit the same file run in this order

The order is enforced by the `Needs` phrases. A task in this table that only reads a file is not listed for it.

| File | Tasks, in this order |
|---|---|
| `mail_gate.py` | T008, T031, T033, T038 |
| `turn_block.py` | T006, T009, T031 |
| `rookery.py` | T021 |
| `rookery_identity.py` | T007 |
| `mailroom_server.py` | T016, T024, T028, T033 |
| `rookery_mcp.py` | T014, T039 |
| `postmaster.py` | T019, T032 |
| `test_gate.py` | T008, T021, T038, T041a, T046 |
| `test_identity.py` | T007, T021 |
| `test_mailroom_gate.py` | T024, T033 |
| `test_mailroom_start.py` | T028, T028a |
| `cli_rookery_session.py` | T038, T041a |
| `bootstrap.py` | T042 |
| `terminal_node.py` | T035 |
| `mailctl.py` | T025a |
| `node_runner.py` | T018 |
| `send_mail.py` | T015 |
| `read_mail.py` | T040 |
| `cli_serve.py` and `rookery_cli.py` | T028a |

Every other test file has one owner: `test_gate_log.py` (T006), `test_paths_local.py` and `gate_matrix.py` (T012), `test_paths_mcp.py` (T014), `test_paths_send_mail.py` (T015), `test_paths_mailroom.py` (T016), `test_paths_node_runner.py` (T018), `test_system_mail.py` (T019), `test_gate_scan.py` (T013), `test_mailctl.py` (T025a), `test_reads.py` (T031), `test_mentions.py` (T032), `test_pane.py` (T035), `test_mcp_gate.py` (T039), `test_read_mail.py` (T040), `test_text_hints.py` (T045).

## Parallel opportunities

- Wave 1: T001, T002, T006, T007 run together. T035 can start after T002.
- Then T008, then T009.
- After T009: T012. After T008: T019 and T031 (T031 also needs T009). T032 follows T019.
- After T012: T014, T015, T016 and T018 run together. They use different files.
- After all of them: T021, then T013.
- T024 follows T016. T025a and T028 both follow T024 and use different files. T028a follows T028. T033 waits for T031, T025a and T028a.
- After T033: T038. Then T039, T040 and T041a run together (different files).
- Polish: T042, T043, T044, T045 and T046 run together. T047 and T048 follow. T049 is last.

### Parallel example: after T012

```text
Task: "Edit rookery_mcp.py, test_paths_mcp.py (T014)"
Task: "Edit send_mail.py, test_paths_send_mail.py (T015)"
Task: "Edit mailroom_server.py, test_paths_mailroom.py (T016)"
Task: "Edit node_runner.py, test_paths_node_runner.py (T018)"
```

## Implementation strategy

### MVP first

1. Do Phase 1 and Phase 2. The block path runs on the gate, and its tests pass unchanged.
2. Do US1. Then run the checkpoint command. No path skips the gate.
3. Do US3 (P1). It completes the P1 scope: a closed mailroom. US2 is P2.

### Incremental delivery

1. Foundational: `mail_gate.py` and the block path on the gate.
2. US1: every path on the gate (the MVP).
3. US2: refusals of system names and bad names.
4. US3: the mailroom start rules.
5. US4: session reads and mention notices.
6. US5: pane notices.
7. US6: MCP read-only reads, `ack` and the move path.
8. Polish: the zipapp, the docs, the lint, the check on a copy and the full run. Then the manual steps.

## Notes

- Commit after each phase with a Conventional Commit message.
- A task that a fixture cannot check says UNTESTED and names the check that is still owed.
- Callers of the bare insert found by `grep -n "rookery.send\|R.send\|\.send(\|INSERT INTO inbox\|quarantine(" *.py plugin_herdr/*.py` on 2026-10-23, and the task that converts each:
  `mailroom_server.py` (3 calls) in T016. `node_runner.py` (3 calls) in T018. `rookery_mcp.py` in T014. `send_mail.py` in T015.
  `postmaster.py` (2 calls) and `relay.py` in T019. `turn_block.py` (2 calls) moves to `mail_gate.py` in T008 and goes in T009.
  `mail_screen.py` calls `rookery.quarantine` on an existing row and stays allowed. `rookery.py` is the one allowed insert.
  `cli_up.py:223` only names `rookery.send(` in text, and T045 fixes it. No caller is uncovered.
