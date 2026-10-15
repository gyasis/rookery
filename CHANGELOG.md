# Changelog

## 0.7.0 - 2026-10-15

### Added

- `rookery init` command. It writes a Rookery section into `CLAUDE.md` and merges three hooks into `.claude/settings.json`. If you run it again, it changes nothing.
- The `rookery turn` block. An agent ends a turn with a block, and the Stop hook sends the mail in it.
- Sessions, identities and bindings. Rookery makes the session ID. A binding links a pane or a herdr workspace to a session.
- Session-start hook and prompt hook. The session-start hook shows who the agent is and the newest `HANDOFF.md`. The prompt hook shows unread mail.
- `rookery inbox` and `rookery peek` commands. They show only the mail of the current agent.
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
