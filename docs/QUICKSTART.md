# Rookery Quickstart

Rookery is a local-first agent-communication mesh. Nodes (headless Claude,
Codex, raw-API daemons, terminal sessions) exchange durable mail through a
SQLite mailroom. A central postmaster wakes sleeping nodes when mail arrives,
so idle agents cost nothing. The DB is the audit log — plain SQL, queryable
at any time.

Prerequisites: Python 3 stdlib only for the core. Log in to `claude` / `codex`
only for paid demos. All three scenarios below run free by default.

---

## Installation

Rookery installs as **one self-contained binary** — the same `rookery` command
**hosts** the mailroom (`rookery serve`) *or* **joins** one (`rookery join`). There
is no separate "server build" or "client build". You pick the role at
run time, not at install time.

### Option A — from GitHub (any machine)

```bash
git clone https://github.com/gyasis/rookery.git ~/Documents/code/rookery
cd ~/Documents/code/rookery
# build the self-contained zipapp onto your PATH (XDG: ~/.local/bin):
python3 -c "import bootstrap; bootstrap.build_pyz('$HOME/.local/bin/rookery')"
rookery --help          # serve · up · approve · known-hosts
```

`build_pyz` bundles every module for **both** roles (mailroom server and
peer client). It reads its SQL schema from inside the archive. A machine
installed this way can serve a mesh with only the single file on PATH.

### Option B — one-liner from a running host (zero git)

If a mailroom already runs, a new machine installs straight from it.
The `/bootstrap` endpoint streams the same zipapp installer:

```bash
curl -sSL http://<host>:<port>/bootstrap | python3 -   # drops ~/.local/bin/rookery
rookery join http://<host>:<port>                        # then join
```

### Dependencies

- **Core:** Python 3 standard library only — nothing to `pip install`.
- **Security tier (Scenario 3 — TLS, signed cards, hardened policy):**
  `pip install cryptography` (the one non-stdlib dependency).

### The two roles at a glance

| Role  | Command            | Owns the SQLite mailroom? | How many per mesh |
|-------|--------------------|---------------------------|-------------------|
| Host  | `rookery serve`    | **yes** (the audit log)   | exactly **1**     |
| Peer  | `rookery join <url>` | no — talks to the host    | many              |

Peers never share the DB file; they reach the host's HTTP sidecar over TCP.
Pick **one** machine to `serve`. All others run `up`.

---

## Scenario 1 — Same machine: two agents

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
rookery send architect $'Task: summarize repo X.\nMAILTO:triage:please send the test logs\nNEEDCRED:aws-prod'
```

`send_mail.py` is legacy. The old form is
`python3 send_mail.py --to architect --from human --body "..."`.

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
(log in to both CLIs first; it runs in ~30–120 s; `demo_real.sh` automates it).

---

## Scenario 2 — Same network: two machines on the LAN

The mailroom sidecar runs on a Linux machine (`<linux-host>`). A Mac Studio
(`<mac-host>`, user `<mac-user>`) joins as a remote node. SQLite stays on one
machine. Peers talk to the sidecar over TCP, never via a network share.
Replace `<linux-host>` / `<mac-host>` / `<mac-user>` with your own LAN values.

Start rules: the sidecar binds `127.0.0.1` by default and needs no token there. A
public `--host` (such as `0.0.0.0`) needs `--allow-public` and a token
(`--token` or `$ROOKERY_TOKEN`).

**On `<linux-host>`:**

```bash
cd ~/Documents/code/rookery && ./cleanup.sh
export ROOKERY_TOKEN="$(openssl rand -hex 32)"
python3 mailroom_server.py --host 0.0.0.0 --allow-public --port 8765 &
curl -s http://<linux-host>:8765/health
```

**On `<mac-host>` (Mac Studio):**

```bash
# mailctl.py is legacy; the current commands are rookery send, inbox and ack.
export ROOKERY_TOKEN="<same-token>"
scp <mac-user>@<linux-host>:~/Documents/code/rookery/mailctl.py .
python3 mailctl.py loop --url http://<linux-host>:8765 --node macbot --poll 1
```

**Back on the Linux machine — send mail, then check the DB:**

```bash
# Current: rookery send macbot "hello from the Linux box"
# Legacy (send_mail.py):
python3 send_mail.py --to macbot --from human --body "hello from the Linux box"
sqlite3 -header -column ~/Documents/code/rookery/rookery.db \
  "SELECT id,sender,recipient,substr(body,1,50) body,status FROM inbox ORDER BY id;"
```

The Mac node replies with the legacy `mailctl.py send --url http://<linux-host>:8765 --to human --from macbot --body "..."` (the current command is `rookery send`).

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
  --host 0.0.0.0 --allow-public --port 8765 \
  --tls-cert rookery-cert.pem --tls-key rookery-key.pem \
  --token "$ROOKERY_TOKEN" \
  --card-key rookery-card-key.pem \
  --public-url https://<your-public-hostname>:8765
```

TLS + token = auth AND encryption. A token alone does not encrypt. Use
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

You own two machines. Machine A is always on. Machine B is a dedicated
"Claude computer" where you sit at the keyboard inside a Claude Code session.
The mailroom lives on A. B's coding agent joins as a node. The mesh **wakes it**
(no polling) and one command configures it.

**On machine A — `rookery serve`.** Starts the sidecar with the interactive
approval prompt and announces presence on the LAN:

```bash
cd ~/Documents/code/rookery
python3 rookery_cli.py serve --host 0.0.0.0 --allow-public --port 8765 \
  --token "$ROOKERY_TOKEN" --card-key rookery-card-key.pem
# (the Ed25519 card key is created once via `python3 gen_card_key.py`)
```

**On machine B — install once, then `rookery join`.** One curl pipes the
installer that drops `~/.local/bin/rookery`; one command joins the mesh:

```bash
curl -sSL http://<A-host>:8765/bootstrap | python3 -
rookery join                      # auto-discovers A on the LAN via mDNS or UDP broadcast
# or, on the open internet / when discovery isn't available:
rookery join https://<A-host>:8765
```

`rookery join` (no args) discovers A on the LAN via **mDNS** (Linux:
`avahi-browse`, macOS: `dns-sd`, Windows: `dns-sd.exe` if you have Apple's
free Bonjour Print Services installed). When mDNS is unavailable or blocked,
it falls back to **UDP broadcast** on port 8888.

What you'll see on B:

```
Discovered mailroom at http://<linux-host>:8765
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

That's the whole flow: one install + one `rookery join`. Mesh wakes the agent
from here on; no polling, no hand-edits.

**Trust model.** The slug is the human-verifiable handshake — if A's prompt
shows a different slug than B's, deny. After approval the served card pubkey
is TOFU-pinned. A later pubkey change fires an SSH-style key-changed
warning, and `rookery join` refuses to touch `~/.claude.json`.

**Approve from your phone (out-of-band notifications).** If you are not at
A's terminal when B knocks, you get a push notification on your
phone or desktop. Then you can approve from anywhere. Set
`ROOKERY_NOTIFY_METHOD` to one of: `none` (default — silent), `ntfy`,
`pushover`, or `webhook`. For ntfy: also set `ROOKERY_NTFY_TOPIC=<your-topic>`
(uses ntfy.sh, which is free and needs no signup). For Pushover: set both
`ROOKERY_PUSHOVER_TOKEN=<app-token>` and `ROOKERY_PUSHOVER_USER=<user-key>`.
For a generic webhook: set `ROOKERY_NOTIFY_WEBHOOK_URL=<your-url>` plus the
optional `ROOKERY_NOTIFY_WEBHOOK_TOKEN=<bearer>` for authenticated endpoints.
The notification carries the 3-word slug and a clickable link to the
in-browser approval form. Notifier failures **never** block the join. They
log to stderr and nothing else happens.

```bash
export ROOKERY_NOTIFY_METHOD=ntfy
export ROOKERY_NTFY_TOPIC=my-rookery-12fa9c   # pick something hard to guess
python3 rookery_cli.py serve --host 0.0.0.0 --allow-public --port 8765 \
  --token "$ROOKERY_TOKEN" --card-key rookery-card-key.pem
# now subscribe on your phone: open https://ntfy.sh/my-rookery-12fa9c in the ntfy app
```

**Mobile-friendly approval form (`GET /approve`).** The link in the ntfy or
Pushover notification opens `https://<A>:8765/approve?request_id=<rid>` in
a browser. That page is a self-contained HTML form. It shows the join details
(node id, slug, pubkey fingerprint, age) with **Approve** and
**Deny** buttons and a bearer-token input field. The form's POST goes back
to `/approve` (the JSON endpoint), which is auth-gated — only an admin with
the correct bearer token can actually approve or deny. The `GET` form itself
is intentionally public: the `request_id` is a random UUID, treat it like a
TOTP. If you are not the admin, the form shows the join details but
your POST fails without the token.

**Managing trusted peers (`rookery known-hosts`).** After B joins, A's
Ed25519 card pubkey is TOFU-pinned on B at `~/.rookery/known_hosts`. You
can inspect and clean up that pin store at any time:

```bash
rookery known-hosts list
# http://<linux-host>:8765  E0lfBFvAegB3tm33...  (mailroom-home, added 2026-05-30)

rookery known-hosts forget http://<linux-host>:8765
# Remove 'http://<linux-host>:8765' (pubkey E0lfBFvA..., added 2026-05-30)? [y/N]: y
# Removed 'http://<linux-host>:8765'.
```

`rookery known-hosts forget <host>` prompts for confirmation; pass `--yes`
to skip the prompt. Removing a stale or compromised entry means the next
`rookery join` to that URL will TOFU-pin the fresh key instead of comparing
against the old one.

**Headless A.** If A's sidecar runs in a detached tmux / systemd unit
(no interactive terminal), SSH in. Run `rookery approve` to clear the
pending queue from the command line.

**The old manual flow** (`rookery_join.py --mode mcp`, hand-paste the
JSON snippet) is still supported — see the `rookery_join.py` section of the
README — but it's no longer the recommended path.

---

## Bring up a team

Use `rookery up` to build a team of agents from one file. Run it in a project that has `rookery init`.

1. Write `team.json`. Give it a `session`, a `task` and a list of `agents`. A `reader` reads and does not edit. A `writer` gets its own git worktree. The README has a full example.
2. Run `rookery up team.json --no-launch`. Rookery builds the folders, the worktrees and `ROOKERY.md`, and starts no agent.
3. Read `ROOKERY.md` in the Rookery root. Then run `rookery up team.json` to start each agent in a pane.
4. In each new Claude Code pane, answer the trust dialog. A reader also shows an import dialog. You answer these one time in each pane.
5. The agents send mail with `rookery send`, `rookery inbox` and `rookery ack`. They do not use the old scripts.
6. When a writer is done, run `rookery finish NAME --message "TEXT"`. It asks three times, then commits, pushes and deletes the worktree. It never deletes the branch.
7. Run `rookery gc` to list old worktree rows. It deletes nothing. Run `rookery regen` to write the generated views again.

To join a peer mailroom, use `rookery join URL`. This was `rookery up URL` in earlier versions.

---

## Screen mail

The screener starts in shadow mode. It writes decisions and changes no mail.

1. Run `rookery screen run`. Add `--once` for one pass. The first run skips mail older than one day (`screener.max_age_s`).
2. Run `rookery screen status` to see the backend, the model host and the backlog.
3. Run `rookery screen review` and `rookery peek`. Then run `rookery screen mark DECISION_ID right` or `wrong` for each decision.
4. Run `rookery screen report` to see the agreement.
5. Run `rookery screen mode injection live`. A model check (`t1`, `hunch`, `t2`) needs 150 compared decisions first, and `t1` needs `screener.bands.t1`. The refusal tells you how many are missing.
6. In live mode a check that fails sends the mail to a person. The boss of the session gets a notice. Use `rookery screen release MAIL_ID` or `rookery screen quarantine MAIL_ID REASON`.
7. To close a thread, run `rookery thread close SESSION THREAD`. A closed thread refuses new mail.

`rookery session new` with no name makes a name of three words. The README has the `screener` configuration.

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
