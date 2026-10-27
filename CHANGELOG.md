# Changelog

## 0.8.0 - 2026-10-27

Feature 005: the event-driven mail wake. An idle agent gets its mail at once. No process polls for it.

### Added

- The event wake. Each step that makes a mail `pending` starts one detached `rookery wake` for the recipient. The steps: the gate when it stores a mail, a release, an unquarantine, a requeue from the dead-letter queue, and a bounce. The screener is one more step, when it delivers a held mail. The send returns before the wake runs. A wake that fails never changes the send.
- The Stop hook wakes an agent whose turn ends with mail that needs action and that no notice announced.
- `rookery wake --session S --agent A [--root R] [--json]` runs one wake for one agent. It takes the lock `postmaster/lock/AGENT_ID.lock` without waiting. A second wake for the same agent logs `wake-busy` and exits. After a push, the holder reads the pending mail again, at most 3 rounds. Each wake writes one log line with its outcome. The outcomes: `pushed`, `nothing`, `busy`, `wake-busy`, `backoff-wait`, `wake-failed`, `no-idle-state`, `no-wake-binding`, `no-root`, `lock-failed` and `unsafe-name`. Exit 0 for every outcome, 2 for bad input, 3 for no session or agent, 4 for a busy database. The back-off record now lives in `postmaster/backoff/AGENT_ID.json`, so it lasts between wakes.
- The session sweep: `rookery sweep --session S [--root R]`, or `postmaster.py --session S`. It is the safety net for a lost event. Each round it scans the outbox drop-box and wakes the agents of the session that have mail with no notice. It also runs one screening pass when screening is on, or when a mail waits as `held` for screening. Then it sleeps 300 seconds (`sweep_s` in `~/.rookery/config.json`, never below 300). It calls no model. It ends when the session ends, when a new database file replaces the old one, or when the project root is gone. One sweep runs for each session (`postmaster/lock/sweep-SESSION_ID.lock`). A second one logs `sweep-busy` and exits.
- `rookery up` starts the sweep for its session after it builds the rows, also with `--no-launch`. The output has a `Sweep:` line, and `--json` has `"sweep"`: `started`, `running`, `off` or `failed`.
- `rookery up --session NAME`. The flag wins over `session` in the team file. With no name from either, `rookery up` makes a name of three words and prints it: `Session: NAME (name made)`, or `"name_made": true` with `--json`. A second run with the same name resumes the session. It keeps the mail, the identities and the worktrees, and starts only the panes that are not alive.
- Screening events. When screening is on for a session, the gate starts one detached `rookery screen run --session S --once` after it stores a mail. The pass takes `postmaster/lock/screen-SESSION_ID.lock`. A held lock prints `screen-busy`. After the pass it looks for mail with no decision, at most 3 rounds.
- `screener.enabled` in `~/.rookery/config.json` (default `false`) turns screening on for every session. The team file key `screener: true` turns it on for one session: `rookery up` writes the session binding `screener-enabled`. Screening is off by default because a shadow `t1` check calls the model. An update must not start model calls that nobody chose. A mail that a live check holds for screening always gets the pass.
- `rookery check [--follow] [--json]`: the remote check, for a machine with `ROOKERY_URL`, `ROOKERY_TOKEN` and `ROOKERY_NODE` set. It calls `GET /inbox?node=NODE` on the mailroom with the Bearer token. It refuses a redirect and reads at most 1 MB in 20 seconds. For mail that needs action, it types one notice into its own pane through the `wake` config. It records the announced IDs in `~/.rookery/remote/NODE.json`. A failed pane command sets a back-off from 30 to 600 seconds. `--follow` starts one detached follower for the node: 5 checks per trigger, 300 seconds apart, and it stops at the first notice. A trigger while the follower sleeps restarts its count and starts one detached check at once. The triggers: the Stop hook on a machine with `ROOKERY_URL`, and a successful send through the MCP tool. A remote agent with neither has no trigger.
- `ROOKERY_WAKE=off` in the environment kills every event spawn. No wake, no screening pass, no sweep from `rookery up`, and no remote check from a hook or the MCP tool start. `rookery wake`, `rookery sweep` and `rookery check` still run when you call them.

### Changed

- The postmaster loop without `--nodes` sleeps 300 seconds between rounds. It raises a `--poll` below 300 to 300. The postmaster prints one `NOTE:` line and logs `poll-raised` once. Old start commands keep working. `--nodes` keeps its poll.
- `rookery screen run` as a loop (without `--once`) sleeps 300 seconds between passes. The default was 2.
- `rookery up` refuses a team file whose `screener` key is not `true` or `false`.
- `postmaster.py`, `rookery_wake.py` and `rookery_remote.py` are in the zipapp.

### Fixed

- An idle agent got no notice, because no postmaster ran on the machine. The event wake and the sweep need no postmaster.
- A file dropped in `outbox/` was never delivered when no postmaster ran. The session sweep delivers it within one interval (300 seconds or less).

## 0.7.0 - 2026-10-26

This release holds features 001 to 004: `rookery init` and the turn block, the mail gate, `rookery up` and the screener chain.

### Added

- The screener chain. `rookery screen run` screens mail that has no decision. It runs rules for injection, duplicate asks, ask budget, threads and worker gates G1, G2, G3 and G5. Then it runs a small model (`t1`), a second opinion (`hunch`) and an optional command (`t2`). Every check starts in `shadow` mode and changes no mail.
- `rookery screen status`, `review`, `mark`, `report`, `export`, `mode`, `release`, `quarantine` and `escalate`.
- The 150 rule. `rookery screen mode CHECK live` refuses a model check with fewer than 150 compared decisions. `t1` also needs `screener.bands.t1`. Editing `config.json` by hand skips the rule.
- Live mode fails closed. When a live check cannot decide, the mail waits as `escalated`. The boss of the session (role `boss`) gets a notice, and the plugin `sink` event `escalated` fires.
- Threads. The first mail of a thread gets a review for the pair. A closed thread refuses new mail. `rookery thread close SESSION THREAD [--as AGENT]` closes one.
- The ask budget (`ask_budget` in the team file, else `screener.ask_budget`) and a hold for duplicate asks.
- The `screener` block in `~/.rookery/config.json`. The screener uses PromptChain when it imports and a standard-library copy when it does not.
- `rookery session new` with no name makes a name of three words.

- `rookery up TEAM_FILE [--no-launch] [--json]` builds a team from a JSON team file. It makes a session, an identity and a folder for each agent. It makes a git worktree for each writer and a port map in `ROOKERY_PORTS`. It starts each pane through configured steps (`launch`, `alive`). Agents that it starts use the CLI only for mail.
- `rookery finish NAME --message TEXT` shows the change, asks three times, then commits, pushes and deletes the worktree. It never deletes the branch.
- `rookery gc` lists stale worktree rows. It deletes nothing.
- `rookery regen` writes the generated views again from the database: `ROOKERY.md`, `_directory/`, `_teams/`, `_threads/` and `_mail/`.
- The hooks `rookery hook guard` and `rookery hook watch` are accident guards. Guard denies a write outside the folders of the agent. Watch warns about a shell command that wrote outside them.
- Claims: `claims` in the team file gives file patterns to an agent. A write to a claimed file warns the agent.

- `rookery send TO SAY [--task T] [--kind K] [--status S] [--thread X] [--read PATH]`. It builds one block and sends it through the same checks as the Stop hook and `rookery turn`: recipient inside your session, `wrong-recipient` and `off-team` quarantine with a bounce, and the loop breaker. The sender is your resolved identity. There is no `--as` or `--from`, and it never makes a new identity. Exit 0 sent, 1 not sent, 2 bad input (nothing stored), 3 no session, 4 database busy. It logs with adapter `send`.
- Push gate. The postmaster now wakes an idle agent only for mail that needs action: kinds `ask`, `handoff`, `recruit` and `decline`, bounces, and an `answer` to an open `ask` of the recipient. Mail of kind `status`, other answers, and mail an agent sends to itself wait for the next turn. Plain mail with no `kind` still pushes.
- Loop breaker. Rookery holds the next mail with reason `loop` and logs `loop-held` when a counter reaches its limit with no human prompt in between. One counter is for one task between two agents (`loop_limit`, default 10). One is for all tasks between the same two agents (`loop_pair_limit`, default 30). One is for bounces to one agent. A prompt that is not a postmaster push resets the counters. In the Stop hook and `rookery turn`, the block stays in `outbox/failed/` and the next prompt notice names it. The breaker covers the block path only, not `rookery_mcp`, `send_mail` or `mailroom_server`. The database gets one new table, `loop_counts`.
- `rookery init` now merges a `permissions.allow` list into `.claude/settings.json`. The list has 10 rules for the `rookery` mail commands, `herdr pane read` and `herdr agent` control. It has no `send-keys` or `send-text` rule, so an agent cannot type into a shell pane. It only pre-approves these commands. It blocks nothing else. Init keeps your existing `permissions` entries and does not add a rule that is in `deny` or `ask`. The `--json` output has a new `settings.permissions` object.

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

- The old `rookery up URL` is now `rookery join URL`. The arguments are the same.
- The gate log always lives in `~/.rookery/logs`.
- A mailroom on a local host (`127.0.0.1`, `::1` or `localhost`) now starts with no token. It prints one `NOTE:` line. Any local program can already write the database, so a local token adds no protection. A host that is not local needs `--allow-public` and a token (`--token` or `$ROOKERY_TOKEN`). `--allow-no-auth` no longer turns auth off on such a host. The flag has no effect now, and old start commands still parse.

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

- The database migration is additive. It adds the tables `rookery_sessions`, `identities` and `bindings`, and the column `inbox.session_id`.
- The functions `send()`, `quarantine()`, `claim()` and `ack()` have new keyword arguments. Old calls still work.
- This phase includes fixes from adversarial audits. Examples: the block parser rejects lone surrogates. `rookery init` rejects a settings file with duplicate keys or NaN values. `rookery turn` rejects input that is not valid UTF-8. The identity link rejects `previous_id` cycles.

### Fixed

- `rookery up` opened herdr tabs in the workspace that had the focus, not in the workspace of the caller. The `herdr-pane` launch step now passes `--workspace` from `$HERDR_WORKSPACE_ID`. Launch steps can use `{$NAME}` from the environment, and a list of tokens is optional.

- A turn that stops in the middle no longer leaves an agent `busy` forever. The postmaster now treats a `busy` state as idle for the push when its last sign of life is older than `busy_stale_s` (default 1800 seconds), and logs `busy-stale` once. The last sign of life is the later of `since` and the modification time of the transcript file. The prompt hook stores that path from its `transcript_path` input in `state/turn.json`, so a long turn that still writes is not stale. A bad `since` falls back to the modification time of `turn.json`. The session-start hook resets the state to `idle` only for `startup`, `clear` or no source, and logs `state-reset` when it replaced `busy`. It keeps `busy` for `compact` and `resume`. If the identity has a `herdr-pane` binding (the first one only) and the postmaster runs inside a herdr pane (`HERDR_ENV=1`) or with `ROOKERY_HERDR=force`, the postmaster also asks herdr (2 seconds at most). The turn must look busy for 60 seconds, and the pane must show `idle` or `done` on two readings at least 10 seconds apart in the same turn. The postmaster then pushes and logs `busy-corrected`. Set `herdr_probe` to `false` to turn the probe off. Rookery does not need herdr for this. The probe uses a new plugin point, `plugins.status_probe`.

### Removed

- The `rk` script (`~/.local/bin/rk`) is gone. We retired it. It was never part of this repository. It took the sender from `--as`, registered any name, and sent with no gate. Use `rookery send`, `rookery inbox`, `rookery peek`, `rookery ack` and `rookery identity list`. A stub at `~/.local/bin/rk` now prints these commands and exits 2.

### Notes

- Phase 1 limits:
  - The wake for herdr and tmux comes from the configuration file.
  - `TMUX_PANE` is not tested.
  - There are no remote mailroom adapters. A block sent with `ROOKERY_URL` goes to `outbox/failed`.
