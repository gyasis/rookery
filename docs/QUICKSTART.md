# Rookery Quickstart

Rookery gives coding agents durable mail. Agents in one Rookery session send each other mail
through a SQLite mailroom, and Rookery wakes an idle agent when mail needs action. Every sender
needs a session. The DB is the audit log — plain SQL, queryable at any time.

Prerequisites: Python 3 stdlib only for the core.

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
rookery --help
```

`build_pyz` bundles every module. It reads its SQL schema from inside the archive. A machine
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
- **Security tier (TLS, signed cards, hardened policy):**
  `pip install cryptography` (the one non-stdlib dependency).

---

## Same machine: a session

```bash
cd ~/Documents/code/<project>
rookery init                       # project section + hooks
rookery session new --name alpha   # one session
ROOKERY_SESSION=alpha ROOKERY_NODE=architect claude   # one agent in each pane
ROOKERY_SESSION=alpha ROOKERY_NODE=reviewer claude
```

The agents send mail with a `rookery` block at the end of a turn, or with
`rookery send reviewer "please review" --task T1`. They read with `rookery inbox` and
`rookery ack ID ...`. A send with no session exits 3 and stores nothing. Run
`rookery identity show` to see the session and identity Rookery finds.

To start a whole team in panes, see [Bring up a team](#bring-up-a-team).

---

## A mailroom for other machines

A remote node reads its mail through a mailroom on the session machine. It cannot send: the
mailroom has no session for a remote sender, so `/send` answers 403 `no-session`.

The mailroom binds `127.0.0.1` by default and needs no token there. A public `--host` (such as
`0.0.0.0`) needs `--allow-public` and a token (`--token` or `$ROOKERY_TOKEN`). A bearer token
authenticates but does NOT encrypt. On an untrusted network, add TLS.

### Security stack

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

**Step 4 — Start the hardened mailroom:**

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

**Step 5 — Verify the signed card:**

```bash
python3 verify_card.py https://<your-public-hostname>:8765
# signature: VALID  (exit 0) — or INVALID (exit 1) if tampered / unsigned
```

`./demo_signed_card.sh` runs the signed card check locally.

### A remote node joins: `rookery join`

On the mailroom machine A:

```bash
python3 rookery_cli.py serve --host 0.0.0.0 --allow-public --port 8765 \
  --token "$ROOKERY_TOKEN" --card-key rookery-card-key.pem
```

On the remote machine B, install once, then join:

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

B prints a 3-word code. A asks you to approve the join and shows the same code:

```
[ROOKERY] Peer wants to join the mesh.
          node id : peer-blade-15
          pubkey  : E0lfBFvAegB3tm33jcKG...
          slug    : plum-basil-plum
  Does the joining machine show this code? Approve? [y/N]
```

You hit `y` on A. B receives a per-node bearer token and A's card pubkey. It **TOFU-pins** the
pubkey to `~/.rookery/known_hosts`, writes the token to `~/.rookery/remote/<node>.token`
(mode 600) and prints the lines to set:

```bash
export ROOKERY_URL=https://<A-host>:8765
export ROOKERY_NODE=peer-blade-15
export ROOKERY_TOKEN="$(cat ~/.rookery/remote/peer-blade-15.token)"
rookery check            # read the mail for peer-blade-15
```

On A, mail goes to the remote node from a session: make an identity of that name in the
session, then run `rookery send peer-blade-15 "SAY" --task T1`.

**Trust model.** The slug is the human-verifiable handshake — if A's prompt
shows a different slug than B's, deny. After approval the served card pubkey
is TOFU-pinned. A later pubkey change fires an SSH-style key-changed
warning, and `rookery join` stops.

**Approve from your phone (out-of-band notifications).** If you are not at
A's terminal when B knocks, you get a push notification on your
phone or desktop. Set `ROOKERY_NOTIFY_METHOD` to one of: `none` (default — silent), `ntfy`,
`pushover`, or `webhook`. For ntfy: also set `ROOKERY_NTFY_TOPIC=<your-topic>`
(uses ntfy.sh, which is free and needs no signup). For Pushover: set both
`ROOKERY_PUSHOVER_TOKEN=<app-token>` and `ROOKERY_PUSHOVER_USER=<user-key>`.
For a generic webhook: set `ROOKERY_NOTIFY_WEBHOOK_URL=<your-url>` plus the
optional `ROOKERY_NOTIFY_WEBHOOK_TOKEN=<bearer>` for authenticated endpoints.
The notification carries the 3-word slug and a link to the
in-browser approval form. Notifier failures **never** block the join. They
log to stderr and nothing else happens.

**Approval form (`GET /approve`).** The link in the notification opens
`https://<A>:8765/approve?request_id=<rid>`. The page shows the join details with
**Approve** and **Deny** buttons and a bearer-token field. Its POST goes to the auth-gated
`/approve` endpoint, so only an admin with the token can approve or deny.

**Trusted peers (`rookery known-hosts`).**

```bash
rookery known-hosts list
rookery known-hosts forget http://<linux-host>:8765    # asks first; --yes skips the prompt
```

**Headless A.** If A's mailroom runs in a detached tmux or a systemd unit, run
`rookery approve` to clear the pending queue from the command line.

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

1. Screening is off by default. Set `screener.enabled` to `true` in `~/.rookery/config.json`, or `"screener": true` in the team file. Then the gate starts one pass when mail arrives, and the sweep of the session runs one each round. You can also run `rookery screen run` by hand: a loop with one pass every 300 seconds. Add `--once` for one pass. The first run skips mail older than one day (`screener.max_age_s`).
2. Run `rookery screen status` to see the backend, the model host and the backlog.
3. Run `rookery screen review` and `rookery peek`. Then run `rookery screen mark DECISION_ID right` or `wrong` for each decision.
4. Run `rookery screen report` to see the agreement.
5. Run `rookery screen mode injection live`. A model check (`t1`, `hunch`, `t2`) needs 150 compared decisions first, and `t1` needs `screener.bands.t1`. The refusal tells you how many are missing.
6. In live mode a check that fails sends the mail to a person. The boss of the session gets a notice. Use `rookery screen release MAIL_ID` or `rookery screen quarantine MAIL_ID REASON`.
7. To close a thread, run `rookery thread close SESSION THREAD`. A closed thread refuses new mail.

`rookery session new` with no name makes a name of three words. The README has the `screener` configuration.

---

## Where to go next

- `README.md` — full reference: sessions, teams, the wake, the screener, the mailroom and the
  security policy API.
- `skills/rookery/SKILL.md` — what an agent reads to use Rookery mail.
- `docs/index.html` — design rationale, paired debate, and A2A protocol survey.
