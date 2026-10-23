# Changelog

## Unreleased

### Fixed

- A turn that stops in the middle no longer leaves an agent `busy` forever. The postmaster now treats a `busy` state as idle for the push when its last sign of life is older than `busy_stale_s` (default 1800 seconds), and logs `busy-stale` once. The last sign of life is the later of `since` and the modification time of the transcript file. The prompt hook stores that path from its `transcript_path` input in `state/turn.json`, so a long turn that still writes is not stale. A bad `since` falls back to the modification time of `turn.json`. The session-start hook resets the state to `idle` only for `startup`, `clear` or no source, and logs `state-reset` when it replaced `busy`. It keeps `busy` for `compact` and `resume`. If the identity has a `herdr-pane` binding (the first one only) and the postmaster runs inside a herdr pane (`HERDR_ENV=1`) or with `ROOKERY_HERDR=force`, the postmaster also asks herdr (2 seconds at most). The turn must look busy for 60 seconds, and the pane must show `idle` or `done` on two readings at least 10 seconds apart in the same turn. The postmaster then pushes and logs `busy-corrected`. Set `herdr_probe` to `false` to turn the probe off. Rookery does not need herdr for this. The probe uses a new plugin point, `plugins.status_probe`.

### Removed

- The `rk` script (`~/.local/bin/rk`) is gone. We retired it. It was never part of this repository. It took the sender from `--as`, registered any name, and sent with no gate. Use `rookery send`, `rookery inbox`, `rookery peek`, `rookery ack` and `rookery identity list`. A stub at `~/.local/bin/rk` now prints these commands and exits 2.

### Added

- `rookery send TO SAY [--task T] [--kind K] [--status S] [--thread X] [--read PATH]`. It builds one block and sends it through the same checks as the Stop hook and `rookery turn`: recipient inside your session, `wrong-recipient` and `off-team` quarantine with a bounce, and the loop breaker. The sender is your resolved identity. There is no `--as` or `--from`, and it never makes a new identity. Exit 0 sent, 1 not sent, 2 bad input (nothing stored), 3 no session, 4 database busy. It logs with adapter `send`.
- Push gate. The postmaster now wakes an idle agent only for mail that needs action: kinds `ask`, `handoff`, `recruit` and `decline`, bounces, and an `answer` to an open `ask` of the recipient. Mail of kind `status`, other answers, and mail an agent sends to itself wait for the next turn. Plain mail with no `kind` still pushes.
- Loop breaker. Rookery holds the next mail with reason `loop` and logs `loop-held` when a counter reaches its limit with no human prompt in between. One counter is for one task between two agents (`loop_limit`, default 10). One is for all tasks between the same two agents (`loop_pair_limit`, default 30). One is for bounces to one agent. A prompt that is not a postmaster push resets the counters. In the Stop hook and `rookery turn`, the block stays in `outbox/failed/` and the next prompt notice names it. The breaker covers the block path only, not `rookery_mcp`, `send_mail` or `mailroom_server`. The database gets one new table, `loop_counts`.
- `rookery init` now merges a `permissions.allow` list into `.claude/settings.json`. The list has 10 rules for the `rookery` mail commands, `herdr pane read` and `herdr agent` control. It has no `send-keys` or `send-text` rule, so an agent cannot type into a shell pane. It only pre-approves these commands. It blocks nothing else. Init keeps your existing `permissions` entries and does not add a rule that is in `deny` or `ask`. The `--json` output has a new `settings.permissions` object.

### Changed

- Mail gate (feature 002). Every send path now goes through one gate: the block, `rookery send`, MCP over stdio and HTTP, `send_mail.py`, the mailroom `/send`, `mailctl`, A2A and the node runner. System mail goes through `system_send`.
- A sender with no session still delivers. The mail goes to `legacy` and the gate logs a warning. Rookery does not quarantine it.
- The gate refuses a bad sender name. It also refuses a system sender name in any case. The gate writes each decision to `logs/gate.jsonl`.
- The mailroom and `rookery serve` now bind `127.0.0.1` by default. They need the shared token (`--token` or `$ROOKERY_TOKEN`) or the flag `--allow-no-auth`. A public host also needs `--allow-public`. The mailroom keeps its one shared token.
- `/send` now returns an HTTP error when the gate refuses: 403, 400, 503 or 500.
- A read never marks mail read. MCP `check_inbox` no longer marks mail read. MCP users must call the new `ack` tool. `rookery ack` now also clears legacy mail addressed to the name of the agent.
- A mention notice appears only for pending mail in the same session that has no body. A pane notice never carries the body.
- `rookery loop reset` is new. It resets the loop counters.
- `roster()` now lists only registered nodes. MCP no longer registers nodes.
- `rookery.send()` now needs a `session_id`. It is an internal function.
- `mailctl.py`, `send_mail.py` and `read_mail.py` are legacy. Use the `rookery` commands.

## 0.7.0 - 2026-10-15

### Added

- `rookery init` command. It writes a Rookery section into `CLAUDE.md` and merges three hooks into `.claude/settings.json`. If you run it again, it changes nothing.
- The `rookery turn` block. An agent ends a turn with a block, and the Stop hook sends the mail in it.
- Sessions, identities and bindings. Rookery makes the session ID. A binding links a pane or a herdr workspace to a session.
- Session-start hook and prompt hook. The session-start hook shows who the agent is and the newest `HANDOFF.md`. The prompt hook shows unread mail.
- `rookery inbox`, `rookery peek` and `rookery ack` commands. `inbox` and `peek` show only the mail of the current agent and change nothing. `inbox` ends with the line `Mark read: rookery ack ID ...`. Run `rookery ack` to mark mail read.
- `rookery turn` command. It reads the text of one turn from stdin and sends the block in it. Use it for agents that have no hooks.
- Postmaster idle push. The postmaster wakes an agent when new mail arrives and the agent is idle.
- Postmaster outbox scan. It sends each file from the outbox drop-box once.
- The postmaster now runs without `--nodes`.

### Changed

- The database migration is additive. It adds the tables `rookery_sessions`, `identities` and `bindings`, and the column `inbox.session_id`.
- The functions `send()`, `quarantine()`, `claim()` and `ack()` have new keyword arguments. Old calls still work.
- This phase includes fixes from adversarial audits. Examples: the block parser rejects lone surrogates. `rookery init` rejects a settings file with duplicate keys or NaN values. `rookery turn` rejects input that is not valid UTF-8. The identity link rejects `previous_id` cycles.

### Notes

- Phase 1 limits:
  - The wake for herdr and tmux comes from the configuration file.
  - `TMUX_PANE` is not tested.
  - There are no remote mailroom adapters. A block sent with `ROOKERY_URL` goes to `outbox/failed`.
