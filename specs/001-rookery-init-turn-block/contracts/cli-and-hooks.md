---
type: Contract
task: 001-rookery-init-turn-block
updated: 2026-10-15
revision: 2 (sessions, identities, bindings)
---

# Contract: CLI commands and hook I/O

Every command exits 0 on success. Exit 2: bad input, not found, or a refusal (for example
`session end legacy`). Exit 1: `ack` skipped an ID. Exit 3: no current session, or an ended session (CLI and `rookery turn`; the hooks always exit 0). Exit 4: database busy. Each command that changes state prints one JSON object on stdout
when it gets `--json` (constitution VI). Database: `ROOKERY_DB`, else
`~/Documents/code/rookery/rookery.db` (research D6). "Resolve" means research D20.

## `rookery init` (FR-012 to FR-014, RQ-1.10, RQ-1.11)

```text
rookery init [--harness claude|codex|pi|omp] [--project NAME] [--dir PATH] [--json]
```

| Option | Default |
|---|---|
| `--harness` | `claude` |
| `--dir` | the current folder |
| `--project` | the folder name of the git top level of `--dir` |

- `claude`: upserts the section in `CLAUDE.md` and merges three hooks and a `permissions.allow` list into
  `.claude/settings.json`. The list holds 10 rules for the `rookery` mail commands, `herdr pane read` and
  `herdr agent` control. It holds no `send-keys` or `send-text` rule. It only pre-approves these commands. It blocks nothing else.
- `codex`: upserts the section in `AGENTS.md` only.
- `pi`, `omp`: upserts the section in `AGENTS.md` and installs the extension `rookery_pi_extension.ts` as
  `.pi/extensions/rookery.ts` (pi) or `.omp/extensions/rookery.ts` (omp), with the project name baked in. The
  extension calls the three `rookery hook` commands on `session_start`, `before_agent_start` and `agent_end`.
  `--json` adds `"extension": {"path": "...", "action": "created|replaced|unchanged"}`.
- `--json` output:
  `{"ok": true, "context_file": {"path": "...", "action": "created|inserted|replaced|unchanged"},
  "settings": {"path": "...", "added": ["Stop"], "updated": [], "unchanged": ["SessionStart"],
  "permissions": {"added": ["Bash(rookery inbox:*)"], "unchanged": [], "skipped": []}},
  "project": "..."}`
- `settings` is `null` for harnesses with no hook settings file (`codex`, `pi`, `omp`). `codex` agents get
  mail through `rookery turn` or the postmaster push.
- `permissions.added` lists the rules init appended to `allow`. `unchanged` lists the rules already in
  `allow`. `skipped` lists the rules init left out because they are in `deny` or `ask`. Init keeps every
  other `permissions` key and every existing `allow` entry in its order, and never changes `deny`, `ask` or
  `defaultMode`. The human line reads `permissions: added N allow rules` or `permissions: unchanged`.
- Human output ends with: `Open agent sessions do not join. Start a new Rookery session with new panes
  and agents: rookery session new --name <name>.`
- Init writes no file in `--dir` other than the context file and `.claude/settings.json` (FR-023). Its
  lock is `<root>/locks/init.lock` (research D13).
- Bad `permissions` shape: exit 2 and no file changed. This covers a `permissions` value that is not an
  object and an `allow`, `deny` or `ask` value that is not a list. Stderr names the key and says
  `Rookery wrote nothing.`
- Invalid settings JSON: exit 2, no file changed, stderr
  `settings file <path> is not valid JSON: line L column C: <message>. Fix the file, then run init again.`

## `rookery session` (FR-032, FR-036, RQ-1.24, RQ-1.26)

```text
rookery session new [--name NAME] [--from SESSION] [--bind KIND:VALUE ...] [--json]
rookery session list|show [SESSION] [--json]
rookery session end SESSION [--json]
```

- `new` prints `{"session_id": "rs-…", "name": "...", "copied": [{"name": "...", "handoff": "..."}]}`.
- `--from` creates one identity per agent name of the old session, with `previous_id`, and copies the
  newest `HANDOFF.md` of each. It never copies mail, idle state or bindings.
- `--bind` adds session bindings, for example `--bind herdr-workspace:w1`.
- `end legacy` refuses: stderr `The legacy session holds the mail from before sessions existed. Rookery
  cannot end it.`, exit 2.

## `rookery identity` (FR-029, FR-036, RQ-1.20, RQ-1.26)

```text
rookery identity new NAME [--session SESSION] [--json]
rookery identity bind KIND:VALUE [--json]          # binds to the resolved identity
rookery identity link --previous AGENT_ID [--json] # on the resolved identity
rookery identity show [--json]                     # who am I
rookery identity list [--session SESSION] [--json]
```

- `new`, `bind` and `link` refuse an ended session: stderr `The Rookery session "<name>" has ended. Rookery
  adds no agent to an ended session. Start a new one with: rookery session new`, exit 3 (the code of
  `rookery turn` for an ended session). `show`, `list`, `inbox`, `peek` and `ack` still work.
- The hooks and `rookery turn` also create and join nothing in an ended session (FR-032).

## `rookery inbox`, `rookery peek` and `rookery ack` (FR-031, RQ-1.13, RQ-1.14, RQ-1.22)

```text
rookery inbox [--json]          # unread mail of the resolved identity; changes nothing
rookery peek  [--json]          # the same mail, without the ack line; changes nothing
rookery ack ID [ID ...] [--json]  # marks the named unread mail read
```

- `inbox` ends its text output with the line `Mark read: rookery ack 12 13 14`. The IDs are the mail it showed. If there is no mail, it prints `No unread mail for <name>.` and no ack line.
- `inbox --json` adds `"ids": [12, 13, 14]` and `"ack": "rookery ack 12 13 14"`. The `ack` value is `null` when there is no mail.
- `ack` resolves the identity as `inbox` does. It marks read only the IDs that are unread mail of that identity. It skips all other IDs and does not change them.
- `ack` prints `N marked read.` If an ID is mail of the identity that is already read, it prints `Already read: 1 2`. This is not an error, so you can run `ack` again. If it skipped IDs, it also prints `Skipped (not unread mail of <name>): 7 9`. It skips an ID in these cases: not found, not mail of the identity, or mail with status held, quarantined or dlq. It exits 1 if it skipped any ID. It still marks the valid IDs. An ID that is not an integer is a usage error (exit 2).
- `ack` logs `inbox-ack` with the identity, its bindings, the IDs it marked, the IDs that were already read and the IDs it skipped. `--json` has the same three lists as `acked`, `already_read` and `skipped`.

- No `--as`, no `--all`. Each `inbox` and `peek` call logs `inbox-read` with the identity and its bindings.
- No current Rookery session: exit 3, stderr `No current Rookery session. Set ROOKERY_SESSION or start
  in a pane with a session binding.`

## `rookery hook session-start` (FR-015, FR-027, RQ-1.12)

```text
rookery hook session-start --project NAME
```

- stdin: the Claude Code SessionStart JSON (`source`, `session_id`, `cwd`, ...).
- Resolve. No session: print nothing, write at most one log line (`no-session` or `unknown-session`),
  exit 0. With no Rookery environment, write nothing (SC-010).
- Effects: `register_node(agent_id, kind="pane", lifecycle="persistent")`. Appends
  `export ROOKERY_SESSION=<session_id>` and `export ROOKERY_NODE=<agent_id>` to `$CLAUDE_ENV_FILE` when it
  exists.
- stdout (becomes context): `[rookery] You are <name> (<agent_id>) in Rookery session <session name>.`,
  then the newest `runs/*/HANDOFF.md` of the identity, then the unread summary line, then one line for
  each new failed block (research D23).
- It writes `state/turn.json` = `{"state": "idle", "since": ts}` (research D8).

## `rookery hook prompt` (FR-016, RQ-1.13)

- stdout with no session, or with 0 unread and 0 new failed blocks: nothing (0 bytes).
- stdout with unread: one line, for example
  `[rookery] 2 unread from researcher, swe about TASK-12. Read them with: rookery inbox`
- A new failed block adds one line (research D23), for example
  `[rookery] Your block for TASK-12 failed (send-failed). It is kept in <path>. Fix the cause, then send the block again.`
- It never prints a mail body. It never changes a mail status.
- With a session, it writes `state/turn.json` = `{"state": "busy", "since": ts}` (research D8).
- Loop breaker (FR-039): the stdin `prompt` text can start with `[rookery] new mail for`. Leading white
  space and one bracketed-paste wrapper (`ESC[200~` ... `ESC[201~`) do not count. If the text does not
  start so, the hook sets the loop counters of this identity to 0 and logs `loop-reset`. It writes
  nothing when no counter is above 0. A postmaster push or a missing `prompt` field resets nothing. If
  the reset fails, the hook logs `loop-reset-error` and still prints the notice.
- A block kept as `loop-held` shows as `[rookery] Your block for <task> failed (loop-held). ... Do not
  send it again until a human gives a prompt.`

## `rookery hook stop` (FR-005 to FR-009, FR-017, RQ-1.4, RQ-1.5, RQ-1.6, RQ-1.7, RQ-1.14)

- stdin: the Claude Code Stop JSON. The hook reads `last_assistant_message` (research D1).
- No session: print nothing, write at most one log line (`no-session` or `unknown-session`), exit 0.
  With no Rookery environment, write nothing (SC-010).
- Ended session: send nothing, write no idle state. Keep a valid block under the identity's
  `outbox/failed/`, log `session-ended`, exit 0.
- Resolve read-only (research D16). A session but no identity: keep a valid block in
  `<root>/unresolved/<TASK-ID>-<ts>.json`, log `no-identity`, exit 0.
- Step 1: `dispatch_text()` on the message (outcome table in data-model.md).
- Step 2: write `state/turn.json` = `{"state": "idle", "since": ts}` (research D8).
- stdout is always empty. The hook never blocks a stop, not for unread mail and not for a missing block
  (FR-017). Rookery is mail, not a phone call.
- Any error: the hook logs it and exits 0 (research D16).

## `rookery turn` — the hookless wrapper (FR-020, RQ-1.16)

```text
rookery turn --project NAME [--json] < turn-output.txt
```

- stdin: the full text of one headless turn (Codex, pi, omp). Resolve from the environment.
- Effect: the same `dispatch_text()` as the Stop hook. It writes no idle state.
- `--json` stdout: `{"outcome": "sent|no-block|quarantined|loop-held|handoff-pending|send-failed|no-session|remote-unsupported|db-busy",
  "msg_id": n, "reason": "..."}`.
- A `loop-held` outcome keeps the block in `outbox/failed/` and sends no bounce mail (FR-039).
- On `db-busy`, exit 4. The caller still has the turn text and can run `rookery turn` again (research D16).

## Postmaster steps (FR-011, FR-018, FR-028, RQ-1.9, RQ-1.15, RQ-1.23)

- **Start**: `python3 postmaster.py` with no `--nodes` runs only these two steps. With `--nodes`, the
  roster steps also run, as before (research D10).

- **Outbox scan**: each loop, for each `<root>/agents/*/*/outbox/*.json`: claim it into `<root>/postmaster/`,
  then `dispatch_block()` (research D3, D11). The scan never puts a file back and never writes `HANDOFF.md`.
- **Wake**: each loop, for each identity in an open session with pending mail newer than
  `<root>/postmaster/pushed/<agent_id>.json`, and ONLY when its `state/turn.json` reads `idle`: run the
  wake command of the first binding whose kind has one in `~/.rookery/config.json`. Notice text:
  `[rookery] new mail for <name> from <sender name> about <task>. Read it with: rookery inbox`.
  Push gate (FR-038): only mail that needs action counts. Kind `status`, and mail from the identity to
  itself, never push. They stay `pending` and show in the next prompt notice. Kind `answer` pushes only
  when it answers an open `ask`. The recipient sent that ask on the same task, and no earlier answer
  from that agent followed it. Bounces and mail with no `kind` push. A mail that is not pushed is not written to the pushed file.
  A busy agent gets no push until its turn ends. No such binding → log `no-wake-binding`. No
  `state/turn.json` → log `no-idle-state`. Either way the mail waits (research D10).

## Bounce `note` texts

Each note names the first rule that failed, then tells the agent what to do next (constitution VI). Each note
passes `ste-lint`. Examples:

- `The block is not valid JSON: <parser message>. Fix the JSON, then send the block again.`
- `The field "to" is missing. Add "to" with the name of an agent in your session, then send the block again.`
- `The kind "veto" is for the system only. Use ask, answer, decline, handoff, status or recruit, then send the block again.`
- `The block has 5,210 bytes. The limit is 4,096 bytes. Put long text in a file and add its path to "read".`
- `The read entry "<entry>" is not a path inside <root>. Put the file inside <root> and give its path.`
- `No agent named "law-cousel" exists. Did you mean: law-counsel? Send the block again with the correct name.` (`wrong-recipient`)
- `The agent "swe" is in another Rookery session. Mail stays inside your session. Ask the user to start a shared session.` (`off-team`)
