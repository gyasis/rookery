# Research: Event-driven mail wake

Feature 005. Each entry gives the decision, the reason and the alternatives. The code facts come from `master`
2cea954 (tag `v0.7.0`), read on 2026-10-27.

## R1. Where mail becomes `pending` (Q1, FR-001, FR-012)

**Fact.** Five code paths make a mail `pending`:

| Path | Code | Commits itself |
|---|---|---|
| The gate stores a mail | `mail_gate._submit` | yes |
| A person or the screener releases a held or escalated mail | `rookery.release`, `rookery._decide_from_open(..., "pending")` | `release` yes; the screener calls it with `commit=False` (`screener.py:453`, `:645`) and commits at the end of the step |
| A person overrules a quarantine | `rookery.unquarantine` | yes |
| A dead-letter mail goes back to the queue | `rookery.requeue_dlq` | yes |
| A claim of a v1 node times out | `rookery.requeue_stale` | yes |

**Decision.** One new function, `rookery_wake.after_pending(conn, msg_ids)`. Each path calls it **after its
commit**, with the IDs that it made `pending`. The screener collects the IDs that it released in one step and calls
the function after its commit. `requeue_stale` is the v1 node path, and v1 nodes poll for themselves, so it does
not call the function.

**Rationale.** A wake that runs before the commit can read the old row and miss the mail. Then no event and no
recheck would find it until the sweep. One function keeps the rule in one place (FR-004).

**Alternatives.** A SQLite trigger cannot start a process. A check in `_decide` would fire before the commit for
the `commit=False` callers.

## R2. How the wake starts without slowing the send (FR-002, SC-006)

**Decision.** `after_pending` reads the recipient of each ID and asks the push gate (`postmaster._needs_push`).
For each recipient that needs a wake, it starts one detached process, `rookery wake --session SID --agent AID`.
It uses `subprocess.Popen` with `start_new_session=True`, `stdin`, `stdout` and `stderr` set to `DEVNULL`, and
`close_fds=True`. It never waits for the child. It keeps the child handles in a module list and calls `poll()` on them at each call, so a long-running parent (the MCP or mailroom service) reaps ended children and collects no zombies. Any error in this step is caught and logged as `wake-spawn-failed`,
and the send goes on.

The command finds its own program: the folder or the zip archive that holds `rookery_cli.py`. From a checkout it
runs `python rookery_cli.py wake ...`. From the zipapp it runs `python ARCHIVE wake ...`. The interpreter is
`sys.executable`.

**Rationale.** The sender can be a short CLI call, the MCP service or the mailroom service. A thread dies with a
short CLI call. A detached child outlives the sender. The fork costs a few milliseconds, well under the 50 ms of
SC-006. The child starts Python in the background, after the send returned.

**Alternatives.** A thread in the sender (lost when the CLI exits). A queue table and a reader (that is a poll).

## R3. Q3: how a detached wake finds its root and its config

**Fact.** `rookery up` writes a binding of kind `rookery-root` for the session (`cli_workspace.ROOT_BINDING`). The
wake needs the root for `agents/SESSION/NAME/state/turn.json` and for `postmaster/pushed/`. The config is always
`~/.rookery/config.json`. The MCP and mailroom services run as the same user, so `HOME` is the same.

**Decision.** The wake reads the root from the `rookery-root` binding of the session. When the session has none (a
session that `rookery init` made, not `up`), it uses `ROOKERY_ROOT` from the environment of the sender, which the
child inherits. When neither gives a root, the wake writes no file, logs `no-root` to the gate log of the sender,
and exits. The sweep of the session then covers the mail, because the sweep knows its root.

**Rationale.** The database already records the root, so a service with a bare environment still finds it.

**Alternatives.** Pass the root on the command line from the sender (the sender often does not know it). One root
for each machine (wrong when there are several projects).

## R4. Q4: the wake lock and the recheck (FR-005)

**Decision.** The lock is the file `ROOT/postmaster/lock/AGENT_ID.lock`, taken with `fcntl.flock(LOCK_EX |
LOCK_NB)`. If another wake holds it, the new wake logs `wake-busy` and exits. The holder runs the wake for that one
agent. Then it releases the lock **and reads the pending mail again**. If mail that needs a push and has no notice
is there, it tries the lock again, at most 3 rounds in total. The OS frees the lock when the process ends or
crashes. `fcntl.flock` exists on Linux and on the Mac.

The back-off record moves from the memory of the postmaster to `ROOT/postmaster/backoff/AGENT_ID.json`, beside
`postmaster/pushed/`. The "no push" set (`nopush`) stays in memory: a short wake recomputes it.

**Rationale.** A release-then-recheck closes the gap where the second wake left with `wake-busy` while the holder
had already read the pending list. The limit of 3 rounds is a bound for Principle X. A fourth event falls to the
sweep.

**Alternatives.** A database row (adds a write to every wake, and a crash leaves it held). No lock (two notices).

## R5. One wake code path (FR-004, D2)

**Decision.** Split `postmaster.wake_identities` into a loop and `wake_one(conn, root, config, ident, ...)`.
`wake_one` holds all the rules for one identity: the push gate, `pushed/`, the idle check, `busy-stale`, the herdr
probe, the binding lookup, the pane commands and the back-off. `wake_identities` (the sweep) calls `wake_one` for
each identity. `rookery wake` calls `wake_one` for one identity under the lock. The pane typing becomes
`type_notice(entry, value, text, run)`, which the remote check of R8 also uses.

**Rationale.** The existing tests of `wake_identities` (`test_wake.py`) keep proving every rule. New tests only
add the event and the lock.

## R6. The screener event (D3, FR-009 to FR-011)

**Fact.** Today the screener runs only when someone starts `rookery screen run`. The checks start in `shadow`
mode, and a shadow `t1` check still calls the model. No screener runs on this machine now, so the screener spends
nothing.

**Decision.** A screening event fires only when screening is on for the session. The new key `screener.enabled`
(default `false`) turns it on for every session, and the team file key `screener: true` turns it on for one
session. When it is on, the gate, after it stores a mail with no decision, starts one detached `rookery screen run
--session SID --once`. A per-session lock (`ROOT/postmaster/lock/screen-SID.lock`, `flock`) allows one pass at a
time. `screener.run(once=True)` already loops until a pass screens nothing. Before the holder exits, it releases
the lock and checks for mail with no decision, as in R4, at most 3 rounds. The 2 second default of `--poll` goes
away from the event path. `rookery screen run` with no `--once` keeps working as a loop for people who start it by
hand, but its default interval becomes 300 seconds.

**Rationale.** If every mail started a shadow screening by default, an update would begin to call the model on
every mail with no user choice. That breaks Principle X and changes cost without consent. The default `false`
keeps the cost of today.

**Alternatives.** Always on (unasked spend). A screener daemon (a process between mails, against D1).

## R7. The sweep for each session (Q5, Q6, FR-007, FR-013, FR-022)

**Decision.** `postmaster.py --session SID` runs the sweep for one session. Each round calls `wake_identities` for
the identities of that session. When screening is on, it also calls `screener.run(sessions=[SID], once=True)`. It
sleeps 300 seconds between rounds. It exits when the session is no longer `open`. It takes
`ROOT/postmaster/lock/sweep-SID.lock`. A second sweep for the same session logs `sweep-busy` and exits.
`rookery up` starts the sweep detached as `rookery sweep --session SID --root ROOT`, the same way as R2, after it built the rows. It uses a subcommand, not a file path, because the installed zipapp holds the modules inside one archive. The bundle did not include `postmaster.py` before this feature, so T003 adds it. When the sweep already runs,
the lock makes the second start exit at once (FR-013).

The sleep uses short steps so that `SIGTERM` ends it soon, as `screener.run` does today. A sleeping process uses no
CPU.

**Rationale.** The session is the unit that the user starts and resumes. The cost grows with sessions, not with
agents × agents.

## R8. The remote check (Q2, FR-019 to FR-021)

**Fact.** A remote agent today is a joined node: `rookery join` writes an MCP entry with `ROOKERY_NODE`,
`ROOKERY_URL` and `ROOKERY_TOKEN`. The mailroom answers `GET /inbox?node=NAME` with the unread mail of that name
(`mail_gate.unread_by_name`). The block path still refuses remote sends (`turn_block.py:589`, "Remote sending does
not work yet"). A remote machine has its own database, with no row for the remote identity in the database of the
mailroom.

**Decision.** A new command, `rookery check`, for a machine with `ROOKERY_URL` set. It calls `GET /inbox` with the
token, for `ROOKERY_NODE`. It applies the push gate to the rows. An `answer` counts as a push here, because the
remote side cannot see the open asks. When a row needs a push, it types one notice into its own pane through the
wake config. The pane comes from the `identity_binding_sources` config key, which local identity resolution also reads. It
records the announced IDs in `~/.rookery/remote/NODE.json`, so that a check never types twice for one mail.

The bound: `rookery check --follow` starts one detached follower. The follower checks at once, then every 300
seconds, at most 4 more times, then exits. It holds `~/.rookery/remote/NODE.lock`. A new trigger writes a new
start time to `~/.rookery/remote/NODE.follow`. A running follower reads that file before each check and restarts
its count from it. So a new turn or send restarts the count, and only one follower runs. The triggers are the Stop
hook (when `ROOKERY_URL` is set) and a send from the MCP tool when it uses `ROOKERY_URL`.

**Rationale.** It meets the bound of the user (1 check, then at most 4 more, 5 minutes apart) and costs no model
call. It needs no new endpoint on the mailroom.

**Limit (stated, not hidden).** This covers remote agents that run a harness with hooks or the MCP tool. A remote
agent with neither has no trigger. Session-scoped remote identities need a later spec.

## R9. Q7: a user who runs `postmaster.py --poll 0.5` (FR-007, FR-017)

**Decision.** In the identity mode (no `--nodes`), a `--poll` below 300 is raised to 300. The postmaster prints one
`NOTE:` line to stderr and logs `poll-raised` once. It does not fail, so an old start command or service unit
keeps working (FR-017). The v1 node mode (`--nodes`) is legacy and keeps its poll unchanged: its loop manages
warm node processes and calls no model on an empty poll. Tests use an injected clock and sleep function, not a
small `--poll`.

**The outbox drop-box (found in the wave 8 audit).** The old loop also ran the outbox scan, which turns a
file that an agent drops in its `outbox/` folder into mail. The scan now runs once in each round of the session
sweep, and once in each round of the old loop. So the sweep delivers a dropped file, or a block that the Stop hook
put back, within one sweep interval (300 seconds or less). A faster scan would be a poll, which Principle X and the user rule
forbid. A scan at turn end gains little, because the scan skips a file younger than 2 seconds. Today no postmaster
runs on this machine, so a dropped file is never delivered; 300 seconds is an improvement.

**Spec change.** FR-007 said "refuse a smaller interval". Refusing would break an old start command, which
conflicts with FR-017. FR-007 now says "raise to 300 with a notice".

## R10. `rookery up --session` and the three-word name (FR-023 to FR-026)

**Fact.** The team file needs `session` today (`rookery_team.py:89`). A second `up` for the same open session
already reuses the session, the identities and the worktrees, and starts only panes that are not alive
(`cli_workspace._build_rows`, `_alive`). `rookery_names.make_name()` makes a three-word name.

**Decision.** `--session NAME` sets `team["session"]` before validation. When the team file and the flag give no
name, `up` calls `make_name()` and retries on a name that already exists, at most 5 times, then fails. `up` prints
the name in its text and JSON output. The validation of the name stays as it is.

**Rationale.** Small. The resume already exists, so this only adds the handle.

## R11. Thinking models and token caps

No path of this feature calls a model. The screener event starts the existing screener, whose model path spec 004
bounded and measured (SC-013 of spec 004). The plan adds no token cap (R-ML1).
