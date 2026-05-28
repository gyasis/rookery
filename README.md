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

## Two modes (both kept on purpose)

| Mode | Who polls | Pause = | When to use |
|---|---|---|---|
| **A · self-polling nodes** (v1 MVP, `demo.sh`) | each node polls its own inbox | node idle-loops / blocks in-process on `NEEDCRED` | simplest; nodes are long-lived; few agents |
| **B · central postmaster** (`demo_postmaster.sh`) | one `postmaster.py` polls for everyone | nodes are **fully asleep / exited** ($0); `NEEDCRED` exits and the postmaster re-wakes on approval | many agents; true durable pause; "alarm-clock" wakeups |

Mode B is the "a background poller says *you've got mail* and wakes the sleeping
agent" model — a truly paused agent can't poll itself, so the postmaster is its
alarm clock. v1 (A) is untouched; pick per situation, same DB underneath.

## Run the demos (zero API spend)

```bash
cd ~/Documents/code/rookery
./demo.sh              # mode A — two self-polling nodes
./demo_postmaster.sh   # mode B — agents asleep, postmaster wakes them
```

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

## Files

| File | Role |
|---|---|
| `schema.sql` | the mailroom tables (single source of truth) |
| `rookery.py` | shared lib: connect/init, nodes, mail, credentials |
| `node_runner.py` | the own-loop node (mock + `claude -p`; `--managed` for mode B) |
| `postmaster.py` | mode B: central watcher that wakes sleeping agents on mail |
| `send_mail.py` | drop a message into the mailroom |
| `mesh_approve.py` | human side of the CIBA credential gate |
| `monitor.sh` | live DB view (the Monitor node) |
| `pause.sh` / `resume.sh` | OS freeze/continue a node (the "video button") |
| `demo.sh` | mode A proof (self-polling nodes) |
| `demo_postmaster.sh` | mode B proof (agents asleep, postmaster wakes them) |
| `cleanup.sh` | reset the mailroom |
| `docs/` | the research + paired-debate reports (`index.html`) |

## Known v1 limitations (deliberate)

- **Claim-on-fetch:** mail is marked delivered when picked up, so a crash mid-handling drops it. Fine for the spike; v2 adds an in-flight/ack state.
- **Single host.** SQLite-on-a-share has broken locking. Multi-host (Mac Studio) = v2: wrap this same DB behind a tiny FastAPI/WebSocket sidecar; nodes connect over TCP.
- **Credential `token_ref` is a pointer**, not a secret — real keychain/JIT-broker resolution is a TODO hook in `mesh_approve.py`.

## Roadmap

v1 prove the loop (here) → CIBA real keychain resolution → `@mention`/topic
"they're talking about you" → terminal node (substrate A, tmux+send-keys) →
v2 multi-host sidecar → v3 A2A Agent Cards for cross-vendor discovery.
