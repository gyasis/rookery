# Rookery

Durable mail for coding agents. Agents in one Rookery session (Claude Code, Codex, pi, Kimi,
an agent started by hand) send each other mail **unprompted**, and Rookery wakes an idle
agent when mail needs action.

> Codename **Rookery** (where messenger birds roost). Internally: the durable
> store is the **mailroom**, the wake step is the **postmaster**, and the
> design rationale is in [`docs/`](docs/index.html).

## Mental model — this is async **mail**, not instant messaging

Rookery is durable, asynchronous **mail**, checked in **bursts** — like email,
not a phone call. Messages persist in the mailroom (SQLite) until the recipient
reads them. **Nothing is lost, whenever that happens.**

Every sender is an identity in a Rookery session. An agent sends with a `rookery` block at the
end of its turn, or with `rookery send`. It reads with `rookery inbox` and marks mail read with
`rookery ack ID ...`. A send with no session is refused: Rookery stores nothing and says how to
get a session.

Rookery types one notice into the pane of an idle agent when mail needs action. See
[How mail wakes an agent](#how-mail-wakes-an-agent).

## Install

One self-contained binary — the **same** `rookery` command runs a project's sessions, hosts a
mailroom (`rookery serve`) or joins one (`rookery join`).

```bash
git clone https://github.com/gyasis/rookery.git ~/Documents/code/rookery
cd ~/Documents/code/rookery
python3 -c "import bootstrap; bootstrap.build_pyz('$HOME/.local/bin/rookery')"
rookery --help
```

Or, from a running mailroom (no git): `curl -sSL http://<host>:<port>/bootstrap | python3 -`.

Core is Python-3 **stdlib only**. Run `pip install cryptography` only for
the TLS / signed-card / hardened-policy security tier. Full matrix:
**[`docs/QUICKSTART.md` → Installation](docs/QUICKSTART.md#installation)**.

## Docs

- **[`docs/QUICKSTART.md`](docs/QUICKSTART.md)** — paste-and-run setup: a session, a team, the screener and a mailroom for other machines.
- **[`skills/rookery/SKILL.md`](skills/rookery/SKILL.md)** — the instructions an agent reads to use Rookery mail.
- **[`docs/rookery-explained.html`](docs/rookery-explained.html)** — single-page visual explainer. It describes the node mesh before 0.7.
- Design rationale: [`docs/index.html`](docs/index.html) (links the two research reports + paired-debate transcript).
- `plugin_herdr/` — optional herdr console: `rookery herdr status|focus|notify`, the pane probes of the wake step and a toast sink. If you delete the directory, core does not change.

## The mailroom (other machines)

`rookery serve` (or `mailroom_server.py`) exposes the mailroom over HTTP. A remote node joins with
`rookery join URL` and reads its mail with `rookery check` (`GET /inbox?node=NODE`, then `/claim`
and `/ack`). **A remote node cannot send.** The mailroom has no session for a remote sender, so
`POST /send` answers 403 `no-session` and A2A `message/send` answers JSON-RPC error -32602.
Mail to a remote node comes from a session on the mailroom machine: `rookery send NODE "SAY"`
reaches it when an identity of that name is in the sender's session.

One remote node can send. Start the mailroom with `--token-node SESSION:NAME` (it needs a token).
Then a `POST /send` that carries the token sends as identity NAME in the open session SESSION. The
`sender` field must be NAME or absent, else 403 `wrong-sender`. The usual gate checks apply: the
recipient must be in SESSION, and the loop limits count. Create the session and its identities first:
`rookery session new --name SESSION`, then `rookery identity new NAME --session SESSION` for NAME and
for each local peer. A2A `message/send` still refuses.

- **Discover:** `GET /.well-known/agent-card.json` returns an A2A Agent Card.
- **Auth:** start the mailroom with `--token <tok>` (or `$ROOKERY_TOKEN`). Then every endpoint
  except `/health` and the agent card needs `Authorization: Bearer <tok>`.

> **Auth ≠ encryption.** A bearer token authenticates but sends in clear text.
> On a trusted **LAN** that's fine. Over the **internet**, add **TLS**:
> `./gen_cert.sh` then `mailroom_server --tls-cert rookery-cert.pem --tls-key
> rookery-key.pem --token …` → HTTPS; **TLS + token = auth + encryption**.
> (Self-signed clients set `ROOKERY_INSECURE_TLS=1`; for real internet use a
> CA-signed cert or a **tunnel** — Tailscale / SSH `-L` / Cloudflare.)

- **Signed cards:** serve with `--card-key <ed25519.pem>` (`gen_card_key.py`) and
  the agent card carries an **Ed25519 signature** + public key; verify with
  `verify_card.py <url>` (`./demo_signed_card.sh`).

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

`python3 postmaster.py` runs the outbox scan and the idle wake for every open session, once every 300 seconds.

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

An agent can also have `"model": "glm-5.3:cloud"`. Rookery then starts the harness with that model, from the `model_cmd` entry of the harness in the configuration. The default for Claude is `claude-ollama {model} --`. It sets `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to the real context window of the model and then runs `ollama launch claude`. Without it, Claude Code assumes 200k tokens for a model it does not know. `claude-ollama` stops if it cannot find the window of the model. Set `"cmd": ["omp-minimax"]` to give the exact start command instead. `cmd` replaces the default command and wins over `model`. Kimi has no `model_cmd`, so a Kimi agent with `model` and no `cmd` fails the check.

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

The wake applies the rules of the postmaster. These are the push gate, the record of announced mail in `postmaster/pushed/`, the idle check, `busy-stale`, the herdr probe and the back-off. Only one wake runs for an agent at a time. It holds `postmaster/lock/AGENT_ID.lock`, and a second wake logs `wake-busy` and exits. After a push the holder reads the pending mail again, at most 3 rounds. Each wake writes one log line with its outcome. The outcomes: `pushed`, `nothing`, `busy`, `wake-busy`, `backoff-wait`, `wake-failed`, `no-idle-state`, `pane-gone`, `draft-in-box`, `no-wake-binding`, `no-root`, `lock-failed` and `unsafe-name`.

The wake does not type into an input box that has text. `herdr agent prompt` adds to the text in the box, so a draft and the notice would go to the agent as one prompt. Before it types, the wake reads the input line of the pane. If the line has text, the wake logs `draft-in-box` with the length of the text and tries again in the next round. The check knows the input line of Claude, Codex and Kimi. It drops dim text, because that is a placeholder. For other agents, or when the input line is not on the screen, the wake types as before. Set `draft_wait` to `false` in the config to turn the check off.

**Agents with no turn hook.** Only the Claude Code hooks and the pi/omp extension write `state/turn.json`. For any other agent (Kimi, Codex, an agent started by hand), the wake asks herdr for the state of its `herdr-pane` binding. `idle` and `done` get the notice, and `working` waits. The notice then gives the full command, `ROOKERY_SESSION=S ROOKERY_NODE=NAME rookery inbox`, so the agent needs no Rookery variables in its process. Set `herdr_probe` to `false` to turn this off. The back-off record is `postmaster/backoff/AGENT_ID.json`, so it lasts between wakes.

The wake finds the project root in the `rookery-root` binding of the session, else in `ROOKERY_ROOT`, else in `--root`. With none, it logs `no-root`, and the sweep covers the mail.

**The sweep.** An event can get lost: a person kills the wake, the machine sleeps, or the database is busy. One sweep for each session catches the mail. `rookery up` starts it. You can start it by hand: `rookery sweep --session S [--root R]`, or `postmaster.py --session S`. Each round it scans the outbox drop-box and wakes the agents of the session that have mail with no notice. It also runs one screening pass when screening is on, or when a mail waits as `held` for screening. Then it sleeps 120 seconds. Set the interval with `rookery up --sweep SECONDS`, with `sweep_s` in the team file, or with `sweep_s` in `~/.rookery/config.json`. The first one found wins. The sweep raises a value below 60 to 60 with one `NOTE:` line. While it sleeps, it reads the state of its session every 5 seconds, so it stops within 5 seconds of `rookery session end`. The sweep reads only the database and calls no model. It ends when the session ends, when a new database file replaces the old one, or when the project root is gone. One sweep runs for each session. A second one logs `sweep-busy` and exits.

**The remote check.** The mailroom runs on one machine. An agent on another machine has `ROOKERY_URL`, `ROOKERY_TOKEN` and `ROOKERY_NODE` from `rookery join`. It pulls. `rookery check` calls `GET /inbox?node=NODE` with the Bearer token, applies the push rules, and types one notice into its own pane through the `wake` config. It records the announced IDs in `~/.rookery/remote/NODE.json`. The call refuses a redirect, reads at most 1 MB and waits at most 20 seconds. A failed pane command sets a back-off from 30 to 600 seconds. `rookery check --follow` starts one follower for the node: 5 checks per trigger, 300 seconds apart. It stops at the first notice. A trigger while it sleeps restarts the count and starts one check at once. The trigger is the Stop hook (on a machine with `ROOKERY_URL`). An agent with no hooks has no trigger. A remote check calls no model.

**Screening.** Screening is off by default (`screener.enabled: false`). A shadow `t1` check calls the model, so an update must not start model calls that nobody chose. Set `screener.enabled` to `true` in `~/.rookery/config.json` for every session, or `"screener": true` in the team file for one session. Then the gate starts one `rookery screen run --session S --once` after it stores a mail, under `postmaster/lock/screen-SESSION_ID.lock`. The sweep runs one pass each round. A mail that a live check holds for screening always gets the pass.

**Kill switch.** `ROOKERY_WAKE=off` in the environment stops every event spawn. No wake, no screening pass, no sweep from `rookery up`, and no remote check from a hook start. `rookery wake`, `rookery sweep` and `rookery check` still run when you call them.

**Cost.** No wake path calls a model. The number of wakes grows with the mail, not with the number of agents. An idle hour costs 12 sweep rounds and 0 model calls.

**Operation.**

- After a deploy, restart `rookery-mailroom.service`. Until it restarts, it runs the old code.
- A sweep does not survive a reboot, or a logout that kills it. Run `rookery up TEAM_FILE --session NAME` again. It resumes the session and starts the sweep.
- One sleeping sweep process runs for each open session, until `rookery session end` ends the session.
- A session that `rookery init` or `rookery session new` made gets event wakes: the spawning side passes the project root. No sweep starts for it unless you run `rookery sweep --session NAME --root ROOT`.
- The Stop hook starts the remote check only when `ROOKERY_URL` and `ROOKERY_NODE` are in the shell environment of the agent.

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

## Security is a swappable module

All security decisions route through a single `SecurityPolicy` (`security.py`), so
you can **add a hardened layer later** without touching the mailroom or the CLIs. Decision points: `authenticate` (transport auth) · `is_public_path` ·
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

**`inspect_inbound` covers ALL mail.** The gate calls it for every send and quarantines
rejected mail with a bounce to the sender. See `policy_example.py` for a hardened reference
(blocks `rm -rf` / `DROP TABLE` / injection phrases; denies tasking `vault`).

**`policy_hardened.py` is that hardened layer** — swap it in with
`ROOKERY_SECURITY="policy_hardened:HardenedPolicy"`. Instead of one shared token,
**every principal has its own bearer token (per-node identity)** plus an ACL of
which nodes it may task (`may_task`, `"*"` = all). The token identifies the
caller. The policy rejects an unknown token (no anonymous fallthrough). It denies tasking a node
outside the ACL and names the principal. Config is a JSON registry
pointed to by `ROOKERY_NODE_IDENTITY` (see `node_identity.example.json`, kept out
of git). The authenticated principal goes into `authorize`, so authz is
per-caller, not global.

**Invite.** With HardenedPolicy installed, the mailroom exposes `POST /invite`. Any
authenticated principal can mint a fresh per-node bearer token (with TTL) for a new peer. The
response includes the URL, the new `node_id`, the token, and the mailroom's pinned Ed25519
**card pubkey**.

**One-command join (`rookery join`).** On A, `rookery serve` starts the mailroom AND an
interactive approval watcher. It also announces presence via **mDNS** (Linux:
`avahi-browse`, macOS: `dns-sd`, Windows: `dns-sd.exe`/Bonjour) **with a
UDP-broadcast fallback** when you have no DNS-SD browser. On the peer
B, one bootstrap (`curl -sSL http://A:8765/bootstrap | python3 -`) installs
`~/.local/bin/rookery`, then `rookery join` (no args) discovers A on the LAN,
generates a 3-word slug, knocks on A's `/join`, waits for A's "approve?
[y/N]" prompt. The slug is the visual check: does the joining machine show
this same code? Then it TOFU-pins A's Ed25519 card pubkey to
`~/.rookery/known_hosts`, writes the token to `~/.rookery/remote/NODE.token` (mode 600) and
prints the `ROOKERY_URL`, `ROOKERY_NODE` and `ROOKERY_TOKEN` lines for `rookery check`. A pubkey
change later fires an SSH-style `REMOTE HOST IDENTIFICATION HAS CHANGED` warning and stops.

**Out-of-band approval notifications.** With `rookery serve` running, set
`ROOKERY_NOTIFY_METHOD` to `ntfy`, `pushover`, or `webhook` (default `none` =
silent). The mailroom then pushes to your phone when a peer asks
to join, so you can approve from anywhere. The notification carries the 3-word
slug and a clickable link to a mobile-friendly approval form at
`GET /approve?request_id=<rid>` (the GET form is public; the POST that actually
mints the token stays bearer-auth-gated). Env knobs: `ROOKERY_NTFY_TOPIC` /
`ROOKERY_PUSHOVER_TOKEN` + `ROOKERY_PUSHOVER_USER` / `ROOKERY_NOTIFY_WEBHOOK_URL` +
`ROOKERY_NOTIFY_WEBHOOK_TOKEN`. Notifier failures never block a join. `notifier.py`
is the dispatcher; see QUICKSTART for the wiring.

## Files

| File | Role |
|---|---|
| `schema.sql` | the mailroom tables (single source of truth) |
| `rookery.py` | shared lib: connect/init, mail, the remote node rows, credential requests |
| `postmaster.py` | the wake rules (`wake_one`) and the session sweep (`rookery sweep`) |
| `rookery_wake.py` | `rookery wake`: one wake for one agent under a lock; the detached spawn and the event hook `after_pending` |
| `rookery_remote.py` | `rookery check`: the remote check of the mailroom and its bounded follower |
| `security.py` | **swappable** SecurityPolicy: auth · authz · credential mint/resolve · inbound message inspection |
| `mail_gate.py` | the one door for every send: session check, recipient, screening, loop breaker |
| `policy_example.py` | reference hardened policy (template) — blocks injection patterns, denies a sensitive node |
| `policy_hardened.py` | **hardened** SecurityPolicy — per-node identity (per-principal bearer token) + per-target ACL; `ROOKERY_SECURITY=policy_hardened:HardenedPolicy` |
| `node_identity.example.json` | template for the per-node token + `may_task` ACL registry (copy to `node_identity.json`) |
| `gen_card_key.py` / `verify_card.py` | generate the Ed25519 card key / verify a served card's signature |
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
| `bootstrap.py` | builds the peer zipapp; `/bootstrap` endpoint serves the installer |
| `mailroom_server.py` | stdlib HTTP mailroom for other machines: `/inbox`, `/claim`, `/ack`, join and approve, the **A2A** card, bearer auth. `/send` and A2A `message/send` refuse with `no-session` |
| `gen_cert.sh` | generate a self-signed cert for the HTTPS sidecar (`--tls-cert`/`--tls-key`) |
| `cleanup.sh` | reset the mailroom |
| `demo_signed_card.sh` | signed agent card: a valid card verifies, a tampered card fails |
| `docs/` | QUICKSTART · `rookery-explained.html` (visual explainer) + the research / paired-debate reports |

## Known limitations (deliberate)

- **No remote send.** A remote node reads its mail through the mailroom, but the mailroom
  refuses mail from it (`no-session`). Mail between machines needs a session-aware mailroom
  send, which does not exist yet.
- **Never put the SQLite file on a network share** — peers talk to the mailroom over TCP.
- **Credential `token_ref` is a pointer**, not a secret — resolved at use time via
  `rookery.resolve_secret()` (`env://VAR` or `keychain://service/account` via libsecret
  `secret-tool`). The secret never touches the DB.

## Possible future work

- **Session-aware remote send** — map a mailroom principal to an identity in a session, so a
  remote node can send again.
- **Out-of-band key pinning** for signed cards (JWKS / DID) — today the card carries its own public key (proves integrity, not identity); pin it externally for true identity.
- **mTLS per node** — client-cert identity at the transport layer, complementing the per-principal bearer tokens.
- **Rotation / revocation** for the per-node token registry — expiry, rotation, and a revocation list on the identity store.
