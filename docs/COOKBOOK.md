# Rookery Cookbook

Small, focused recipes for the Rookery agent-communication mesh. Each is paste-and-run;
the convention: `cd` to the repo root and `export ROOKERY_DB="$PWD/rookery.db"` unless
overridden. Every recipe has a matching `demo_*.sh` (or `a2a_demo.sh`) for end-to-end proof.

---

## Basics

### Initialize the mailroom

Create the SQLite database and apply the schema on first run.

```bash
cd ~/Documents/code/rookery
export ROOKERY_DB="$PWD/rookery.db"
python3 -c "import rookery as R; R.connect(); print('ready:', R.DB_PATH)"
```

`rookery.py:connect()` creates `rookery.db` and runs `schema.sql` if absent; three
tables: `nodes`, `inbox`, `credential_requests`.

---

### Run a single mock node (mode A, self-polling)

Prove the poller loop without spending any API tokens.

```bash
python3 node_runner.py --node-id architect --engine mock --poll 1
```

Self-polls every second, wakes only on real mail. `demo.sh` runs the two-node proof
(architect + triage, unprompted hops, CIBA pause).

---

### Send a piece of mail

Drop a message into the mailroom; wire-protocol directives go in the body.

```bash
python3 send_mail.py --to architect --from human \
  --body "Summarize the repo. MAILTO:triage:send test logs. NEEDCRED:aws-prod"
```

`MAILTO:<node>:<text>` and `NEEDCRED:<resource>` are the only wire-protocol lines;
`--topic` is optional metadata.

---

### Watch live activity

Run in a second pane before starting nodes; requires `sqlite3` and `watch`.

```bash
./monitor.sh
```

Shows the last 12 inbox rows, node status/pid, and pending credential requests.

---

## Engines

### Wire a real Claude engine

Replace the mock with a live `claude -p` turn.

```bash
python3 node_runner.py --node-id architect --engine claude \
  --allowed-tools mcp__deeplakesearch__retrieve_context
```

Prompt goes via stdin so `--allowedTools` doesn't consume it; MCP stays on by default.
See `claude_engine()` in `node_runner.py`.

---

### Use the warm Claude SDK session (kills the ~115 s cold start)

`engine=claude-sdk` opens one session and reuses it across turns (~2 s warm vs ~115 s
per `claude -p` in this environment).

```bash
# Via the postmaster (recommended — it manages the warm window)
python3 postmaster.py --nodes architect=claude-sdk --idle-timeout 600

# Or directly:
pip install claude-agent-sdk
python3 sdk_node.py --node-id architect --idle-timeout 600 \
  --mcp deeplakesearch --allowed-tools mcp__deeplakesearch__retrieve_context
```

`setting_sources=[]` skips SessionStart hooks (the cold-start source); `--mcp` names
re-attach only those servers. See `sdk_node.py` and `demo_real_sdk.sh`.

---

### Wire Codex and Gemini engines

Both run in non-interactive mode; no extra flags needed beyond `--engine`.

```bash
# Codex (read-only sandbox via codex exec)
python3 node_runner.py --node-id reviewer --engine codex

# Gemini (--skip-trust is required for headless use)
python3 node_runner.py --node-id gemini-node --engine gemini
```

`--skip-trust` is required for headless Gemini; see `gemini_engine()` in `node_runner.py`.
`demo_real_team.sh` fans 2×Claude + Codex + Gemini out in parallel.

---

### Per-node engines through the postmaster

Mix engines in one command; the postmaster spawns the right runner per node.

```bash
python3 postmaster.py \
  --nodes architect=claude,reviewer=codex,analyst=gemini \
  --persistent architect \
  --max-warm 5 --idle-timeout 600
```

Entries without `=engine` inherit `--engine` (default `mock`). See `postmaster.py`.

---

## Lifecycle

### Persistent vs ephemeral, plus the warm pool

Ephemeral = one turn, then exit ($0). Persistent = rehydrate full thread from DB each
wake, stay warm for `--idle-timeout` seconds.

```bash
python3 postmaster.py \
  --nodes architect=claude,reviewer=codex \
  --persistent architect --max-warm 5 --idle-timeout 600
```

`--max-warm` caps the warm pool; the postmaster LRU-evicts when full (context stays in
the DB, rehydrates on next wake). `demo_postmaster.sh` proves no process is alive while
paused.

---

### @mention routing — talking about a node wakes it

Include `@<node>` in any message body; the postmaster notifies that node even if it is
not the recipient.

```bash
python3 send_mail.py --to architect --from human \
  --body "Ask @reviewer to check the auth module before merging."
```

Postmaster scans new rows (skips acks + its own notices) and mails a MENTION notice
to each named node. See step 3 in `postmaster.py run()`.

---

## Multi-host & federation

### Multi-host: run the sidecar + a remote node via mailctl.py loop

Run the sidecar on the host that owns the DB; join from any peer with one file.

```bash
# On the DB host (binds all interfaces):
python3 mailroom_server.py --host 0.0.0.0 --port 8765 --token "$ROOKERY_TOKEN"

# On the peer (e.g. Mac Studio — copy mailctl.py there):
ROOKERY_TOKEN=<same-token> \
python3 mailctl.py loop --url http://192.168.0.146:8765 --node macbot
```

Never put the SQLite file on a network share — peers talk to the sidecar over TCP.
See `demo_multihost.sh` and `mailctl.py` (`send`/`inbox`/`loop`).

---

### Federate two mailrooms (relay.py + mailrooms.json, node@mailroom addressing)

Each mailroom runs its own relay; mail addressed `node@remote` is forwarded there.

```bash
# mailrooms.json on each box (see mailrooms.example.json):
# { "self":"home", "home":{"url":"http://HOST:8765"}, "mac":{"url":"http://MAC:8765"} }

# Start the relay on each box:
ROOKERY_DB=/path/to/home.db ROOKERY_MAILROOM=home \
  ROOKERY_MAILROOMS=mailrooms.json python3 relay.py &

# Send cross-mailroom:
ROOKERY_DB=/path/to/home.db python3 send_mail.py \
  --to bob@mac --from alice --body "hello across mailrooms"
```

Replies route back via `sender@home`; hops capped at 4 (loop guard). See
`demo_federation.sh` and `relay.py`.

---

## A2A

### Discover the agent card and send a JSON-RPC message/send

External agents discover Rookery via the agent card and task it with JSON-RPC.

```bash
curl -s http://localhost:8765/.well-known/agent-card.json | python3 -m json.tool

curl -s http://localhost:8765/a2a -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"r1","method":"message/send",
  "params":{"message":{"role":"user","messageId":"m1",
    "parts":[{"kind":"text","text":"hello from external agent"}],
    "metadata":{"recipient":"concierge","from":"ext"}}}}'
```

Poll with `tasks/get` using the returned `result.id`. See `a2a_demo.sh`.

---

### A2A streaming over SSE (message/stream)

Receive `submitted` → `artifact-update` → `completed` without polling.

```bash
curl -sN http://localhost:8765/a2a \
  -H "Authorization: Bearer $ROOKERY_TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"s1","method":"message/stream","params":{"message":{
    "role":"user","messageId":"m1","parts":[{"kind":"text","text":"stream a reply"}],
    "metadata":{"recipient":"concierge","from":"ext"}}}}'
```

Each SSE `data:` line is a complete JSON-RPC result object. See `demo_a2a_secure.sh`.

---

### A2A push: webhook on completion (configuration.pushNotificationConfig)

Return `submitted` immediately; the sidecar POSTs the completed Task to your webhook.

```bash
curl -s http://localhost:8765/a2a -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"p1","method":"message/send","params":{
    "message":{"role":"user","messageId":"m1",
      "parts":[{"kind":"text","text":"do a thing"}],
      "metadata":{"recipient":"concierge","from":"ext"}},
    "configuration":{"pushNotificationConfig":{"url":"http://my-host:9099"}}}}'
```

`webhook_sink.py 9099` is a minimal test receiver; `_push_watch` in
`mailroom_server.py` fires the POST. See `demo_a2a_push.sh`.

---

## Security

### Sign the agent card (gen_card_key.py + --card-key; verify with verify_card.py)

Embed an Ed25519 signature in the agent card so consumers can verify integrity.

```bash
python3 gen_card_key.py                         # writes rookery-card-key.pem (chmod 600)
python3 mailroom_server.py --host 0.0.0.0 \
  --card-key rookery-card-key.pem               # card now carries alg+publicKey+value
python3 verify_card.py http://localhost:8765    # VALID or INVALID + reason
```

Integrity is proved by the embedded public key; pin it out-of-band (JWKS/DID) for
full identity. See `demo_signed_card.sh` and `security.sign_card()`.

---

### Install the hardened policy (per-node identity + per-target ACL)

Swap in `HardenedPolicy`: each principal has its own bearer token and an ACL of which
nodes it may task.

```bash
cp node_identity.example.json node_identity.json && chmod 600 node_identity.json
# edit: set "token" to $(openssl rand -hex 24), set "may_task" per principal.

ROOKERY_SECURITY="policy_hardened:HardenedPolicy" \
ROOKERY_NODE_IDENTITY="$PWD/node_identity.json" \
  python3 mailroom_server.py --host 0.0.0.0 --port 8765
```

Unknown tokens are rejected; tasking outside `may_task` returns a named denial. See
`demo_hardened.sh` and `policy_hardened.py`.

---

## Bridges

### Use the MCP bridge from a Claude Code session (rookery_mcp.py)

Add Rookery as an MCP server so any Claude Code session or subagent can join the mesh.

```json
"rookery": {
  "command": "python3",
  "args": ["/home/gyasis/Documents/code/rookery/rookery_mcp.py"],
  "env": {"ROOKERY_NODE": "claude-main",
          "ROOKERY_DB": "/home/gyasis/Documents/code/rookery/rookery.db"}
}
```

Four tools: `send(to, body)` · `check_inbox()` · `await_message(timeout)` · `roster()`.
For a remote mailroom swap `ROOKERY_DB` for `ROOKERY_URL` + `ROOKERY_TOKEN`. See
`rookery_mcp.py`.

---

### Bridge a tmux/wezterm/zellij pane into the mesh

Inject mesh mail into a human or terminal-agent pane; reply with `send_mail.py`.

```bash
# tmux (target = session:window.pane):
python3 terminal_node.py --node-id deskbot --injector tmux --target rooktest:0.0

# wezterm (target = pane-id from wezterm cli list-clients):
python3 terminal_node.py --node-id deskbot --injector wezterm --target 3

# zellij (writes to the active pane):
python3 terminal_node.py --node-id deskbot --injector zellij --target ""
```

`inspect_inbound` screens mail before it reaches the pane. See `demo_terminal.sh`.

---

## Operations

### Phone home for credentials (CIBA): NEEDCRED + mesh_approve.py

An agent emits `NEEDCRED:<resource>` and durably pauses; a human approves a pointer.

```bash
python3 mesh_approve.py                   # list pending requests
python3 mesh_approve.py --id 1 --approve  # mint a token_ref pointer (not the secret)
python3 mesh_approve.py --id 1 --deny
```

The postmaster mails `CRED_GRANTED:<resource>:<token_ref>` to wake the node;
`rookery.resolve_secret(token_ref)` resolves `env://VAR` or `keychain://…` at use
time — secret never touches the DB. See `demo.sh` and `mesh_approve.py`.

---

### DLQ — list and requeue (relay.py --list-dlq / --requeue-dlq)

Undeliverable mail (unknown mailroom or unreachable after 3 attempts) goes to the DLQ;
the sender is alerted automatically.

```bash
ROOKERY_MAILROOM=home ROOKERY_MAILROOMS=mailrooms.json python3 relay.py --list-dlq
ROOKERY_MAILROOM=home ROOKERY_MAILROOMS=mailrooms.json python3 relay.py --requeue-dlq 7
```

Dead-letter reason is in `inbox.note`; inspect with `sqlite3 rookery.db
"SELECT id,note FROM inbox WHERE status='dlq'"`. See `demo_dlq.sh`.

---

### Pause/resume a node (kill -STOP / -CONT)

OS-freeze a running node instantly: 0 CPU, RAM held, no state lost.

```bash
./pause.sh architect    # kill -STOP by pid from the nodes table
./resume.sh architect   # kill -CONT — continues exactly where it stopped
```

In-RAM only — does not survive reboot. For a durable zero-cost pause that survives
reboot, use the `NEEDCRED` flow (state in the DB). See `pause.sh` and `resume.sh`.
