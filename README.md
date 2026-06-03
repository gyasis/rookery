# Rookery

A local-first mesh that lets multiple coding agents (headless Claude nodes, raw-API
daemons, and terminal sessions) talk to each other **unprompted** — and lets a
sub-agent **phone home** mid-task for more context or for credentials it lacks,
without ever putting a secret in a prompt.

> Codename **Rookery** (where messenger birds roost). Internally: the durable
> store is the **mailroom**, the watcher/loop is the **postmaster**, and the
> design rationale is in [`docs/`](docs/index.html).

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
(`rookery serve`) *or* joins one (`rookery up`). Role is chosen per machine at
run time, not at install time.

```bash
git clone https://github.com/gyasis/rookery.git ~/Documents/code/rookery
cd ~/Documents/code/rookery
python3 -c "import bootstrap; bootstrap.build_pyz('$HOME/.local/bin/rookery')"
rookery --help          # serve · up · approve · known-hosts
```

Or, from an already-running host (no git): `curl -sSL http://<host>:<port>/bootstrap | python3 -`.

Core is Python-3 **stdlib only**; `pip install cryptography` is needed only for
the TLS / signed-card / hardened-policy security tier. Full matrix and the
host-vs-peer model: **[`docs/QUICKSTART.md` → Installation](docs/QUICKSTART.md#installation)**.

## Docs

- **[`docs/QUICKSTART.md`](docs/QUICKSTART.md)** — paste-and-run setup for the three deployment shapes: same machine · same network · internet (full security stack).
- **[`docs/COOKBOOK.md`](docs/COOKBOOK.md)** — 22 small recipes (start the mailroom, wire each engine, federate, sign cards, install the hardened policy, mint invites, …).
- **[`docs/NODE_BRIEFING.md`](docs/NODE_BRIEFING.md)** — the instruction block you hand each agent so it knows its name, its siblings, and how to message them (CLI + MCP variants, with a filled `architect ↔ triage` example).
- **[`docs/STORIES.md`](docs/STORIES.md)** — narrative use-cases with the commands lifted from the matching demo (Claude↔Codex review, Mac Studio joins over LAN, external A2A agent, federation, CIBA credential phone-home, three-vendor parallel work).
- **[`docs/rookery-explained.html`](docs/rookery-explained.html)** — single-page visual explainer (model · deploy shapes · A2A · security tiers · engines).
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

Mode B is the "a background poller says *you've got mail* and wakes the sleeping
agent" model — a truly paused agent can't poll itself, so the postmaster is its
alarm clock. v1 (A) is untouched; pick per situation, same DB underneath.

## Agent lifecycle — ephemeral vs persistent

A second axis, independent of transport mode: **what an agent remembers**.

| Lifecycle | Memory | Cost when idle | Use for |
|---|---|---|---|
| **`ephemeral`** | none — gets only the task brief, runs once, vanishes | $0 (gone) | throwaway, token-saving subtasks |
| **`persistent`** | full thread **rehydrated from the mailroom** on each wake | $0 when reaped; warm during its idle window | orchestrators, standing "power-partner" specialists |

The key idea: **nothing stays warm forever, and nothing loses context that shouldn't.**
A persistent partner is *also* killed when idle — its context lives in the DB, so
it re-ups with full memory next time (context survives the kill, not the process).
To avoid cold-start churn during active work, persistent agents get a **warm
window** (`--idle-timeout`, e.g. 5–10 min): they stay up while work is flowing,
then sleep. The postmaster keeps a bounded **warm pool** (`--max-warm`, default 5)
and evicts the least-recently-used partner when full (it rehydrates later).

```bash
# architect = persistent power-partner (Claude), reviewer = ephemeral (Codex)
python3 postmaster.py --nodes architect=claude,reviewer=codex \
  --persistent architect --max-warm 5 --idle-timeout 600
```

> **Latency — solved via the Agent SDK.** Plain `claude -p` pays **~115s every
> call** in this environment (the user's SessionStart hooks), *not* fixed by
> model, MCP, or `--resume` (all measured). The fix is a **warm long-lived
> session** through the Claude Agent SDK with `setting_sources=[]` (hooks off):
> open the session once, reuse it per turn. Measured: **open 2s · first turn 7s ·
> warm turns ~2s** — vs ~115s/turn. Use `engine=claude-sdk` for persistent
> partners; the node is `sdk_node.py`. (Tool-using SDK nodes pass `mcp_servers`
> explicitly since `setting_sources=[]` also skips global MCP.)

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
./demo_up.sh              # one-command join: `rookery up` (bootstrap installer + handshake + TOFU pin + auto-config)
./demo_tls.sh             # HTTPS sidecar (self-signed cert) + token = auth + encryption
# paid (real models):
./demo_real.sh            # Claude architect ↔ Codex reviewer
./demo_real_research.sh   # Claude researcher fires the DeepLake MCP tool → Codex writer → human
./demo_real_sdk.sh        # warm SDK node: 3 sequential turns, ~7s then ~2s (vs ~115s each)
./demo_real_sdk_research.sh # warm SDK + DeepLake MCP tool → Codex writer → human
./demo_real_team.sh       # 2×Claude + Codex + Gemini: architect fans out to all 3 IN PARALLEL
```

> **Warm SDK + tools:** a `claude-sdk` node attaches MCP servers explicitly via
> `--mcp <name>` (looked up in `~/.claude.json`), since `setting_sources=[]`
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
  `/a2a` + the REST endpoints require `Authorization: Bearer <tok>`; `/health`
  and the agent card stay public (the card advertises the `bearerAuth` scheme).
  `mailctl.py` sends the token from `--token`/`$ROOKERY_TOKEN`.

> **Auth ≠ encryption.** A bearer token authenticates but sends in clear text.
> On a trusted **LAN** that's fine. Over the **internet**, add **TLS**:
> `./gen_cert.sh` then `mailroom_server --tls-cert rookery-cert.pem --tls-key
> rookery-key.pem --token …` → HTTPS; **TLS + token = auth + encryption**.
> (Self-signed clients set `ROOKERY_INSECURE_TLS=1`; for real internet use a
> CA-signed cert or a **tunnel** — Tailscale / SSH `-L` / Cloudflare.)

- **Push:** include `configuration.pushNotificationConfig` `{url, token}` in
  `message/send`; the sidecar returns `submitted` immediately and **POSTs the
  completed Task to your webhook** when the node replies (no polling/streaming).
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
Tools: `send(to, body)` · `check_inbox()` · `await_message(timeout)` · `roster()`.
For a **remote** mailroom set `ROOKERY_URL` (+ `ROOKERY_TOKEN`) instead of `ROOKERY_DB`.

**CLI / scripting** — `send_mail.py`, `mailctl.py` (remote), `mesh_approve.py`;
agents drive these from Bash. Same mailroom underneath either way.

A **background subagent** + these tools = it talks to other agents
asynchronously while you keep working in the main context.

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
is relayed across, bob replies, and it's relayed back. Loops are bounded by a
hop count (`relay/<n>`, max 4). Nodes, the sidecar, `MAILTO:` directives, and the
MCP `send` tool all accept `node@mailroom` unchanged — only the relay is new, so
keep `node` ids `@`-free.

Undeliverable mail — an **unknown** mailroom, or a known one **unreachable** after
`MAX_ATTEMPTS` retries — goes to a **dead-letter queue** (`status='dlq'`) and the
sender is alerted; inspect/recover with `relay.py --list-dlq` / `--requeue-dlq <id>`.

## Security is a swappable module

All security decisions route through a single `SecurityPolicy` (`security.py`), so
a **hardened layer can be dropped in later** without touching the sidecar, nodes,
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
ROOKERY_SECURITY="mypolicy:Hardened" python3 mailroom_server.py --host 0.0.0.0
```

**`inspect_inbound` covers ALL mail:** the A2A boundary screens at send-time, and
**every node screens its inbound at the consuming end** (before the agent sees
it) — so internal node→node mail is vetted too. Rejected mail is quarantined and
the sender is told. See `policy_example.py` for a hardened reference (blocks
`rm -rf` / `DROP TABLE` / injection phrases; denies tasking `vault`) and
`./demo_security.sh` for a node blocking a dangerous message. This module is the
seam for the full hardened security layer.

**`policy_hardened.py` is that hardened layer** — swap it in with
`ROOKERY_SECURITY="policy_hardened:HardenedPolicy"`. Instead of one shared token,
**every principal has its own bearer token (per-node identity)** plus an ACL of
which nodes it may task (`may_task`, `"*"` = all). The token identifies the
caller; an unknown token is rejected (no anonymous fallthrough); tasking a node
outside the ACL is denied with the principal named. Config is a JSON registry
pointed to by `ROOKERY_NODE_IDENTITY` (see `node_identity.example.json`, kept out
of git). The authenticated principal is threaded into `authorize`, so authz is
per-caller, not global. `./demo_hardened.sh` proves it: no token → 401, guest →
task `researcher` OK but `reviewer` denied, architect → `reviewer` OK.

**Invite / handshake.** With HardenedPolicy installed, the sidecar exposes
`POST /invite` — any authenticated principal can mint a fresh per-node bearer
token (with TTL) for a new peer. The response includes the URL, the new
`node_id`, the token, and the sidecar's pinned Ed25519 **card pubkey**.
`rookery_join.py invite.json --mode {verify,mcp,loop}` consumes it on the
peer: TOFU pin-check against the served card, then either print the
`~/.claude.json` snippet or start `mailctl loop` directly. `./demo_invite.sh`
proves the round-trip — including refusing a tampered invite (pubkey flipped).

**One-command join (`rookery up`).** The frictionless wrapper around the
invite + MCP wiring. On A: `rookery serve` starts the sidecar AND an
interactive approval watcher AND announces presence via **mDNS** (Linux:
`avahi-browse`, macOS: `dns-sd`, Windows: `dns-sd.exe`/Bonjour) **with a
UDP-broadcast fallback** when no DNS-SD browser is installed. On the peer
B: one bootstrap (`curl -sSL http://A:8765/bootstrap | python3 -`) installs
`~/.local/bin/rookery`, then `rookery up` (no args) discovers A on the LAN,
generates a 3-word slug, knocks on A's `/join`, waits for A's "approve?
[y/N]" prompt (the slug is the visual check — does the joining machine show
this same code?), TOFU-pins A's Ed25519 card pubkey to
`~/.rookery/known_hosts`, and auto-merges the `rookery` MCP server entry
into `~/.claude.json` (with `.bak`). A pubkey change later fires an
SSH-style `REMOTE HOST IDENTIFICATION HAS CHANGED` warning and refuses
to update the config. `./demo_up.sh` proves the round-trip end-to-end.

**Out-of-band approval notifications.** With `rookery serve` running, set
`ROOKERY_NOTIFY_METHOD` to `ntfy`, `pushover`, or `webhook` (default `none` =
silent) and the mailroom fires a push to your phone whenever a peer requests
to join — so you can approve from anywhere. The notification carries the 3-word
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
| `postmaster.py` | mode B: central watcher; per-node engines (incl. `claude-sdk`), `--persistent`, `--max-warm` |
| `send_mail.py` | drop a message into the mailroom |
| `mesh_approve.py` | human side of the CIBA credential gate |
| `security.py` | **swappable** SecurityPolicy: auth · authz · credential mint/resolve · inbound message inspection |
| `policy_example.py` | reference hardened policy (template) — blocks injection patterns, denies a sensitive node |
| `policy_hardened.py` | **hardened** SecurityPolicy — per-node identity (per-principal bearer token) + per-target ACL; `ROOKERY_SECURITY=policy_hardened:HardenedPolicy` |
| `node_identity.example.json` | template for the per-node token + `may_task` ACL registry (copy to `node_identity.json`) |
| `gen_card_key.py` / `verify_card.py` | generate the Ed25519 card key / verify a served card's signature |
| `rookery_join.py` | consume an invite (`/invite`) — pin-check the card, then print the `~/.claude.json` snippet or start `mailctl loop` directly |
| `rookery_cli.py` | unified CLI — `rookery serve`, `rookery up`, `rookery approve` |
| `identity.py` | per-node Ed25519 keypair at `~/.rookery/id_ed25519` |
| `known_hosts.py` | SSH-style TOFU pin store at `~/.rookery/known_hosts` (`ok` / `changed` / `unknown`) |
| `discovery.py` | LAN discovery — mDNS shell-out (`avahi-browse` / `dns-sd`) + UDP-broadcast fallback |
| `handshake.py` | in-memory join-request queue (`/join` -> approval -> minted token) |
| `mailroom_handshake.py` | HTTP route handlers for `/join`, `/approve`, `GET /approve` form, `/pending` |
| `notifier.py` | out-of-band approval notifications (ntfy / Pushover / webhook). Silent no-op when `ROOKERY_NOTIFY_METHOD` is unset or `none` |
| `cli_serve.py` | `rookery serve` subcommand handler |
| `cli_up.py` | `rookery up` subcommand handler |
| `cli_approve.py` | `rookery approve` subcommand handler |
| `cli_known_hosts.py` | `rookery known-hosts list` / `forget <host> [--yes]` for managing TOFU pins |
| `config_manager.py` | idempotent `~/.claude.json` merge with `.bak.<epoch>` |
| `bootstrap.py` | builds the peer zipapp; `/bootstrap` endpoint serves the installer |
| `monitor.sh` | live DB view (the Monitor node) |
| `mailroom_server.py` | stdlib HTTP sidecar: mailroom API + **A2A** card / JSON-RPC (`message/send`, `tasks/get`, `message/stream` SSE) + bearer auth |
| `mailctl.py` | one-file stdlib HTTP client for a peer (copy to the Mac; `send` / `inbox` / `loop`) |
| `rookery_mcp.py` | MCP server — `send` / `check_inbox` / `await_message` / `roster` tools for a Claude Code session + subagents |
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

- **Durable delivery + slow models:** mail goes `pending` → `inflight` (on claim) → `done` (on ack). A node **heartbeats while it works**, so the postmaster requeues `inflight` mail **only after the claiming node goes silent** (`NODE_DEAD_AFTER`, judged by `last_seen`) — a slow model (e.g. Ollama taking minutes) keeps its claim and is never double-processed. The mesh is async, so a sender never blocks on a slow node; the reply lands whenever. Mode A (no postmaster) has no requeuer.
- **Multi-host** is via `mailroom_server.py` (stdlib HTTP sidecar over the mailroom) + `mailctl.py` (one-file peer client). Run the sidecar with `--host 0.0.0.0`; a peer (e.g. the Mac Studio) copies `mailctl.py` and runs `loop --url http://<host>:8765 --node <name>`. Never put the SQLite file on a network share — peers talk to the sidecar over TCP.
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

**Possible future work (not requested — just noted):**
- **Out-of-band key pinning** for signed cards (JWKS / DID) — today the card carries its own public key (proves integrity, not identity); pin it externally for true identity.
- **mTLS per node** — client-cert identity at the transport layer, complementing the per-principal bearer tokens.
- **Rotation / revocation** for the per-node token registry — expiry, rotation, and a revocation list on the identity store.
