---
type: Contract
task: 002-mail-gate
updated: 2026-10-23
revision: 3
---

# Contract: the mail gate, its commands and its HTTP surface

Spec 001 `contracts/cli-and-hooks.md` stays valid. This file adds to it. The exit codes of the send paths
(`rookery send`, `send_mail.py`, `rookery turn`):

- 0: sent or done.
- 1: not sent (quarantined or held), or an ack skipped an ID.
- 2: refused or bad input.
- 4: database busy.

A send path never exits 3. A missing session is not an error: the mail goes to `legacy` and the exit code
is 0. Exit 3 stays only for the spec 001 commands that need a current session. D17 lists the same codes.

Each command that changes state prints one JSON object with `--json` (constitution VI).

## Python API — `mail_gate.py`

```python
submit(conn, *, path, principal, raw_sender, to, body, topic="", task=None,
       asked_sender=None, root=None, decided_by="gate", pre_store=None) -> dict
system_send(conn, sender, recipient, body, *, topic=None, session_id, root=None) -> int
quarantine_malformed(conn, ctx, recipient, body, topic, reason, note, suggest=None) -> int  # moved _hold_and_quarantine
principal_from_env(conn, env, root) -> (session, identity) | None
unread(conn, identity, *, with_body=False) -> list
unread_legacy(conn, raw_name, *, with_body=False) -> list             # reader with no identity
unread_by_name(conn, name, *, with_body=False) -> list               # HTTP reader: any session, by name
ack(conn, identity, ids, *, from_inflight=False) -> {"ok": [...], "already": [...], "skipped": [...]}
ack_legacy(conn, raw_name, ids) -> {"ok": [...], "already": [...], "skipped": [...]}  # reader with no identity
ack_by_name(conn, name, ids, *, from_inflight=True) -> {"ok": [...], "already": [...], "skipped": [...]}  # HTTP /ack
```

- `submit` never raises for a mail problem. It returns `{"outcome", "reason", "warning", "msg_id", "bounce_id"}`.
  The outcomes and the order of checks are in [data-model.md](../data-model.md).
- HTTP paths have no principal. They pass `principal=None`. The sender comes from the body, the A2A
  metadata or the header, as the caller asked.
- `pre_store` gets the resolved recipient row after check 6 (screening). It returns `None` or `("held", reason)`.
  `turn_block` passes the `handoff-pending` check as `pre_store`.
- `quarantine_malformed` holds a block that does not parse, quarantines it as `malformed` and sends the
  bounce. `turn_block` calls it and never calls `rookery.send`.
- `system_send` raises `ValueError` for a sender that is not in `rookery.SYSTEM_SENDERS`.
- `principal_from_env` returns `(session, identity)` only when the session is `open` and the identity
  exists. A session with no identity, or a session that is not open, gives `None`. Then `submit` stores the
  mail in `legacy` with the warning `no-session`.
- `unread` returns mail of the identity's session to its agent ID, and `legacy` mail to its name or agent
  ID (FR-015). It never changes a row.
- `ack` changes `pending` rows where `recipient` is the identity's agent ID and `session_id` is its
  session. With `from_inflight=True` it also moves `inflight` rows to `done`. `rookery ack` (`cmd_ack`)
  keeps `from_inflight=False`, so its output does not change. Spec 001 counts `inflight` as already read
  (`cli_rookery_session.py:392-394`).
- `rookery.send()` keeps its name. `session_id` becomes a keyword with no default. Only `mail_gate.py` and
  `rookery.py` call it (`test_gate_scan.py`).

## CLI — new `rookery` subcommand

```text
rookery loop reset [NAME] [--json]
```

| Command | Output | Exit |
|---|---|---|
| `loop reset` | `Reset <n> loop counters for <name>.` It resets the pairs of NAME in the current session and logs to the gate log | 0. 2 for an unknown NAME |

The command takes no sender flag (FR-011). A human runs `loop reset`. A held loop mail leaves the hold with
`mail_screen.py release`. A loop-held send sends no bounce, so the output of `rookery inbox` for the sender
does not change.

## CLI — changed scripts

| Script | Change |
|---|---|
| `send_mail.py --to TO --body BODY [--topic T] [--from NAME]` | principal = `principal_from_env`, else none. `--from` is the asked sender and becomes the sender. A system name or a bad name exits 2. The default raw name `human` stays. `--to` and `--body` stay (`send_mail.py:9-13`). Exit codes as above |
| `read_mail.py --node NAME [--since N] [--all] [--json] [--ack]` | reads `unread` for the identity of NAME. With no identity it reads `unread_legacy(NAME)`. `--ack` calls `ack`. `--all` shows done mail of the same identity and session only. Legacy |
| `mailctl.py send --url U --to TO --body B [--from NAME]` | `--from` stays as today. An HTTP error prints the `error` and `detail` of the server and exits 2 (400 or 403) or 1. Legacy |
| `mailctl.py inbox`, `mailctl.py loop` | read mail to `--node`, as `GET /inbox` does. Legacy |

## MCP tools — `rookery_mcp.py`

| Tool | Behaviour |
|---|---|
| `send(to, body, topic="")` | `submit(path="mcp-stdio" or "mcp-http")`. Returns the outcome text. A refusal returns the reason |
| `check_inbox()` | `unread(with_body=True)` for the caller. Changes nothing. Ends with `Mark read: ack([<ids>])` |
| `await_message(timeout=30)` | polls `unread` until mail exists or the time ends. Changes nothing |
| `ack(ids)` | NEW. `mail_gate.ack`, or `ack_legacy(NODE)` for a reader with no identity. Returns the counts of `ok`, `already` and `skipped` |
| `roster()` | no change in code. It lists only registered nodes, because `_register` goes |

- **stdio**: the caller is `principal_from_env` of the server process. With no identity, reads use
  `unread_legacy(NODE)` and sends go to `legacy` with the warning `no-session`.
- **shared HTTP**: there is no principal. The sender is the node name that the header `x-rookery-node`
  gives, as today. The mail goes to `legacy`. There is no change to the MCP client config.
- **remote mode** (`ROOKERY_URL`): `send` posts `/send` with `ROOKERY_TOKEN`, as now.

## HTTP — `mailroom_server.py` and `rookery serve`

### Start

```text
mailroom_server.py [--host 127.0.0.1] [--port 8765] [--token T] [--allow-public] [--allow-no-auth] [TLS options]
rookery serve [--host 127.0.0.1] [--port 8765] [--token T] [--allow-public] [--allow-no-auth]
```

The one shared token comes from `--token`, else from `$ROOKERY_TOKEN`. This is the token that exists today.
`mailroom_server.check_start(args)` holds the rules. `main()` and `cli_serve.cmd_serve` both call it.

| Condition | Result |
|---|---|
| no token, no `--allow-no-auth` | exit 2. Prints the two choices: give a token (`--token`, `$ROOKERY_TOKEN`) or `--allow-no-auth` |
| `--host` not `127.0.0.1`, `::1` or `localhost`, no `--allow-public` | exit 2 |
| `--allow-public` | starts. One `WARNING:` line names the risk |
| `--allow-no-auth` and no token | starts with auth off. One `WARNING:` line names the risk |
| a token and `--allow-no-auth` | auth stays on. The flag has no effect and prints no warning |

### Routes

The routes in `security.PUBLIC_PATHS` need no token, as today: `GET /health`, the agent card, `POST /join`
and `GET /join/<id>`. Every other route needs the shared token, unless auth is off. `/approve`, `/invite`,
`/join`, `relay.py` and A2A `tasks/get` behave as today.

### Calls

| Call | Rule | Answers |
|---|---|---|
| no token or bad token on a route that needs one | stores nothing | 401 |
| `POST /send {recipient, body, topic?, sender?}` | `submit(path="mailroom", principal=None)`. `recipient` is the key that the callers send (`mailroom_server.py:428`, `mailctl.py`, `rookery_mcp.py` remote mode, `relay.py`). `to` works as an alias. `sender` is the asked sender, as today | 200 `{id, outcome, reason}`. `id` is the stored row, or null. 403 for `refused`/`system-name`. 400 for `refused`/`bad-input`. 503 for `db-busy`. 500 for `send-failed`. The error body is `{"error": "<reason>", "detail": "<text>"}` |
| `GET /inbox?node=N` | `unread_by_name(N, with_body=True)`: pending mail in any session to N, or to the agent ID of an identity named N, as `fetch_undelivered` does today. It changes nothing | 200 with rows that hold `recipient`, `status` and `body` |
| `GET /thread` | as today (`fetch_thread`) | 200 |
| `POST /claim` | moves the pending rows of `unread_by_name` to `inflight` | 200 |
| `POST /ack {ids}` | `ack_by_name`: pending and inflight rows of the named node (the same rows as `/inbox`) go to `done` | 200 with `ok`, `already`, `skipped` |

### A2A outcomes

The sender is the asked sender, as today: `a2a-<from>` from `metadata.from`. The gate passes
`principal=None`. `message/stream` (`:341-353`) follows the same rules. `tasks/get`, `message/stream` task
reads and the push watcher behave as today.

| Gate outcome | JSON-RPC answer |
|---|---|
| `sent` | result with task state `submitted` |
| `quarantined` or `loop-held` | result with state `rejected`. The status message holds the reason |
| `refused` (`system-name` or `bad-input`) | error -32602 |
| policy `authorize` deny | error -32003 |
| `db-busy` | error -32006 |
| `send-failed` | error -32603 |

## Notices (FR-018, FR-019)

```text
MENTION: <sender> named you in mail to <recipient>, topic <topic>. You cannot read that mail.
[rookery] new mail for <name> from <sender> about <topic>. Run: rookery inbox
```

The pane notice is `postmaster._notice` (no body, 400 characters at most). Both notices pass `<topic>`
and `<sender>` through `turn_block.notice_field` (`turn_block.py:826`, cap 64 characters). Both texts pass
`ste-lint` (FR-031).

## Log warning for a sender with no session (FR-014)

There is no bounce and no quarantine. The mail is stored `pending` in `legacy` and the outcome is `sent`.
The gate log record has `warning: "no-session"`:

```json
{"path": "send-mail", "principal": null, "sender": "old-node", "asked_sender": null,
 "recipient": "bob", "session": "legacy", "outcome": "sent", "reason": null,
 "warning": "no-session", "decided_by": "gate"}
```
