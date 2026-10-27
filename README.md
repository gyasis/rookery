# Rookery

A local-first mesh. Many coding agents (headless Claude nodes, raw-API
daemons, and terminal sessions) talk to each other **unprompted**. A
sub-agent can **phone home** mid-task for more context or for credentials it lacks,
without ever putting a secret in a prompt.

> Codename **Rookery** (where messenger birds roost). Internally: the durable
> store is the **mailroom**, the watcher/loop is the **postmaster**, and the
> design rationale is in [`docs/`](docs/index.html).

## Mental model — this is async **mail**, not instant messaging

Rookery is durable, asynchronous **mail**, checked in **bursts** — like email,
not a phone call. Messages persist in the mailroom (SQLite) until the recipient
checks. **Nothing is lost, whenever that happens.**
An agent does its own
work and **periodically drains its inbox** (on a `/loop` schedule, or at natural
points in its workflow), handles the batch, replies, and goes back to work.

**Slow delivery is fine.** Do *not* reach for instant-delivery machinery
(blocking waits, or typing into another agent's terminal via tmux). That solves
real-time delivery, which this design does not need. The right receive path
is a periodic `rookery inbox` check (legacy: `mailctl inbox`). Then `rookery ack ID ...`
marks mail read. (tmux pane-injection exists for the rare
"I want it instant" case, but it's a workaround, not the model.)

In a 0.7 session, Rookery types one notice into the pane of an idle agent when mail
needs action. See [How mail wakes an agent](#how-mail-wakes-an-agent).

## Cross-harness — the poll-and-act loop lives OUTSIDE the agent

The goal is to make **different** coding agents (Claude, Codex, Gemini,
raw-API) work together. So the "check mail and act" loop must **not** depend on
any one harness's features — e.g. Claude Code's `/loop` is Claude-only and useless
to a Codex node. The loop lives in the **postmaster**: an external driver that
polls the mailroom and invokes each node *non-interactively* with the new mail as
its prompt. `node_runner.py` ships engines for `claude` (`claude -p`), `codex`
(`codex exec`), `gemini` (`gemini -p`), `claude-sdk`, and `mock`, so

```bash
postmaster.py --nodes architect=claude,reviewer=codex,researcher=gemini
```

puts three different harnesses on one mailroom. The only thing the postmaster
needs from any harness is a "run once with this prompt" invocation — which all of
them have. Per-harness loops like `/loop` are just a convenience for a single
interactive session you want to *watch*, never the cross-harness mechanism.

## The idea in one picture

```
 send_mail ─▶ ┌──────────── mailroom.db (SQLite, WAL) ────────────┐
              │   nodes · inbox · credential_requests  (=audit log)│
              └─────▲──────────────────────────────┬──────────────┘
        own-loop poll (cheap, non-LLM)             │ claim() = mark delivered
              │ wakes engine ONLY on real mail      │
     ┌────────┴─────────┐                  ┌────────┴─────────┐
     │ node: architect  │  MAILTO:triage   │ node: triage     │
     │ headless `claude -p`────────────────▶ (raw daemon ok)  │
     │ NEEDCRED:aws-prod│                  └──────────────────┘
     └────────┬─────────┘
              ▼  phone home (CIBA)
     mesh_approve ──▶ JIT token *pointer* (secret stays in keychain)
```

## Install

One self-contained binary — the **same** `rookery` command hosts a mailroom
(`rookery serve`) *or* joins one (`rookery join`). You choose the role at
run time, not at install time.

```bash
git clone https://github.com/gyasis/rookery.git ~/Documents/code/rookery
cd ~/Documents/code/rookery
python3 -c "import bootstrap; bootstrap.build_pyz('$HOME/.local/bin/rookery')"
rookery --help          # serve · up · approve · known-hosts
```

Or, from a running mailroom (no git): `curl -sSL http://<host>:<port>/bootstrap | python3 -`.

Core is Python-3 **stdlib only**. Run `pip install cryptography` only for
the TLS / signed-card / hardened-policy security tier. Full matrix and the
host-vs-peer model: **[`docs/QUICKSTART.md` → Installation](docs/QUICKSTART.md#installation)**.

## Docs

- **[`docs/QUICKSTART.md`](docs/QUICKSTART.md)** — paste-and-run setup for the three deployment shapes: same machine · same network · internet (full security stack).
- **[`docs/COOKBOOK.md`](docs/COOKBOOK.md)** — 22 small recipes (start the mailroom, wire each engine, federate, sign cards, install the hardened policy, mint invites, …).
- **[`docs/NODE_BRIEFING.md`](docs/NODE_BRIEFING.md)** — the instruction block you hand each agent. It tells the agent its name, its siblings, and how to message them. It has CLI and MCP variants, and a filled `architect ↔ triage` example.
- **[`docs/STORIES.md`](docs/STORIES.md)** — narrative use-cases with the commands from the matching demo. Stories: Claude↔Codex review, Mac Studio joins over LAN, external A2A agent, federation, CIBA credential phone-home, three-vendor parallel work.
- **[`docs/rookery-explained.html`](docs/rookery-explained.html)** — single-page visual explainer (model · deploy shapes · A2A · security tiers · engines).
- **[`docs/HERDR_INTEGRATION.md`](docs/HERDR_INTEGRATION.md)** — herdr as the console over the mesh (human→agent = pane, agent→agent = mail; `blocked` ↔ `NEEDCRED`). Ships as the optional `plugin_herdr/` package on the `plugins.py` registry. If you delete the directory, core does not change. Adds `--injector herdr`, `rookery herdr status|bind|focus|notify`, and a `postmaster --notify` sink; see `demo_herdr.sh`.
- Design rationale: [`docs/index.html`](docs/index.html) (links the two research reports + paired-debate transcript).

## Why this shape (from the paired debate)

- **State vs signal.** Durable state lives in SQLite (survives crashes, fully
  queryable = observability). The "signal" (waking an agent) is cheap.
- **Economy.** The poller is a non-LLM loop (~$0). A token-turn is spent only
  when **real mail** exists. Idle nodes cost nothing.
- **Substrate = headless Claude.** Each turn is a fresh `claude -p` (or Agent
  SDK) wrapped in a thin own-loop: own-loop reliability **plus** the full coding
  harness, no rebuild. Raw-API daemons and terminal sessions can share the same
  bus. (Full reasoning: `docs/paired-debate-mailroom-architecture.html`.)

## Wire protocol

An engine (mock or Claude) drives the mesh by emitting lines:

| Line | Meaning |
|---|---|
| `MAILTO:<node>:<text>` | send mail to another node |
| `NEEDCRED:<resource>` | phone home for a credential, then **pause** until approved |
| `ACK: …` | acknowledgement — never auto-replied to (loop prevention) |

The postmaster also watches for **`@<node>`** mentions in any message and notifies
the mentioned node ("they're talking about you") — even if it isn't the recipient.

## Two modes (both kept on purpose)

| Mode | Who polls | Pause = | When to use |
|---|---|---|---|
| **A · self-polling nodes** (v1 MVP, `demo.sh`) | each node polls its own inbox | node idle-loops / blocks in-process on `NEEDCRED` | simplest; nodes are long-lived; few agents |
| **B · central postmaster** (`demo_postmaster.sh`) | one `postmaster.py` polls for everyone | nodes are **fully asleep / exited** ($0); `NEEDCRED` exits and the postmaster re-wakes on approval | many agents; true durable pause; "alarm-clock" wakeups |

Mode B is the "a background poller says *you've got mail*" model. It wakes the
sleeping agent. A truly paused agent cannot poll itself, so the postmaster is its
alarm clock. v1 (A) did not change. Pick per situation. The DB is the same.

## Agent lifecycle — ephemeral vs persistent

A second axis, independent of transport mode: **what an agent remembers**.

| Lifecycle | Memory | Cost when idle | Use for |
|---|---|---|---|
| **`ephemeral`** | none — gets only the task brief, runs once, vanishes | $0 (gone) | throwaway, token-saving subtasks |
| **`persistent`** | full thread **rehydrated from the mailroom** on each wake | $0 when reaped; warm during its idle window | orchestrators, standing "power-partner" specialists |

The key idea: **nothing stays warm forever. Nothing loses context it needs.**
A persistent partner is *also* killed when idle — its context lives in the DB, so
it re-ups with full memory next time (context survives the kill, not the process).
To avoid cold-start churn during active work, persistent agents get a **warm
window** (`--idle-timeout`, e.g. 5–10 min). They stay up while work flows,
then sleep. The postmaster keeps a bounded **warm pool** (`--max-warm`, default 5)
and evicts the least-recently-used partner when full (it rehydrates later).

```bash
# architect = persistent power-partner (Claude), reviewer = ephemeral (Codex)
python3 postmaster.py --nodes architect=claude,reviewer=codex \
  --persistent architect --max-warm 5 --idle-timeout 600
```

> **Latency: a warm session still wins, but for a different reason.**
> This note used to claim plain `claude -p` pays **~115s per call** because of
> SessionStart hooks. **A re-measure on 2026-08-03 showed that is wrong.** Timing the
> hooks directly: `herdr-agent-state.sh` **0.05s**, the SIO session_start hook
> **0.04s** — about **0.1s** of hooks, not 115s. End to end, `claude -p` costs
> **~6.6–7.2s** per call, and hooks-on vs hooks-off is inside the run-to-run
> noise (3 runs each, two working directories; the hooks-off mean came out
> *higher* in one pair). Whatever caused 115s here is gone or was never the
> hooks.
>
> The Agent SDK fix still stands on its own merits. The cost is per-session
> startup plus inference, so a **warm long-lived session** spreads it out: **open
> 2s · first turn 7s · warm turns ~2s** vs ~7s for every one-shot. Use
> `engine=claude-sdk` for persistent partners; the node is `sdk_node.py`.
> `setting_sources=[]` saves roughly 0.1s/turn. Choose it for
> isolation, not speed. It also skips global MCP, so
> tool-using SDK nodes must pass `mcp_servers` explicitly.

## Run the demos

Prereqs: Python 3 (stdlib only for the core). Paid demos need `claude` and
`codex` logged in; the SDK demos also need `pip install claude-agent-sdk`.

```bash
cd ~/Documents/code/rookery
# free (mock, instant):
./demo.sh                 # mode A — two self-polling nodes
./demo_postmaster.sh      # mode B — agents asleep, postmaster wakes them; persistence
./demo_research.sh        # research queue: 3 tasks dispatched, researcher→writer handoff
./demo_multihost.sh       # HTTP sidecar + a node that speaks only HTTP (multi-host transport)
./a2a_demo.sh             # external agent discovers Rookery (Agent Card) + tasks it via A2A JSON-RPC
./demo_a2a_secure.sh      # A2A with bearer auth (401 without token) + message/stream over SSE
./demo_security.sh        # a node blocks a dangerous inbound message (swappable policy)
./demo_federation.sh      # two mailrooms (home+mac): alice@home <-> bob@mac relayed across
./demo_dlq.sh             # dead-letter queue: undeliverable mail -> DLQ + sender alert + requeue
./demo_terminal.sh        # substrate A: mail injected into a tmux pane (a human/agent joins)
./demo_a2a_push.sh        # A2A push: sidecar POSTs the completed task to your webhook
./demo_signed_card.sh     # A2A signed agent card (Ed25519): valid verifies, tampered fails
./demo_hardened.sh        # hardened policy: per-node identity + per-target ACL (guest denied reviewer)
./demo_invite.sh          # invite/handshake: A mints per-node token; B verifies pin + joins (no hand-edits)
./demo_up.sh              # one-command join: `rookery join` (bootstrap installer + handshake + TOFU pin + auto-config)
./demo_tls.sh             # HTTPS sidecar (self-signed cert) + token = auth + encryption
# paid (real models):
./demo_real.sh            # Claude architect ↔ Codex reviewer
./demo_real_research.sh   # Claude researcher fires the DeepLake MCP tool → Codex writer → human
./demo_real_sdk.sh        # warm SDK node: 3 sequential turns, ~7s then ~2s (vs ~115s each)
./demo_real_sdk_research.sh # warm SDK + DeepLake MCP tool → Codex writer → human
./demo_real_team.sh       # 2×Claude + Codex + Gemini: architect fans out to all 3 IN PARALLEL
```

> **Warm SDK + tools:** a `claude-sdk` node attaches MCP servers explicitly via
> `--mcp <name>` (from `~/.claude.json`), since `setting_sources=[]`
> skips global MCP. E.g. `--mcp deeplakesearch --allowed-tools mcp__deeplakesearch__retrieve_context`.

Both send one task to `architect` and show:
1. `architect` wakes **unprompted**, mails `triage`, then **pauses** on a credential.
2. `triage` wakes **unprompted** and acks back.
3. You approve the credential → `architect` **resumes / is re-woken**.

**Success =** the hops happen with no keystroke between turns, visible in the DB.
In mode B, `demo_postmaster.sh` additionally proves **no agent process is alive**
while paused.

### Watch it live (the Monitor node)
```bash
./monitor.sh        # in a second pane — live view of inbox/nodes/credentials
```

### Use real Claude instead of the mock
```bash
python3 node_runner.py --node-id architect --engine claude
python3 node_runner.py --node-id triage    --engine claude
# Legacy send_mail.py; the current command is: rookery send architect "..."
python3 send_mail.py --to architect --from human --body "summarize this repo, ask triage for test logs"
```

## Pause & resume (the "video button")

Three levels, weakest-to-strongest durability:

1. **OS freeze** — `./pause.sh <node>` (`kill -STOP`) freezes a node instantly,
   0 CPU; `./resume.sh <node>` (`kill -CONT`) continues exactly where it was.
   RAM held; does **not** survive reboot.
2. **CRIU** — checkpoint a process to disk, restore after reboot / on another
   host. (Not wired here; the heavyweight option.)
3. **Durable pause (preferred)** — the `NEEDCRED` flow. State is in the mailroom,
   so a paused node can even **exit**; a fresh run resumes from the rows. Costs
   **$0** while paused and survives reboot. This is the agent-native pause.

## A2A interop (cross-vendor discovery)

The sidecar speaks **A2A** (Agent2Agent), so external agents on other frameworks
can discover and task Rookery:

- **Discover:** `GET /.well-known/agent-card.json` → an Agent Card whose `skills`
  are the registered Rookery nodes.
- **Task:** `POST /a2a` JSON-RPC `message/send` (route to a node with
  `message.metadata.recipient`) → returns a Task; the message lands in the
  mailroom and a node processes it.
- **Poll:** `POST /a2a` JSON-RPC `tasks/get` → Task `state` + the node's reply as
  an artifact.

- **Stream:** `POST /a2a` JSON-RPC `message/stream` → Server-Sent Events
  (`submitted` → `artifact-update` with the reply → `status-update` `completed`).
- **Auth:** start the sidecar with `--token <tok>` (or `$ROOKERY_TOKEN`). Then
  `/a2a` + the REST endpoints need `Authorization: Bearer <tok>`; `/health`
  and the agent card stay public (the card advertises the `bearerAuth` scheme).
  The legacy `mailctl.py` sends the token from `--token`/`$ROOKERY_TOKEN`.

> **Auth ≠ encryption.** A bearer token authenticates but sends in clear text.
> On a trusted **LAN** that's fine. Over the **internet**, add **TLS**:
> `./gen_cert.sh` then `mailroom_server --tls-cert rookery-cert.pem --tls-key
> rookery-key.pem --token …` → HTTPS; **TLS + token = auth + encryption**.
> (Self-signed clients set `ROOKERY_INSECURE_TLS=1`; for real internet use a
> CA-signed cert or a **tunnel** — Tailscale / SSH `-L` / Cloudflare.)

- **Push:** put `configuration.pushNotificationConfig` `{url, token}` in
  `message/send`. The sidecar returns `submitted` at once. It **POSTs the
  completed Task to your webhook** when the node replies.
- **Signed cards:** serve with `--card-key <ed25519.pem>` (`gen_card_key.py`) and
  the agent card carries an **Ed25519 signature** + public key; verify with
  `verify_card.py <url>`. (Embedding proves integrity; pin the key out-of-band —
  JWKS/DID — for true identity.)

Run `./a2a_demo.sh` (open), `./demo_a2a_secure.sh` (auth + streaming), or
`./demo_a2a_push.sh` (webhook push) for the round-trip.

## Use it from Claude Code (MCP + CLI)

Two ways for a Claude Code session (and its subagents) to join the mesh:

**MCP tools** — add `rookery_mcp.py` to `~/.claude.json` `mcpServers`:
```json
"rookery": {
  "command": "python3",
  "args": ["/home/gyasis/Documents/code/rookery/rookery_mcp.py"],
  "env": {"ROOKERY_NODE": "claude-main",
          "ROOKERY_DB": "/home/gyasis/Documents/code/rookery/rookery.db"}
}
```
Tools: `send(to, body)` · `check_inbox()` · `ack(ids)` · `await_message(timeout)` · `roster()`.
`check_inbox` does not mark mail read. Call `ack` with the IDs it lists.
For a **remote** mailroom set `ROOKERY_URL` (+ `ROOKERY_TOKEN`) instead of `ROOKERY_DB`.

**CLI / scripting** — `rookery send` / `inbox` / `ack`, `mesh_approve.py`, and the legacy `send_mail.py` and `mailctl.py` (remote);
agents drive these from Bash. Same mailroom underneath either way.

A **background subagent** + these tools = it talks to other agents
asynchronously while you keep working in the main context.

## rookery init and sessions

Install Rookery into a project, then start a Rookery session:

```bash
cd ~/Documents/code/<project>
rookery init --json                     # CLAUDE.md section + 3 merged hooks
rookery init --json                     # run again: every action reads "unchanged"
rookery session new --name alpha --json # Rookery generates the session ID
```

Start each agent in a new pane:

```bash
ROOKERY_SESSION=alpha ROOKERY_NODE=researcher claude
```

You can also bind the session to a herdr workspace one time:

```bash
rookery session new --name alpha --bind herdr-workspace:<id>
```

Then each new pane in that workspace joins `alpha`. A pane that was open before `rookery init` does not join.

You start no postmaster. When mail that needs action arrives, the gate wakes the idle recipient. The sweep of the session catches a lost event. See [How mail wakes an agent](#how-mail-wakes-an-agent). A session from `rookery session new` has no sweep until you start one:

```bash
rookery sweep --session alpha --root ~/.rookery/projects/<project>
```

`python3 postmaster.py` without `--nodes` still works. It runs the outbox scan and the idle wake for every open session, once every 300 seconds.

Note: do not run `rookery turn` for a turn in a Claude Code session that has the Rookery hooks installed. The Stop hook already sends the block of that turn, so `rookery turn` sends it a second time. Use `rookery turn` for agents that have no hooks, such as Codex.

To send one mail now, without a turn, run `rookery send TO "SAY" --task TASK --kind KIND`. It goes through the same checks as a block. The sender is always your identity: there is no `--as`.

## Build a team: rookery up

`rookery up TEAM_FILE` builds a team from one JSON file. The old `rookery up URL` is now `rookery join URL`.

```json
{
  "session": "auth-fix",
  "task": "Fix the token refresh race.",
  "acceptance": ["test_refresh passes 100 times", "no change to the public API"],
  "agents": [
    {"name": "scout", "harness": "claude", "role": "reader",
     "persona": "You read code and report. You do not edit.",
     "scope": {"does": ["read the auth module"], "does_not": ["edit files", "run the server"]}},
    {"name": "fixer", "harness": "claude", "role": "writer", "task_id": "T1",
     "binding": "tmux-pane", "claims": ["auth/*.py"]}
  ]
}
```

Run `rookery up team.json`. Add `--no-launch` to build everything and start no agent. Add `--json` for one JSON object.

Add `--session NAME` to name the session. The flag wins over `session` in the team file. With no name from either, `rookery up` makes a name of three words and prints it: `Session: NAME (name made)`. Run `rookery up team.json --session NAME` again to resume the session. It keeps the mail, the identities and the worktrees. It starts only the panes that are not alive, and no second sweep. `"screener": true` in the team file turns screening on for the session.

What it builds:

- A session, and an identity for each agent.
- One sweep process for the session. It catches mail that got no wake. See [How mail wakes an agent](#how-mail-wakes-an-agent). The output has a `Sweep:` line: `started`, `running`, `off` or `failed`.
- A folder for each agent, with generated files: `CLAUDE.md`, `AGENTS.md`, `CONTEXT.md`, and a persona and a scope card in `_config/`.
- A git worktree for each writer, in `wt/<session>/<name>/<repo>/`. A reader has no worktree. A reader keeps the host project rules.
- A port map for each agent, in `ROOKERY_PORTS`.
- The generated views: `ROOKERY.md`, `_directory/`, `_teams/`, `_threads/` and `_mail/`.

Rookery starts each pane with the steps in the configuration (`launch` and `alive`). Rookery has no code for one terminal tool. To use another tool, add a binding kind to the configuration. The defaults cover `herdr-pane` and `tmux-pane`.

A step can use `{$NAME}`: Rookery fills it from the environment of the shell that runs `rookery up`. Put such a token in a list to make it optional. Rookery drops the whole list when the variable is not set. The `herdr-pane` default uses `["--workspace", "{$HERDR_WORKSPACE_ID}"]`, so herdr opens the tabs in the workspace where you ran `rookery up`. Outside herdr, Rookery drops the flag. If your `~/.rookery/config.json` has its own `launch` block, add this list to it yourself.

First start: Claude Code shows a trust dialog in each new folder. A reader also shows an import dialog. You answer both one time in each new pane. Rookery does not edit `~/.claude.json`. Rookery does not wake an agent until its first turn has started.

Mail: agents that `rookery up` starts use the CLI only: `rookery send`, `rookery inbox` and `rookery ack`.

Other commands:

- `rookery finish NAME --message TEXT [--session S]` shows the change, asks three times, then commits, pushes and deletes the worktree. It never deletes the branch. An answer other than `yes` stops it. Set `BROTUNDWASSER=1` to skip the questions.
- `rookery gc` lists stale worktree rows and the reason for each. It deletes nothing.
- `rookery regen` writes the generated views again from the database. It changes no row.

Guardrails: `rookery hook guard` denies a write outside the folders of the agent. `rookery hook watch` warns when a shell command wrote outside them. A claim (`claims` in the team file) warns the agent that another agent owns a file. These checks stop accidents. They are not a security boundary, and they do not catch every shell command.

Folder layout of the root:

```text
~/.rookery/projects/<project>/
├── ROOKERY.md                 generated: what the root holds
├── _directory/                generated: INDEX.md, ports.md, claims.md
├── _teams/                    generated: one file for each session
├── _threads/                  generated: one line for each thread
├── _mail/                     generated: quarantine/ and review/
├── agents/<session>/<name>/   one folder for each agent
│   ├── _config/               persona, scope card, settings
│   ├── runs/                  one folder for each task
│   ├── outbox/                mail the agent sends
│   └── state/                 turn.json
├── wt/<session>/<name>/<repo>/   the worktree of a writer
├── postmaster/
│   ├── outbox/                mail the postmaster takes
│   ├── pushed/                mail already pushed
│   ├── backoff/               one file for each agent whose wake failed
│   └── lock/                  the wake, sweep and screening locks
└── logs/                      gate.jsonl and the other logs
```

## How mail wakes an agent

An agent sees new mail at a turn boundary. An idle agent sees nothing until something starts a turn. Rookery starts that turn with one notice in the pane of the agent: a line that starts with `[rookery] new mail for`. No process polls for it.

**Events.** Each step that makes a mail `pending` starts one detached `rookery wake --session S --agent A` for the recipient. The steps: the gate when it stores a mail, a release, an unquarantine, a requeue from the dead-letter queue, and a bounce. The screener is one more step, when it delivers a held mail. The send returns before the wake runs. A wake that fails never changes the send. The Stop hook starts the same wake when a turn ends with mail that needs action and no notice announced it.

The wake applies the rules of the postmaster. These are the push gate, the record of announced mail in `postmaster/pushed/`, the idle check, `busy-stale`, the herdr probe and the back-off. Only one wake runs for an agent at a time. It holds `postmaster/lock/AGENT_ID.lock`, and a second wake logs `wake-busy` and exits. After a push the holder reads the pending mail again, at most 3 rounds. Each wake writes one log line with its outcome. The outcomes: `pushed`, `nothing`, `busy`, `wake-busy`, `backoff-wait`, `wake-failed`, `no-idle-state`, `no-wake-binding`, `no-root`, `lock-failed` and `unsafe-name`. The back-off record is `postmaster/backoff/AGENT_ID.json`, so it lasts between wakes.

The wake finds the project root in the `rookery-root` binding of the session, else in `ROOKERY_ROOT`, else in `--root`. With none, it logs `no-root`, and the sweep covers the mail.

**The sweep.** An event can get lost: a person kills the wake, the machine sleeps, or the database is busy. One sweep for each session catches the mail. `rookery up` starts it. You can start it by hand: `rookery sweep --session S [--root R]`, or `postmaster.py --session S`. Each round it scans the outbox drop-box and wakes the agents of the session that have mail with no notice. It also runs one screening pass when screening is on, or when a mail waits as `held` for screening. Then it sleeps 300 seconds. Set `sweep_s` in `~/.rookery/config.json` for a longer interval. The sweep raises a smaller value to 300 with one `NOTE:` line. The sweep reads only the database and calls no model. It ends when the session ends, when a new database file replaces the old one, or when the project root is gone. One sweep runs for each session. A second one logs `sweep-busy` and exits.

**The remote check.** The mailroom runs on one machine. An agent on another machine has `ROOKERY_URL`, `ROOKERY_TOKEN` and `ROOKERY_NODE` from `rookery join`. It pulls. `rookery check` calls `GET /inbox?node=NODE` with the Bearer token, applies the push rules, and types one notice into its own pane through the `wake` config. It records the announced IDs in `~/.rookery/remote/NODE.json`. The call refuses a redirect, reads at most 1 MB and waits at most 20 seconds. A failed pane command sets a back-off from 30 to 600 seconds. `rookery check --follow` starts one follower for the node: 5 checks per trigger, 300 seconds apart. It stops at the first notice. A trigger while it sleeps restarts the count and starts one check at once. The triggers are the Stop hook (on a machine with `ROOKERY_URL`) and a successful send through the MCP tool. An agent with no hooks and no MCP tool has no trigger. A remote check calls no model.

**Screening.** Screening is off by default (`screener.enabled: false`). A shadow `t1` check calls the model, so an update must not start model calls that nobody chose. Set `screener.enabled` to `true` in `~/.rookery/config.json` for every session, or `"screener": true` in the team file for one session. Then the gate starts one `rookery screen run --session S --once` after it stores a mail, under `postmaster/lock/screen-SESSION_ID.lock`. The sweep runs one pass each round. A mail that a live check holds for screening always gets the pass.

**Kill switch.** `ROOKERY_WAKE=off` in the environment stops every event spawn. No wake, no screening pass, no sweep from `rookery up`, and no remote check from a hook or the MCP tool start. `rookery wake`, `rookery sweep` and `rookery check` still run when you call them.

**Cost.** No wake path calls a model. The number of wakes grows with the mail, not with the number of agents. An idle hour costs 12 sweep rounds and 0 model calls.

**Operation.**

- After a deploy, restart `rookery-mcp.service` and `rookery-mailroom.service`. Until they restart, a send through them starts no wake.
- A sweep does not survive a reboot, or a logout that kills it. Run `rookery up TEAM_FILE --session NAME` again. It resumes the session and starts the sweep.
- One sleeping sweep process runs for each open session, until `rookery session end` ends the session.
- A session that `rookery init` or `rookery session new` made gets event wakes: the spawning side passes the project root. No sweep starts for it unless you run `rookery sweep --session NAME --root ROOT`.
- The Stop hook starts the remote check only when `ROOKERY_URL` and `ROOKERY_NODE` are in the shell environment of the agent. The `env` block of the MCP server alone is not enough. A remote agent that only uses the MCP tool gets the trigger on each successful send.

## The screener: rookery screen

The screener checks mail before an agent reads it. It is off the delivery path. Screening is off by default (`screener.enabled: false`). Set `screener.enabled` to `true` in `~/.rookery/config.json`, or `"screener": true` in the team file. Then the gate starts one pass when mail arrives, and the sweep of the session runs one each round. See [How mail wakes an agent](#how-mail-wakes-an-agent). You can also run it by hand:

```bash
rookery screen run            # loop, one pass every 300 seconds; add --once for one pass
rookery screen status --json  # backend, model host, backlog
```

**Shadow mode first.** Every check starts in `shadow`. A shadow check writes a decision and changes no mail. The checks are `injection`, `duplicate`, `budget`, `thread`, `gates`, `t1`, `hunch` and `t2`. Each is `shadow`, `live` or `off`. The first run skips mail older than `screener.max_age_s` (86400 seconds).

**Compare, then go live.**

1. `rookery screen review` lists decisions that have no call. Run `rookery peek` to read a mail.
2. `rookery screen mark DECISION_ID right|wrong|LABEL` says if the decision was right. `--by NAME` sets your name.
3. `rookery screen report [--check C] [--json]` shows the agreement. `rookery screen export [--check C] [--out FILE]` writes one JSON line for each compared decision.
4. `rookery screen mode CHECK live` switches a check. This is the 150 rule: a model check (`t1`, `hunch`, `t2`) needs 150 compared decisions, and `t1` also needs `screener.bands.t1`. The refusal tells you how many are missing. If you edit `config.json` by hand, the 150 rule does not run. That risk is yours. Use `rookery screen mode CHECK shadow` or `off` to go back.

**Live mode fails closed.** In live mode only an answer in the accept band acts. If a check fails, times out or is unsure, the mail goes to a person as `escalated`. Rookery sends a notice to the boss of the session (the agent with the role `boss` in the team file) and fires the plugin `sink` event `escalated`. A person decides:

- `rookery screen release MAIL_ID [--by NAME]` delivers it.
- `rookery screen quarantine MAIL_ID REASON [--note TEXT] [--suggest AGENT]` rejects it.
- `rookery screen escalate MAIL_ID [--reason TEXT]` holds a mail for a person.

**Threads.** A thread is the `thread` field of a block. One review covers the first-mail pair of a thread: later mail of those two agents gets no model call. A closed thread refuses new mail, and the sender gets a bounce. A thread closes in four cases.

- A live check puts a mail in quarantine.
- The user runs `rookery thread close SESSION THREAD`.
- The boss runs the same command with `--as AGENT`.
- The agent that opened the thread sends a `handoff` with `status` `done`.

Time never closes a thread. A live check that puts a mail in quarantine closes its thread. Releasing that mail later does not open the thread again. Start a new thread.

**Ask budget and duplicates.** Each task has a limit on `ask` mail. The limit is `ask_budget` in the team file, else `screener.ask_budget` (20). Rookery holds an ask over the limit with the reason `budget`. It holds the same ask from the same sender to the same recipient on the same task as `duplicate`. Rookery compares the question in lower case, with only its letters and digits and single spaces. A question with no letters or digits is never a duplicate. This applies inside `screener.duplicate_window_s` (3600 seconds).

**Injection and worker gates.** A fixed set of patterns finds text that tries to change instructions, show the system prompt, ask for keys, run piped shell commands or hide text in base64. A match is `injection-suspect`. The gates read `self_check` in the block.

- G1: `task_complete` is true.
- G2: `gap` is empty.
- G3: `gap_in_my_scope` is true.
- G5: the ask is not allowed, for example over budget.

In live mode a gate anomaly escalates the mail to a person.

**The T2 command.** `screener.t2.command` is a list of arguments (no shell). Rookery sends escalated mail to it, up to `screener.t2.batch` at a time, as JSON lines on stdin: one envelope with a `mail_id` on each line. The command prints one JSON line for each mail: `{"mail_id": 812, "label": "deliver", "reason": "..."}`. T2 takes only mail that a screener judgement escalated, not gate anomalies. The envelope holds the ask text, so use only a command that you trust with that text. Name it with `screener.t2.name`.

**Configuration.** The `screener` block in `~/.rookery/config.json`. Each missing key has a default:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Screening on for every session. Off: no pass starts when mail arrives. A shadow `t1` check calls the model, so you choose this. |
| `checks` | all `shadow` | Mode of each check. |
| `bands.t1` | `null` | `{"accept": N, "review": N}`, numbers from 0 to 1. |
| `base_url`, `model` | the model host, `tev1:4b` | The `t1` model. |
| `hunch.model`, `hunch.url` | `qwen2.5:3b`, `null` | The second opinion. |
| `t2.command`, `t2.name`, `t2.batch` | `null`, `null`, 20 | The T2 command. |
| `timeout_s` | 300 | Time for one model call. |
| `lease_s` | 960 | A claim with no answer returns to the queue. Must be at least 3 times `timeout_s` plus 30. One screening can make three model calls. |
| `max_age_s` | 86400 | The screener skips older mail. |
| `ask_budget`, `duplicate_window_s` | 20, 3600 | See above. |
| `review_mode` | `pre` | `pre` holds a mail until the screener decides. `post` delivers it and sends a notice. Set `review_mode.agents.NAME` for one recipient. |

**PromptChain is optional.** The screener uses PromptChain when it imports. If it does not, the screener uses a standard-library copy with the same contract. `rookery screen status` shows which one runs. It counts escalated mail from the inbox. `rookery screen escalated` lists escalated mail and shows no body.

**Bad config.** If `config.json` is invalid or unreadable, the T0 checks and the screening hold turn off. The gate logs it. The screener escalates mail that waits for screening.

**Session names.** `rookery session new` and `rookery up` with no name make a name of three words, such as `calm-amber-heron`.

## Federation — multiple mailrooms (`node@mailroom`)

Run several independent mailrooms (one per machine / trust domain) and relay
between them — the mental model is **email**. Address with **`node@mailroom`**
(a bare `node` = local). Each mailroom runs `relay.py`, which forwards mail
addressed to a remote mailroom to that mailroom's sidecar, rewriting the sender
to `<orig>@<self>` so replies route home. Mailrooms resolve via a directory
(see `mailrooms.example.json`):

```json
{ "home": {"url": "http://<host-a>:8765", "token": "..."},
  "mac":  {"url": "http://<host-b>:8765", "token": "..."} }
```
Set `ROOKERY_MAILROOMS` (directory path) and `ROOKERY_MAILROOM` (this box's id).
`./demo_federation.sh` runs two mailrooms on localhost: `alice@home → bob@mac`
crosses the relay, bob replies, and the reply comes back. A hop count
(`relay/<n>`, max 4) limits loops. Nodes, the sidecar, `MAILTO:` directives, and the
MCP `send` tool all accept `node@mailroom` unchanged — only the relay is new, so
keep `node` ids `@`-free.

Undeliverable mail — an **unknown** mailroom, or a known one **unreachable** after
`MAX_ATTEMPTS` retries — goes to a **dead-letter queue** (`status='dlq'`). The
sender gets an alert. Inspect and recover with `relay.py --list-dlq` / `--requeue-dlq <id>`.

## Security is a swappable module

All security decisions route through a single `SecurityPolicy` (`security.py`), so
you can **add a hardened layer later** without touching the sidecar, nodes,
or CLIs. Decision points: `authenticate` (transport auth) · `is_public_path` ·
`security_schemes` (what the agent card advertises) · `authorize(principal,
action, target)` · `inspect_inbound(sender, recipient, body)` (the
prompt-injection / content seam) · `mint_pointer` / `resolve_secret` (credentials).

The default (`DefaultPolicy`) is exactly today's behavior (optional bearer token,
allow-all authz, pass-through inspection, env/keychain creds). To harden, subclass
and install it:

```python
# mypolicy.py
import security
class Hardened(security.SecurityPolicy):
    def authenticate(self, headers): ...        # per-node identity / mTLS / OAuth
    def authorize(self, principal, action, target): ...   # per-skill access control
    def inspect_inbound(self, sender, recipient, body): ...  # scan for injection
```
```bash
ROOKERY_SECURITY="mypolicy:Hardened" python3 mailroom_server.py --host 0.0.0.0 --allow-public --token "$ROOKERY_TOKEN"
```

Start rules for the mailroom (`mailroom_server.py` and `rookery serve`): it binds
`127.0.0.1` by default. On a local host it needs no token. A public `--host` needs
`--allow-public` and a token (`--token` or `$ROOKERY_TOKEN`).
```bash
```

**`inspect_inbound` covers ALL mail.** The A2A boundary screens at send-time.
**Every node also screens its inbound at the consuming end** (before the agent sees
it), so internal node→node mail gets the same check. The mailroom quarantines rejected mail and
tells the sender. See `policy_example.py` for a hardened reference (blocks
`rm -rf` / `DROP TABLE` / injection phrases; denies tasking `vault`) and
`./demo_security.sh` for a node blocking a dangerous message. This module is the
seam for the full hardened security layer.

**`policy_hardened.py` is that hardened layer** — swap it in with
`ROOKERY_SECURITY="policy_hardened:HardenedPolicy"`. Instead of one shared token,
**every principal has its own bearer token (per-node identity)** plus an ACL of
which nodes it may task (`may_task`, `"*"` = all). The token identifies the
caller. The policy rejects an unknown token (no anonymous fallthrough). It denies tasking a node
outside the ACL and names the principal. Config is a JSON registry
pointed to by `ROOKERY_NODE_IDENTITY` (see `node_identity.example.json`, kept out
of git). The authenticated principal goes into `authorize`, so authz is
per-caller, not global. `./demo_hardened.sh` proves it: no token → 401, guest →
task `researcher` OK but `reviewer` denied, architect → `reviewer` OK.

**Invite / handshake.** With HardenedPolicy installed, the sidecar exposes
`POST /invite` — any authenticated principal can mint a fresh per-node bearer
token (with TTL) for a new peer. The response includes the URL, the new
`node_id`, the token, and the sidecar's pinned Ed25519 **card pubkey**.
`rookery_join.py invite.json --mode {verify,mcp,loop}` consumes it on the
peer: TOFU pin-check against the served card, then either print the
`~/.claude.json` snippet or start the legacy `mailctl loop` directly. `./demo_invite.sh`
proves the round-trip — including refusing a tampered invite (pubkey flipped).

**One-command join (`rookery join`).** The easy wrapper around the
invite + MCP wiring. On A, `rookery serve` starts the sidecar AND an
interactive approval watcher. It also announces presence via **mDNS** (Linux:
`avahi-browse`, macOS: `dns-sd`, Windows: `dns-sd.exe`/Bonjour) **with a
UDP-broadcast fallback** when you have no DNS-SD browser. On the peer
B, one bootstrap (`curl -sSL http://A:8765/bootstrap | python3 -`) installs
`~/.local/bin/rookery`, then `rookery join` (no args) discovers A on the LAN,
generates a 3-word slug, knocks on A's `/join`, waits for A's "approve?
[y/N]" prompt. The slug is the visual check: does the joining machine show
this same code? Then it TOFU-pins A's Ed25519 card pubkey to
`~/.rookery/known_hosts`, and auto-merges the `rookery` MCP server entry
into `~/.claude.json` (with `.bak`). A pubkey change later fires an
SSH-style `REMOTE HOST IDENTIFICATION HAS CHANGED` warning and refuses
to update the config. `./demo_up.sh` proves the round-trip end-to-end.

**Out-of-band approval notifications.** With `rookery serve` running, set
`ROOKERY_NOTIFY_METHOD` to `ntfy`, `pushover`, or `webhook` (default `none` =
silent). The mailroom then pushes to your phone when a peer asks
to join, so you can approve from anywhere. The notification carries the 3-word
slug and a clickable link to a mobile-friendly approval form at
`GET /approve?request_id=<rid>` (the GET form is public; the POST that actually
mints the token stays bearer-auth-gated). Env knobs: `ROOKERY_NTFY_TOPIC` /
`ROOKERY_PUSHOVER_TOKEN` + `ROOKERY_PUSHOVER_USER` / `ROOKERY_NOTIFY_WEBHOOK_URL` +
`ROOKERY_NOTIFY_WEBHOOK_TOKEN`. Notifier failures never block a join. `notifier.py`
is the dispatcher; see QUICKSTART Scenario 4 for the wiring.

## Files

| File | Role |
|---|---|
| `schema.sql` | the mailroom tables (single source of truth) |
| `rookery.py` | shared lib: connect/init, nodes, mail, credentials |
| `node_runner.py` | the own-loop node (engines: mock / `claude -p` / `codex exec` / `gemini -p`; `--lifecycle`, `--allowed-tools`) |
| `sdk_node.py` | warm long-lived Claude session (Agent SDK) — `engine=claude-sdk`, kills cold start |
| `postmaster.py` | the wake rules (`wake_one`) and the session sweep (`rookery sweep`); legacy mode B with `--nodes`: per-node engines (incl. `claude-sdk`), `--persistent`, `--max-warm` |
| `rookery_wake.py` | `rookery wake`: one wake for one agent under a lock; the detached spawn and the event hook `after_pending` |
| `rookery_remote.py` | `rookery check`: the remote check of the mailroom and its bounded follower |
| `send_mail.py` | legacy: drop a message into the mailroom (use `rookery send`) |
| `mesh_approve.py` | human side of the CIBA credential gate |
| `security.py` | **swappable** SecurityPolicy: auth · authz · credential mint/resolve · inbound message inspection |
| `policy_example.py` | reference hardened policy (template) — blocks injection patterns, denies a sensitive node |
| `policy_hardened.py` | **hardened** SecurityPolicy — per-node identity (per-principal bearer token) + per-target ACL; `ROOKERY_SECURITY=policy_hardened:HardenedPolicy` |
| `node_identity.example.json` | template for the per-node token + `may_task` ACL registry (copy to `node_identity.json`) |
| `gen_card_key.py` / `verify_card.py` | generate the Ed25519 card key / verify a served card's signature |
| `rookery_join.py` | consume an invite (`/invite`) — pin-check the card, then print the `~/.claude.json` snippet or start the legacy `mailctl loop` directly |
| `rookery_cli.py` | unified CLI — `rookery serve`, `rookery join`, `rookery approve` |
| `identity.py` | per-node Ed25519 keypair at `~/.rookery/id_ed25519` |
| `known_hosts.py` | SSH-style TOFU pin store at `~/.rookery/known_hosts` (`ok` / `changed` / `unknown`) |
| `discovery.py` | LAN discovery — mDNS shell-out (`avahi-browse` / `dns-sd`) + UDP-broadcast fallback |
| `handshake.py` | in-memory join-request queue (`/join` -> approval -> minted token) |
| `mailroom_handshake.py` | HTTP route handlers for `/join`, `/approve`, `GET /approve` form, `/pending` |
| `notifier.py` | out-of-band approval notifications (ntfy / Pushover / webhook). Silent no-op when `ROOKERY_NOTIFY_METHOD` is unset or `none` |
| `cli_serve.py` | `rookery serve` subcommand handler |
| `cli_join.py` | `rookery join` subcommand handler |
| `cli_workspace.py` | `rookery up`, `rookery finish`, `rookery gc` and `rookery regen` |
| `rookery_team.py` | read and check a team file |
| `rookery_worktree.py` | one git worktree for each writer |
| `rookery_guard.py` | the `guard` and `watch` hooks |
| `rookery_views.py` | the generated files: workspace files, `ROOKERY.md` and the views |
| `cli_approve.py` | `rookery approve` subcommand handler |
| `cli_known_hosts.py` | `rookery known-hosts list` / `forget <host> [--yes]` for managing TOFU pins |
| `config_manager.py` | idempotent `~/.claude.json` merge with `.bak.<epoch>` |
| `bootstrap.py` | builds the peer zipapp; `/bootstrap` endpoint serves the installer |
| `monitor.sh` | live DB view (the Monitor node) |
| `mailroom_server.py` | stdlib HTTP sidecar: mailroom API + **A2A** card / JSON-RPC (`message/send`, `tasks/get`, `message/stream` SSE) + bearer auth |
| `mailctl.py` | legacy: one-file stdlib HTTP client for a peer (copy to the Mac; `send` / `inbox` / `loop`). Use `rookery send`, `rookery inbox` and `rookery ack` |
| `rookery_mcp.py` | MCP server — `send` / `check_inbox` / `ack` / `await_message` / `roster` tools for a Claude Code session + subagents |
| `relay.py` | federation relay — forwards `node@remote` mail to that mailroom's sidecar (directory: `mailrooms.json`) |
| `terminal_node.py` | substrate A — bridges a human/terminal-agent in a pane into the mesh (injector: tmux / wezterm / zellij) |
| `webhook_sink.py` | tiny test receiver for A2A push notifications (prints each POST) |
| `gen_cert.sh` | generate a self-signed cert for the HTTPS sidecar (`--tls-cert`/`--tls-key`) |
| `run_all_demos.sh` | run the whole demo suite end to end + PASS/FAIL matrix (`--all` adds paid demos) |
| `pause.sh` / `resume.sh` | OS freeze/continue a node (the "video button") |
| `demo.sh` | mode A proof (self-polling nodes) |
| `demo_postmaster.sh` | mode B proof (agents asleep, postmaster wakes them) |
| `demo_research.sh` | research-queue proof (mock): dispatch + handoff + persistence |
| `demo_real.sh` | real Claude ↔ Codex handoff |
| `demo_real_research.sh` | real DeepLake tool-use → Claude → Codex → human |
| `cleanup.sh` | reset the mailroom |
| `docs/` | QUICKSTART · COOKBOOK · STORIES · `rookery-explained.html` (visual explainer) + the research / paired-debate reports |

## Known v1 limitations (deliberate)

- **Durable delivery + slow models:** mail goes `pending` → `inflight` (on claim) → `done` (on ack). A node **heartbeats while it works**. The postmaster requeues `inflight` mail **only after the claiming node goes silent** (`NODE_DEAD_AFTER`, judged by `last_seen`). A slow model (e.g. Ollama taking minutes) keeps its claim and nobody double-processes it. The mesh is async, so a sender never blocks on a slow node; the reply lands whenever. Mode A (no postmaster) has no requeuer.
- **Multi-host** is via `mailroom_server.py` (stdlib HTTP sidecar over the mailroom) + `mailctl.py` (one-file peer client). Run the sidecar with `--host 0.0.0.0 --allow-public` and a token (`--token` or `$ROOKERY_TOKEN`). A peer (e.g. the Mac Studio) copies `mailctl.py` (legacy; the current commands are `rookery send`, `rookery inbox` and `rookery ack`) and runs `loop --url http://<host>:8765 --node <name>`. Never put the SQLite file on a network share — peers talk to the sidecar over TCP.
- **Credential `token_ref` is a pointer**, not a secret — resolved at use time via `rookery.resolve_secret()` (`env://VAR` or `keychain://service/account` via libsecret `secret-tool`). The secret never touches the DB.

## Roadmap

Done: prove the loop · CIBA real keychain/env resolution · `@mention` routing ·
durable delivery (in-flight/ack) · warm SDK session · multi-host HTTP sidecar ·
A2A agent card + `message/send`/`tasks/get` + `message/stream` (SSE) + bearer auth.
MCP bridge (Claude Code sessions/subagents join the mesh) + federation
(`node@mailroom` across multiple mailrooms via relays).
terminal node (substrate A) — a human/terminal-agent joins via a pane (tmux/wezterm/zellij).
A2A push notifications (webhook); TLS sidecar (`--tls-cert/--tls-key` + `gen_cert.sh`)
and tunnel guidance for internet use.
Relay **dead-letter queue** (retries + sender alert + requeue);
A2A **signed agent cards** (Ed25519 — `gen_card_key.py` / `verify_card.py`);
**hardened SecurityPolicy** with per-node identity + per-target authorization
(`policy_hardened.py`). **Backlog + the three follow-ups cleared.**

**Possible future work (noted only, no one asked for it):**
- **Out-of-band key pinning** for signed cards (JWKS / DID) — today the card carries its own public key (proves integrity, not identity); pin it externally for true identity.
- **mTLS per node** — client-cert identity at the transport layer, complementing the per-principal bearer tokens.
- **Rotation / revocation** for the per-node token registry — expiry, rotation, and a revocation list on the identity store.
