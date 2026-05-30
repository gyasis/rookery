# Rookery Quickstart

Rookery is a local-first agent-communication mesh. Nodes (headless Claude,
Codex, raw-API daemons, terminal sessions) exchange durable mail through a
SQLite mailroom; a central postmaster wakes sleeping nodes when mail arrives,
so idle agents cost nothing. The DB is the audit log — plain SQL, queryable
at any time.

Prerequisites: Python 3 stdlib only for the core. `claude` / `codex` must be
logged in only for paid demos. All three scenarios below run free by default.

---

## Scenario 1 — Same machine: two agents on one box

The postmaster (mode B) manages both nodes. Agents are fully asleep between
turns; the postmaster is their alarm clock.

```bash
cd ~/Documents/code/rookery
./cleanup.sh
python3 postmaster.py \
  --nodes architect,triage --engine mock \
  --persistent architect --idle-timeout 3 --poll 0.5 &

./monitor.sh   # second pane: live view of inbox / nodes / credentials
```

Drop the first task:

```bash
python3 send_mail.py \
  --to architect --from human \
  --body $'Task: summarize repo X.\nMAILTO:triage:please send the test logs\nNEEDCRED:aws-prod'
```

`architect` wakes, mails `triage`, then exits waiting on the credential.
Confirm no process is alive: `pgrep -af "node-id architect"`. Approve:

```bash
python3 mesh_approve.py   # lists pending requests
REQ=$(sqlite3 rookery.db "SELECT id FROM credential_requests WHERE status='pending' LIMIT 1")
python3 mesh_approve.py --id "$REQ" --approve
```

`architect` is re-woken by the grant and finishes. Inspect the transcript:

```bash
sqlite3 -header -column rookery.db \
  'SELECT id,sender,recipient,substr(body,1,60) body,delivered FROM inbox ORDER BY id;'
```

For real models, swap `--engine mock` → `--nodes architect=claude,reviewer=codex`
(both CLIs must be logged in; runs in ~30–120 s; `demo_real.sh` automates it).

---

## Scenario 2 — Same network: two machines on the LAN

Mailroom sidecar on the Linux box (192.168.0.146); Mac Studio
(192.168.0.159, user `gyasisutton`) joins as a remote node. SQLite stays on
one host — peers talk to the sidecar over TCP, never via a network share.

**On 192.168.0.146:**

```bash
cd ~/Documents/code/rookery && ./cleanup.sh
python3 mailroom_server.py --host 0.0.0.0 --port 8765 &
curl -s http://192.168.0.146:8765/health
```

**On 192.168.0.159 (Mac Studio):**

```bash
scp gyasisutton@192.168.0.146:~/Documents/code/rookery/mailctl.py .
python3 mailctl.py loop --url http://192.168.0.146:8765 --node macbot --poll 1
```

**Back on the Linux box — send mail, then check the DB:**

```bash
python3 send_mail.py --to macbot --from human --body "hello from the Linux box"
sqlite3 -header -column ~/Documents/code/rookery/rookery.db \
  "SELECT id,sender,recipient,substr(body,1,50) body,status FROM inbox ORDER BY id;"
```

The Mac node replies with `mailctl.py send --url http://192.168.0.146:8765 --to human --from macbot --body "..."`.

A bearer token (`--token`) authenticates but does NOT encrypt. On a trusted
LAN that is acceptable; for an untrusted segment use the TLS sidecar below.

---

## Scenario 3 — Internet: full security stack

TLS, bearer token, signed agent card, and hardened per-node-identity policy.

**Step 1 — TLS cert.** For a lab, generate self-signed:

```bash
cd ~/Documents/code/rookery
./gen_cert.sh <your-hostname-or-IP>
# writes rookery-cert.pem + rookery-key.pem (mode 600, gitignored)
```

For production use a CA-signed cert (Let's Encrypt) or a tunnel instead:

```bash
tailscale up                                      # Tailscale: zero-config mesh VPN
cloudflared tunnel --url http://localhost:8765    # Cloudflare Tunnel
ssh -L 8765:localhost:8765 user@<internet-host>   # SSH reverse tunnel
```

**Step 2 — Hardened identity.** Per-principal tokens + per-target ACLs:

```bash
cp node_identity.example.json node_identity.json
# set each principal's "token" and "may_task" list in the JSON
```

**Step 3 — Signed agent card.** Proves the card wasn't tampered in transit:

```bash
pip install cryptography        # only non-stdlib dep
python3 gen_card_key.py         # writes rookery-card-key.pem (mode 600, gitignored)
```

**Step 4 — Start the hardened sidecar:**

```bash
export ROOKERY_TOKEN="$(openssl rand -hex 32)"
export ROOKERY_SECURITY="policy_hardened:HardenedPolicy"
export ROOKERY_NODE_IDENTITY="$(pwd)/node_identity.json"

python3 mailroom_server.py \
  --host 0.0.0.0 --port 8765 \
  --tls-cert rookery-cert.pem --tls-key rookery-key.pem \
  --token "$ROOKERY_TOKEN" \
  --card-key rookery-card-key.pem \
  --public-url https://<your-public-hostname>:8765
```

TLS + token = auth AND encryption. Token alone does not encrypt; it requires
TLS or a tunnel for the open internet.

**Step 5 — Remote node joins:**

```bash
# On the remote machine:
export ROOKERY_TOKEN="<this-principal's-token-from-node_identity.json>"
python3 mailctl.py loop \
  --url https://<your-public-hostname>:8765 \
  --node architect --token "$ROOKERY_TOKEN"
# Self-signed cert: also set ROOKERY_INSECURE_TLS=1
```

**Step 6 — Verify the signed card:**

```bash
python3 verify_card.py https://<your-public-hostname>:8765
# signature: VALID  (exit 0) — or INVALID (exit 1) if tampered / unsigned
```

To run the full security suite locally:

```bash
./demo_tls.sh          # HTTPS + token; 401 without token
./demo_signed_card.sh  # Ed25519 card; tampered card fails
./demo_hardened.sh     # per-node identity + ACL; guest denied reviewer
```

---

## Scenario 4 — Bring a Claude Code session into the mesh (cross-host)

You own two machines: a regular box (machine A, always-on) and a dedicated
"Claude computer" (machine B) where you sit at the keyboard inside a Claude
Code session. The mailroom lives on A; B's coding agent joins as a node —
and gets **woken by the mesh** (no polling, no "is there mail?" checks).

**On machine A** — start the sidecar (Scenario 2 LAN-only or Scenario 3 full
TLS+token; whatever you ran already is fine):

```bash
python3 mailroom_server.py --host 0.0.0.0 --port 8765 --token "$ROOKERY_TOKEN" &
```

**On machine A** — mint an **invite** for B (one-shot handshake, no hand-editing
of any config). The invite is a JSON file carrying the URL, a freshly minted
per-node token (with TTL), and the sidecar's pinned Ed25519 card pubkey:

```bash
curl -s -X POST "https://<A-host>:8765/invite" \
  -H "Authorization: Bearer $ROOKERY_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"node_id":"claude-on-B","may_task":["*"],"ttl_minutes":30}' \
  > invite.json   # transfer this file to machine B (scp / paste / QR — your call)
```

**On machine B** — fetch the bridge files once, then consume the invite:

```bash
scp gyasisutton@<A-host>:~/Documents/code/rookery/{rookery_mcp.py,rookery_join.py,rookery.py,mailctl.py,schema.sql,security.py} \
    ~/rookery-bridge/
pip install mcp cryptography                          # MCP SDK + Ed25519 verify

python3 ~/rookery-bridge/rookery_join.py invite.json --mode verify   # pins card pubkey
python3 ~/rookery-bridge/rookery_join.py invite.json --mode mcp      # prints the ~/.claude.json snippet to paste
# OR, for a non-Claude-Code peer that should just be a remote loop:
python3 ~/rookery-bridge/rookery_join.py invite.json --mode loop     # starts mailctl.py loop right now
```

`--mode verify` **refuses** the invite if the sidecar's served agent-card pubkey
doesn't match the one pinned in the invite (TOFU). A tampered invite exits 1.

Once the snippet is in `~/.claude.json`, restart the Claude Code session and
**send ONE setup message** that puts the
agent into the wake-loop. From here the agent does NOT poll — it sits inside
`await_message` and only re-enters the loop after acting on returned mail:

```text
> You are node `claude-on-B` in a Rookery mesh. Use the rookery MCP server.
> Loop forever: call rookery.await_message(timeout=300). When it returns
> mail, act on it (use any tool you need) then call await_message again.
> Use rookery.send(to, body) for replies. Continue until I tell you to stop.
```

That's it. From this point on, anything you send from machine A:

```bash
python3 send_mail.py --to claude-on-B --from human --body "summarize repo X"
```

…lands in the agent's `await_message` return value within ~1 s. The agent
acts, replies via `rookery.send`, and goes back to blocking. Mesh wakes
agent — agent never had to ask.

**Alternative — pane keystroke injection.** If you can't run MCP in the
session (legacy CLI, no MCP support), `terminal_node.py --injector
{tmux,wezterm,zellij} --target <pane>` types mail into the pane. It currently
reads a *local* mailroom — for cross-host pane-injection, federate
B → A with `relay.py` or wrap `mailctl.py loop` with `tmux send-keys`.
See `COOKBOOK.md` recipes 12 (federate) and 19 (terminal node).

---

## Where to go next

- `COOKBOOK.md` — copy-paste recipes: research queues, CIBA credential gate,
  federation, dead-letter queue, terminal substrate.
- `STORIES.md` — annotated walkthroughs of the paid demos (`demo_real.sh`,
  `demo_real_team.sh`, `demo_real_sdk.sh`).
- `docs/index.html` (`rookery-explained.html`) — design rationale, paired
  debate, and A2A protocol survey.
- `README.md` — full reference: wire protocol, lifecycle model, A2A interop,
  MCP bridge for Claude Code sessions, federation, security policy API.
