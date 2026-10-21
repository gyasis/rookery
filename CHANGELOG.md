# Changelog

## Unreleased

### Fixed

- A turn that is killed in the middle no longer leaves an agent `busy` forever. The postmaster now treats a `busy` state as idle for the push when its last sign of life is older than `busy_stale_s` (default 1800 seconds), and logs `busy-stale` once. The last sign of life is the later of `since` and the modification time of the transcript file. The prompt hook stores that path from its `transcript_path` input in `state/turn.json`, so a long turn that still writes is not stale. A bad `since` falls back to the modification time of `turn.json`. The session-start hook resets the state to `idle` only for `startup`, `clear` or no source, and logs `state-reset` when it replaced `busy`. It keeps `busy` for `compact` and `resume`. If the identity has a `herdr-pane` binding (the first one only) and `herdr` is on `PATH`, the postmaster also asks herdr (2 seconds at most). The turn must look busy for 60 seconds, and the pane must show `idle` or `done` on two readings at least 10 seconds apart. The postmaster then pushes and logs `busy-corrected`. Set `herdr_probe` to `false` to turn the probe off. Rookery does not need herdr for this. The probe uses a new plugin point, `plugins.status_probe`.

### Added

- Push gate. The postmaster now wakes an idle agent only for mail that needs action: kinds `ask`, `handoff`, `recruit` and `decline`, bounces, and an `answer` to an open `ask` of the recipient. Mail of kind `status`, other answers, and mail an agent sends to itself wait for the next turn. Plain mail with no `kind` still pushes.
- Loop breaker. Rookery holds the next mail with reason `loop` and logs `loop-held` when a counter reaches its limit with no human prompt in between. One counter is for one task between two agents (`loop_limit`, default 10). One is for all tasks between the same two agents (`loop_pair_limit`, default 30). One is for bounces to one agent. A prompt that is not a postmaster push resets the counters. In the Stop hook and `rookery turn`, the block stays in `outbox/failed/` and the next prompt notice names it. The breaker covers the block path only, not `rookery_mcp`, `send_mail` or `mailroom_server`. The database gets one new table, `loop_counts`.
- `rookery init` now merges a `permissions.allow` list into `.claude/settings.json`. The list has 10 rules for the `rookery` mail commands, `herdr pane read` and `herdr agent` control. It has no `send-keys` or `send-text` rule, so an agent cannot type into a shell pane. It only pre-approves these commands. It blocks nothing else. Init keeps your existing `permissions` entries and does not add a rule that is in `deny` or `ask`. The `--json` output has a new `settings.permissions` object.

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
