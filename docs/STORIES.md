# Rookery — Use-Case Stories

This file is a set of narrative walkthroughs of the Rookery mesh in action.
Each story follows the same structure: the scene, what happens inside the mesh,
a "Run it yourself" block of real commands lifted from the corresponding demo,
a brief explanation of which primitives are doing the work, and a proof line
pointing at the demo script.

---

## Story 1 — My Codex partner reviews everything Claude writes

**The scene.** You are on a Linux laptop and you want a standing code-review
partner: every time Claude drafts something, Codex should see it unprompted
and send back a critique. You do not want to babysit the handoff. The obstacle
is that two separately billed model APIs have no shared protocol; you need
something in the middle that can store the work and wake the right process at
the right moment.

**What happens.** The postmaster starts with two nodes registered: `architect`
bound to `engine=claude` and marked persistent, `reviewer` bound to
`engine=codex` and left ephemeral. You send one mail to `architect` from
`human` asking for a one-line `is_prime(n)` function and telling it to pass the
result to `reviewer` via `MAILTO:reviewer:`. Claude wakes, writes the function,
emits the `MAILTO:reviewer:` line. The postmaster intercepts that line, drops a
new row into `inbox`, and wakes `reviewer` — which was fully asleep at $0 cost
until that moment. Codex reads the function, critiques it, and its reply lands
as a new `done` row in the mailroom. No keystroke between turns.

**Run it yourself.**
```bash
cd ~/Documents/code/rookery
./cleanup.sh
python3 postmaster.py --nodes architect=claude,reviewer=codex \
  --persistent architect --idle-timeout 10 --poll 1 &
python3 send_mail.py --to architect --from human --body \
  "Write a one-line Python function is_prime(n). Then send it to the reviewer
   for critique using a line exactly: MAILTO:reviewer:<the function> please review."
# wait ~2-4 min, then inspect:
sqlite3 rookery.db \
  'SELECT id,sender,recipient,substr(body,1,80) AS body FROM inbox ORDER BY id;'
```

**Why this works.** The postmaster operates in mode B: nodes are fully exited
between turns, so idle cost is zero. Mail durability is enforced by the
`pending` → `inflight` → `done` state machine in SQLite WAL mode — if the
reviewer crashes mid-turn the row stays `inflight` and is requeued after
`NODE_DEAD_AFTER` seconds without a heartbeat. `architect` is marked persistent,
so its conversation thread is rehydrated from the mailroom on each wake; the
reviewer is ephemeral and gets only the single task brief. The `MAILTO:` line is
the entire cross-vendor wire protocol — no SDK dependency on either side.

**Proof.** `demo_real.sh` (commit `2e61cae` for the warm-SDK variant in
`demo_real_sdk.sh`).

---

## Story 2 — My Mac Studio runs the heavy job while I am on the laptop

**The scene.** A Linux laptop (`<linux-host>`) is the primary machine; a Mac
Studio (`<mac-host>`, user `<mac-user>`) has the GPU budget and the large
models. You want a node called `macbot` to run on the Mac and receive tasks
from the laptop's mailroom over the LAN. There is no shared filesystem — the
Mac cannot mount the laptop's SQLite file, and you do not want it to. The
obstacle is transport: the Mac needs to read and acknowledge mail without
touching the DB directly.

**What happens.** On the laptop you start `mailroom_server.py` bound to
`0.0.0.0:8765`, which exposes an HTTP API over the local SQLite file. On the
Mac you copy the single-file `mailctl.py` (no dependencies beyond Python 3) and
run `mailctl.py loop`. The loop registers `macbot`, heartbeats every poll
interval, pulls its inbox via `GET /inbox?node=macbot`, claims the messages, and
posts ACKs back via `POST /ack` — all pure stdlib HTTP. Mail injected locally on
the laptop via `send_mail.py` shows up in `macbot`'s inbox over TCP; the ACK
written by the Mac appears in the laptop's DB as a `done` row.

**Run it yourself.**
```bash
# On the laptop (<linux-host>):
cd ~/Documents/code/rookery
python3 mailroom_server.py --host 0.0.0.0 --port 8765

# On the Mac Studio (<mac-host>), copy mailctl.py then:
python3 mailctl.py loop --url http://<linux-host>:8765 --node macbot

# From the laptop, send a task:
python3 send_mail.py --to macbot --from human --body "run the heavy inference job"

# Inspect from either side:
python3 mailctl.py inbox --url http://<linux-host>:8765 --node macbot
```

**Why this works.** The sidecar is the only process that touches SQLite; every
peer speaks to it over HTTP. `mailctl.py` is intentionally a single file with no
third-party imports — copy it to any Python 3 host and it works. The heartbeat
keeps the `last_seen` timestamp fresh so the postmaster's liveness check never
falsely requeues a message that the Mac is still processing. On a trusted LAN a
bearer token (`--token` / `$ROOKERY_TOKEN`) is sufficient auth; for traffic
leaving the LAN the sidecar can be started with `--tls-cert` and `--tls-key`
(see `gen_cert.sh`) to add encryption.

**Proof.** `demo_multihost.sh` (loopback proof); the real cross-host path is
documented in the script's closing instructions and commit `385abf4`.

---

## Story 3 — An external A2A agent shows up and talks to my mesh

**The scene.** A vendor you are evaluating ships an agent built on a different
framework. They claim it speaks A2A (Agent2Agent JSON-RPC). You want to let it
discover what your Rookery mesh can do, send it a task, and get the result back
— without writing any Rookery-specific code on their side. The obstacle is
discovery: they have no idea what nodes you are running or how to address them.

**What happens.** The sidecar serves `GET /.well-known/agent-card.json` — an
Agent Card whose `skills` array lists the registered Rookery nodes (in this demo,
`concierge`). The external client fetches the card, picks a skill, and sends
`POST /a2a` with a JSON-RPC `message/send` body, setting
`message.metadata.recipient` to `concierge`. The sidecar drops the message into
the mailroom and returns a `Task` object with an id. The postmaster wakes
`concierge`, which processes the message and replies. The external client either
polls with `tasks/get` until `state` is `completed` and reads the reply as an
artifact, or it passes a `configuration.pushNotificationConfig.url` in
`message/send` and the sidecar POSTs the completed Task to that webhook the
moment the node replies — no polling required.

**Run it yourself.**
```bash
cd ~/Documents/code/rookery
# open A2A (no auth) + poll variant:
./a2a_demo.sh

# push-notification variant (sidecar POSTs to your webhook):
./demo_a2a_push.sh

# step by step:
python3 mailroom_server.py --host 127.0.0.1 --port 8765 \
  --public-url http://127.0.0.1:8765 --default-node concierge &
python3 postmaster.py --nodes concierge --engine mock --poll 0.5 &
curl -s http://127.0.0.1:8765/.well-known/agent-card.json | python3 -m json.tool
curl -s http://127.0.0.1:8765/a2a -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"r1","method":"message/send",
       "params":{"message":{"role":"user","messageId":"m1",
         "parts":[{"kind":"text","text":"hello concierge"}],
         "metadata":{"recipient":"concierge","from":"ext-agent"}}}}' \
  | python3 -m json.tool
```

**Why this works.** The A2A surface is a thin layer over the normal mailroom:
`message/send` calls `/send` internally and wraps the result in a Task; `tasks/get`
queries the `inbox` row by id. `message/stream` returns Server-Sent Events
(`submitted` → `artifact-update` → `status-update completed`) for clients that
prefer streaming over polling. None of these paths bypass the durable delivery
guarantee — the message is still `pending` → `inflight` → `done` in SQLite, still
screened by `inspect_inbound` in the security policy, and still processed by a
normal Rookery node. The external agent needs only `curl`; Rookery speaks to it.

**Proof.** `a2a_demo.sh` (commit `90de7a6`) and `demo_a2a_push.sh` (commit
`7de5196`).

---

## Story 4 — My research agent and my writing agent live in different mailrooms and they still chat

**The scene.** You have two physical machines: the laptop runs `alice`, a
research node; the Mac runs `bob`, a writing node. They are on the same LAN but
each machine owns its own SQLite file — you do not want them sharing a DB, both
for isolation and because SQLite on a network share is asking for corruption.
You want `alice` to mail `bob` as naturally as she mails any local node, and
for replies to route back automatically.

**What happens.** Each machine runs its own `mailroom_server.py` (the `home`
mailroom at port 8765, the `mac` mailroom at 8766) and its own `relay.py`
pointed at a shared directory file (`mailrooms.json`) that maps mailroom names
to URLs. When `alice` sends to `bob@mac`, the local relay picks up the outbound
row, looks up `mac` in the directory, and POSTs the message to the `mac`
sidecar — rewriting the sender to `alice@home` so that `bob`'s reply knows where
to go. `bob` processes the message and emits a reply addressed to `alice@home`;
the `mac` relay forwards it back to the `home` sidecar. Both mailrooms end up
with a coherent transcript of the exchange.

**Run it yourself.**
```bash
cd ~/Documents/code/rookery
# demo_federation.sh runs both mailrooms on localhost to prove the relay:
./demo_federation.sh

# The real two-machine form (abbreviated):
# On the laptop:
ROOKERY_DB=/tmp/rookery_home.db ROOKERY_MAILROOM=home \
  python3 mailroom_server.py --host 0.0.0.0 --port 8765 &
ROOKERY_DB=/tmp/rookery_home.db ROOKERY_MAILROOM=home \
  python3 relay.py --poll 1 &
ROOKERY_DB=/tmp/rookery_home.db \
  python3 send_mail.py --to bob@mac --from alice \
    --body "hello bob, alice@home here"

# On the Mac:
ROOKERY_DB=/tmp/rookery_mac.db ROOKERY_MAILROOM=mac \
  python3 mailroom_server.py --host 0.0.0.0 --port 8765 &
ROOKERY_DB=/tmp/rookery_mac.db ROOKERY_MAILROOM=mac \
  python3 relay.py --poll 1 &
```

**Why this works.** The mental model is email: bare `node` names are local;
`node@mailroom` names cross a relay. Each mailroom is sovereign — it has its own
DB, its own nodes, its own security policy. The relay rewrites sender addresses
so replies route home without any caller-side knowledge of the topology. Loops
are bounded by a hop counter (`relay/<n>`, max 4). Mail that cannot reach a
remote mailroom after retries lands in a dead-letter queue (`status='dlq'`) and
the original sender is alerted; `relay.py --list-dlq` and `--requeue-dlq <id>`
handle recovery. Two teams, two physical sites, or an air-gapped slice all map
cleanly onto this model.

**Proof.** `demo_federation.sh` (commit `d84c0f4`).

---

## Story 5 — A subagent phones home for a credential it does not have

**The scene.** A deploy node is mid-task and realizes it needs the Snowflake
production password to push a schema migration. The password is in the OS
keychain, not in the environment, and you have a hard rule that secrets never
touch a prompt or a log file. The obstacle is that the node is running headless,
possibly on a schedule, with no interactive terminal.

**What happens.** The node emits a single line: `NEEDCRED:snowflake-prod`. The
postmaster (mode B) sees this, writes a row to `credential_requests` with
`node_id=deploy` and `resource=snowflake-prod`, and parks the node — in mode B
the node process fully exits, spending nothing while it waits. You run
`mesh_approve.py` with no arguments and see the pending request. You run it
again with `--id <N> --approve`. The tool calls `security.get_policy().mint_pointer()`
which looks up `keychain://snowflake-prod/password` via `secret-tool`, confirms
the secret exists, and writes the `token_ref` string — not the secret itself —
into `credential_requests`. The postmaster detects the approval and re-wakes the
`deploy` node. The node calls `rookery.resolve_secret(token_ref)` at the exact
moment it needs the credential; the secret moves from the keychain to memory,
is used, and is never written anywhere else.

**Run it yourself.**
```bash
cd ~/Documents/code/rookery
# Inspect pending requests (nothing yet):
python3 mesh_approve.py

# In a separate terminal, simulate a node requesting a cred:
python3 node_runner.py --node-id deploy --engine mock
# (or send mail that causes the mock to emit NEEDCRED:snowflake-prod)

# List and approve:
python3 mesh_approve.py
python3 mesh_approve.py --id 1 --approve
# -> approved #1 for node 'deploy' -> token_ref=keychain://snowflake-prod/password [verified]
# (pointer only — secret never touches the DB)
```

**Why this works.** The credential gate is the third level of the durable-pause
hierarchy: state lives in `credential_requests` (SQLite), so the node can exit
completely and the secret is never written anywhere during the pause. The
`token_ref` is a *pointer* (`env://VAR` or `keychain://service/account`) — a
string that tells the node where to fetch the secret at use time via
`rookery.resolve_secret()`. The real secret travels only from the OS keychain to
the process memory of the node that needs it, for the instant it is needed.
`mesh_approve.py --deny` is equally valid; the node gets a denial row and can
handle it gracefully. The entire credential path runs through the swappable
`SecurityPolicy`, so a JIT broker (Aembit, Vault) can replace the keychain
lookup by subclassing `mint_pointer` and `resolve_secret` without touching
anything else.

**Proof.** `mesh_approve.py` + the CIBA section of `README.md` (commit `4921432`
added real keychain/env resolution).

---

## Story 6 — Three vendors, one task, parallel work

**The scene.** You are building a URL shortener. You want a Gemini node to
research collision-avoidance strategies, a Codex node to enumerate security
concerns, and a second Claude node to draft the core function — all at the same
time, not sequentially. Then you want the architect to collect all three replies
and synthesize. The obstacle is that fanning out to three different model APIs
and collecting their results normally requires polling logic you have to write
and maintain.

**What happens.** The postmaster starts with four nodes: `architect=claude-sdk`
(warm, persistent), `coder=claude-sdk` (warm), `reviewer=codex` (ephemeral), and
`researcher=gemini` (ephemeral). You send one mail to `architect`. It wakes, and
its reply contains three `MAILTO:` lines emitted simultaneously — one to
`researcher`, one to `reviewer`, one to `coder`. The postmaster sees all three
rows land in `inbox` at roughly the same time and wakes all three nodes
concurrently. You can verify overlap with the `woke on` timestamps in the
postmaster log. All three reply to `architect` via `MAILTO:architect:`. The
`architect` node (still warm in its SDK session) receives the three replies and
synthesizes them into a final answer that lands in the mailroom.

**Run it yourself.**
```bash
cd ~/Documents/code/rookery
./cleanup.sh
python3 postmaster.py \
  --nodes architect=claude-sdk,coder=claude-sdk,reviewer=codex,researcher=gemini \
  --idle-timeout 10 --poll 1 > /tmp/team_pm.log 2>&1 &
python3 send_mail.py --to architect --from human --body \
  "Fan the work out IN PARALLEL by emitting EXACTLY these three lines:
MAILTO:researcher:Research short-code generation + collision avoidance for a URL shortener (2 bullets). Reply to architect via MAILTO:architect:<result>.
MAILTO:reviewer:List 3 security considerations for a public URL shortener. Reply to architect via MAILTO:architect:<result>.
MAILTO:coder:Draft a short Python generate_short_code(n=7). Reply to architect via MAILTO:architect:<result>."
# wait ~4 min, then check overlap:
grep -E "woke on|warm session ready|online" /tmp/team_pm.log | grep -vi postmaster | head -20
sqlite3 rookery.db \
  "SELECT id,sender,recipient,substr(body,1,56) AS body FROM inbox ORDER BY id;"
```

**Why this works.** The postmaster's poll loop does not serialize wake-ups: it
finds all `pending` rows each tick and spawns the corresponding node processes in
parallel. Three `MAILTO:` lines from one architect turn become three independent
`inbox` rows; the postmaster wakes three processes in the same poll cycle.
`architect` and `coder` use `engine=claude-sdk` (`sdk_node.py`), which opens a
long-lived Claude Agent SDK session with `setting_sources=[]` to skip the
SessionStart hooks that cost ~115 s per cold call in this environment. First turn
after warm-up costs ~7 s; subsequent turns on the same session cost ~2 s. Gemini
and Codex use `node_runner.py` with their respective engines. All four share the
same SQLite mailroom; the only coordination mechanism is a row in `inbox`.

**Proof.** `demo_real_team.sh` (commit `925dc1d`).
