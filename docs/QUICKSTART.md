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

## Scenario 4 — Bring a Claude Code session into the mesh (one command)

You own two machines: a regular box (machine A, always-on) and a dedicated
"Claude computer" (machine B) where you sit at the keyboard inside a Claude
Code session. The mailroom lives on A; B's coding agent joins as a node —
**woken by the mesh** (no polling) and configured by a single command.

**On machine A — `rookery serve`.** Starts the sidecar with the interactive
approval prompt and announces presence on the LAN:

```bash
cd ~/Documents/code/rookery
python3 rookery_cli.py serve --host 0.0.0.0 --port 8765 \
  --token "$ROOKERY_TOKEN" --card-key rookery-card-key.pem
# (the Ed25519 card key is created once via `python3 gen_card_key.py`)
```

**On machine B — install once, then `rookery up`.** One curl pipes the
installer that drops `~/.local/bin/rookery`; one command joins the mesh:

```bash
curl -sSL http://<A-host>:8765/bootstrap | python3 -
rookery up                      # auto-discovers A on the LAN via mDNS or UDP broadcast
# or, on the open internet / when discovery isn't available:
rookery up https://<A-host>:8765
```

`rookery up` (no args) discovers A on the LAN via **mDNS** (Linux:
`avahi-browse`, macOS: `dns-sd`, Windows: `dns-sd.exe` if you have Apple's
free Bonjour Print Services installed). When mDNS is unavailable or blocked,
it falls back to **UDP broadcast** on port 8888.

What you'll see on B:

```
Discovered mailroom at http://192.168.0.146:8765
Waiting for approval on the mailroom. Your code:
  → plum-basil-plum
```

What A's terminal flashes:

```
[ROOKERY] Peer wants to join the mesh.
          node id : peer-blade-15
          pubkey  : E0lfBFvAegB3tm33jcKG...
          slug    : plum-basil-plum
  Does the joining machine show this code? Approve? [y/N]
```

You hit `y` on A; B receives the minted per-node bearer + A's card pubkey,
**TOFU-pins** it to `~/.rookery/known_hosts`, and merges the `rookery` MCP
server entry into `~/.claude.json` (with a `.bak.<epoch>`). B then prints
the wake-loop instruction.

Restart Claude Code on B and send ONE setup message that puts the agent into
the wake-loop:

```text
> You are node `peer-blade-15` in a Rookery mesh. Use the rookery MCP
> server. Loop: call rookery.await_message(timeout=300). When it
> returns mail, act on it and call await_message again. Use
> rookery.send(to, body) for replies.
```

That's the whole flow: one install + one `rookery up`. Mesh wakes the agent
from here on; no polling, no hand-edits.

**Trust model.** The slug is the human-verifiable handshake — if A's prompt
shows a different slug than B's, deny. After approval the served card pubkey
is TOFU-pinned; a later pubkey change fires an SSH-style `REMOTE HOST
IDENTIFICATION HAS CHANGED` warning and `rookery up` refuses to touch
`~/.claude.json`.

**Approve from your phone (out-of-band notifications).** If you're not at
A's terminal when B knocks, you can receive a push notification to your
phone or desktop so you can approve from anywhere. Set
`ROOKERY_NOTIFY_METHOD` to one of: `none` (default — silent), `ntfy`,
`pushover`, or `webhook`. For ntfy: also set `ROOKERY_NTFY_TOPIC=<your-topic>`
(uses ntfy.sh — free, no signup required). For Pushover: set both
`ROOKERY_PUSHOVER_TOKEN=<app-token>` and `ROOKERY_PUSHOVER_USER=<user-key>`.
For a generic webhook: set `ROOKERY_NOTIFY_WEBHOOK_URL=<your-url>` plus the
optional `ROOKERY_NOTIFY_WEBHOOK_TOKEN=<bearer>` for authenticated endpoints.
The notification carries the 3-word slug and a clickable link to the
in-browser approval form. Notifier failures **never** block the join — they
log to stderr and are silently dropped.

```bash
export ROOKERY_NOTIFY_METHOD=ntfy
export ROOKERY_NTFY_TOPIC=my-rookery-12fa9c   # pick something hard to guess
python3 rookery_cli.py serve --host 0.0.0.0 --port 8765 \
  --token "$ROOKERY_TOKEN" --card-key rookery-card-key.pem
# now subscribe on your phone: open https://ntfy.sh/my-rookery-12fa9c in the ntfy app
```

**Mobile-friendly approval form (`GET /approve`).** The link in the ntfy or
Pushover notification opens `https://<A>:8765/approve?request_id=<rid>` in
a browser. That page is a self-contained HTML form showing the join request
(node id, slug, pubkey fingerprint, request age) with **Approve** and
**Deny** buttons and a bearer-token input field. The form's POST goes back
to `/approve` (the JSON endpoint), which is auth-gated — only an admin with
the correct bearer token can actually approve or deny. The `GET` form itself
is intentionally public: the `request_id` is a random UUID, treat it like a
TOTP. If you are not the admin, the form will show the request details but
your POST will be rejected without the token.

**Managing trusted peers (`rookery known-hosts`).** After B joins, A's
Ed25519 card pubkey is TOFU-pinned on B at `~/.rookery/known_hosts`. You
can inspect and clean up that pin store at any time:

```bash
rookery known-hosts list
# http://192.168.0.146:8765  E0lfBFvAegB3tm33...  (mailroom-home, added 2026-05-30)

rookery known-hosts forget http://192.168.0.146:8765
# Remove 'http://192.168.0.146:8765' (pubkey E0lfBFvA..., added 2026-05-30)? [y/N]: y
# Removed 'http://192.168.0.146:8765'.
```

`rookery known-hosts forget <host>` prompts for confirmation; pass `--yes`
to skip the prompt. Removing a stale or compromised entry means the next
`rookery up` to that URL will TOFU-pin the fresh key instead of comparing
against the old one.

**Headless A.** If A's sidecar is running in a detached tmux / systemd unit
(no interactive terminal), SSH in and run `rookery approve` to clear the
pending queue from the command line.

**The old manual flow** (`rookery_join.py --mode mcp`, hand-paste the
JSON snippet) is still supported — see the `rookery_join.py` section of the
README — but it's no longer the recommended path.

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
