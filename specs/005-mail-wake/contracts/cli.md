# CLI contract: Event-driven mail wake

Exit codes follow the Rookery rule: 0 done, 1 refused or failed, 2 bad input, 3 no session, 4 database busy.

## `rookery wake --session SESSION --agent AGENT [--json]`

Runs one wake for one identity under the lock (research R4, R5). The event path starts it detached. A person can
also run it.

- `SESSION` is a session name or ID. `AGENT` is an identity name or ID in that session.
- Output: one line, or with `--json` one object: `{"session", "agent_id", "outcome", "mail_id", "rounds"}`.
  `outcome` is one of `pushed`, `nothing`, `wake-busy`, `wake-failed`, `no-idle-state`, `no-wake-binding`,
  `no-root`, `busy`.
- Exit 0 for every outcome except bad input (2), no such session or agent (3) and database busy (4). A wake that
  could not type is not an error of the command: it is the outcome `wake-failed`.
- The lock holder checks again only after `pushed` or `nothing`, at most 3 rounds.
- Writes only `postmaster/pushed/`, `postmaster/backoff/` and the log.

## `rookery check [--follow] [--json]`

For a machine with `ROOKERY_URL`, `ROOKERY_TOKEN` and `ROOKERY_NODE` set (research R8).

- With no flag: one check. `GET ROOKERY_URL/inbox?node=ROOKERY_NODE`. It types a notice into its own pane for mail
  that the push gate pushes and that `NODE.json` does not list.
- `--follow`: write `NODE.follow`, then start the detached follower and return at once. The follower makes at most
  5 checks, 300 seconds apart, and stops at the first notice.
- Output with `--json`: `{"node", "outcome", "count", "checks"}`. `outcome` is one of `pushed`, `nothing`,
  `check-failed`, `no-wake-binding`, `follower-started`, `follower-running`.
- Exit 2 when `ROOKERY_URL` or `ROOKERY_NODE` is missing. A mailroom that does not answer is the outcome
  `check-failed`, exit 0, so that a hook never fails.

## `rookery up TEAM_FILE [--session NAME] [--no-launch] [--json]`

Changed (research R10, R7):

- `--session NAME` wins over `session` in the team file.
- With no name from either, `up` makes a three-word name and prints it.
- An open session with that name resumes, as today: the same session, identities and worktrees, and only panes
  that are not alive start.
- After `up` builds the rows, it starts `rookery sweep` for the session detached. When a sweep already runs, the new one
  exits with `sweep-busy`.
- New fields in `--json`: `"session_name"`, `"name_made": true|false`, `"sweep": "started"|"running"`.

## `rookery sweep --session SESSION [--root ROOT]` and `postmaster.py --session SESSION [--root ROOT] [--poll S]`

The sweep of one session (research R7, R9). Both entries run `postmaster.run_session_sweep`. `rookery up` starts the
subcommand, because it also works from the zipapp.

- Each round: `wake_identities` for the identities of the session, then, when screening is on,
  `screener.run(sessions=[SESSION], once=True)`.
- `--poll` default 300. The sweep raises a value below 300 to 300. One `NOTE:` line goes to stderr, and the log gets
  `poll-raised`.
- Exits 0 when the session is no longer `open`. Exits 0 with `sweep-busy` when another sweep holds the lock.
- With no `--session` and no `--nodes`: one sweep for every open session, with the same 300 second floor
  (old start commands, FR-017).
- `--nodes` (v1 mode) keeps its old behavior.

## `rookery screen run --session SESSION --once`

The screening event entry (research R6).

- Takes `postmaster/lock/screen-SESSION_ID.lock` without waiting. If another pass holds it, exit 0 with
  `screen-busy`.
- Runs `screener.run(sessions=[SESSION], once=True)`, releases the lock, checks again for mail with no decision,
  at most 3 rounds.
- `rookery screen run` with no `--once`: a loop as today, default `--poll` 300.

## Hooks

- **Stop hook**: after `_write_idle`, the hook looks at the pending mail of the agent. Mail that the push gate
  pushes and `pushed/` does not list starts `rookery wake` detached. With `ROOKERY_URL`, the hook starts
  `rookery check --follow` instead. The hook still never blocks and never prints.
- **MCP `send` with `ROOKERY_URL`**: after a send that succeeded, start `rookery check --follow`.
