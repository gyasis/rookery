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
# paid (real models):
./demo_real.sh            # Claude architect ↔ Codex reviewer
./demo_real_research.sh   # Claude researcher fires the DeepLake MCP tool → Codex writer → human
./demo_real_sdk.sh        # warm SDK node: 3 sequential turns, ~7s then ~2s (vs ~115s each)
./demo_real_sdk_research.sh # warm SDK + DeepLake MCP tool → Codex writer → human
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
> On a trusted **LAN** that's fine. Over the **internet**, put the sidecar behind
> **TLS or a tunnel** (Tailscale / SSH `-L` / Cloudflare Tunnel) — never expose
> plain HTTP + token to the open internet.

Run `./a2a_demo.sh` (open) or `./demo_a2a_secure.sh` (auth + streaming) for the
round-trip. (Push notifications are not implemented yet.)

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

## Files

| File | Role |
|---|---|
| `schema.sql` | the mailroom tables (single source of truth) |
| `rookery.py` | shared lib: connect/init, nodes, mail, credentials |
| `node_runner.py` | the own-loop node (mock / `claude -p` / `codex exec`; `--lifecycle`, `--allowed-tools`) |
| `sdk_node.py` | warm long-lived Claude session (Agent SDK) — `engine=claude-sdk`, kills cold start |
| `postmaster.py` | mode B: central watcher; per-node engines (incl. `claude-sdk`), `--persistent`, `--max-warm` |
| `send_mail.py` | drop a message into the mailroom |
| `mesh_approve.py` | human side of the CIBA credential gate |
| `security.py` | **swappable** SecurityPolicy: auth · authz · credential mint/resolve · inbound message inspection |
| `policy_example.py` | reference hardened policy (template) — blocks injection patterns, denies a sensitive node |
| `monitor.sh` | live DB view (the Monitor node) |
| `mailroom_server.py` | stdlib HTTP sidecar: mailroom API + **A2A** card / JSON-RPC (`message/send`, `tasks/get`, `message/stream` SSE) + bearer auth |
| `mailctl.py` | one-file stdlib HTTP client for a peer (copy to the Mac; `send` / `inbox` / `loop`) |
| `pause.sh` / `resume.sh` | OS freeze/continue a node (the "video button") |
| `demo.sh` | mode A proof (self-polling nodes) |
| `demo_postmaster.sh` | mode B proof (agents asleep, postmaster wakes them) |
| `demo_research.sh` | research-queue proof (mock): dispatch + handoff + persistence |
| `demo_real.sh` | real Claude ↔ Codex handoff |
| `demo_real_research.sh` | real DeepLake tool-use → Claude → Codex → human |
| `cleanup.sh` | reset the mailroom |
| `docs/` | the research + paired-debate reports (`index.html`) |

## Known v1 limitations (deliberate)

- **Durable delivery:** mail goes `pending` → `inflight` (on claim) → `done` (on ack); the postmaster requeues `inflight` mail abandoned by a node that crashed mid-turn (after `INFLIGHT_TIMEOUT`). Mode A (no postmaster) has no requeuer.
- **Multi-host** is via `mailroom_server.py` (stdlib HTTP sidecar over the mailroom) + `mailctl.py` (one-file peer client). Run the sidecar with `--host 0.0.0.0`; a peer (e.g. the Mac Studio) copies `mailctl.py` and runs `loop --url http://<host>:8765 --node <name>`. Never put the SQLite file on a network share — peers talk to the sidecar over TCP.
- **Credential `token_ref` is a pointer**, not a secret — resolved at use time via `rookery.resolve_secret()` (`env://VAR` or `keychain://service/account` via libsecret `secret-tool`). The secret never touches the DB.

## Roadmap

Done: prove the loop · CIBA real keychain/env resolution · `@mention` routing ·
durable delivery (in-flight/ack) · warm SDK session · multi-host HTTP sidecar ·
A2A agent card + `message/send`/`tasks/get` + `message/stream` (SSE) + bearer auth.
Next: terminal node (substrate A, tmux+send-keys); A2A push notifications; TLS/tunnel helper for internet use.
