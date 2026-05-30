# Rookery — `rookery up` v1 task list (for dev-kid)

**Source spec:** the paired-debate (Claude × Gemini, 2 rounds) on minimum-friction
peer enrollment. Decision: one-command join (`rookery up`), mDNS/UDP discovery,
A-side approval prompt with a 3-word "wormhole" slug as the trust root,
auto-merged `~/.claude.json`, sidecar-served zipapp bootstrap.

**Existing primitives the build STANDS ON (already shipped):**
- `mailroom_server.py` HTTP sidecar (`POST /invite` returns a per-node minted
  token + the Ed25519 card_pubkey pin — commit `c1b3cab`)
- `policy_hardened.py` `HardenedPolicy.mint_invite(node_id, may_task, ttl)` —
  fresh per-principal bearer token with TTL, in-memory eviction
- `gen_card_key.py` / `verify_card.py` — Ed25519 signed agent card
- `mailctl.py` — stdlib HTTP client (peer side)
- `rookery_join.py` — explicit invite-file consumer (`--mode verify|mcp|loop`)

**Conventions:**
- Pure Python 3 stdlib on peer (machine B) wherever possible.
- One non-stdlib dep ok on A (`cryptography`, already used).
- Tests for each task are `bash`-style demo-script asserts, not pytest.
  The existing `demo_*.sh` pattern in the repo is the test surface.
- Every change must keep `./run_all_demos.sh` (free tier) green.
- Files NEVER added: `~/bin/*` (use `~/.local/bin/`). Generated artifacts
  do NOT go to this repo when they're throwaway (use `~/Documents/generated/`).

---

## T1 — CLI scaffolding (`rookery_cli.py` with three subcommands)

**Owner:** code
**Depends on:** —
**Files:** `rookery_cli.py` (new)

Stdlib `argparse` entrypoint exposing three subcommands: `serve`, `up`, `approve`.
Each is a stub for now — they just print the subcommand name + parsed args. This
unblocks every other task.

**Acceptance:**
- `python3 rookery_cli.py --help` lists `serve` / `up` / `approve`.
- `python3 rookery_cli.py serve --help`, `up --help`, `approve --help` work.
- `python3 -c "import rookery_cli"` succeeds (importable).
- Each subcommand has placeholders for the flags it will need (e.g. `up` takes
  optional positional `url`, `--node-id`, `--insecure-tls`).

---

## T2 — Per-node Ed25519 identity (`identity.py`)

**Owner:** code
**Depends on:** —
**Files:** `identity.py` (new)

Manage this node's Ed25519 keypair at `~/.rookery/id_ed25519` (PEM, mode 600)
and `~/.rookery/id_ed25519.pub`. Uses `cryptography` (already a repo dep).

**API:**
- `load_or_create() -> (priv, pub_bytes)` — idempotent; chmods 600 if newly created.
- `sign(message: bytes) -> bytes`
- `pubkey_b64() -> str` — base64 of raw 32-byte Ed25519 public key.

**Acceptance:**
- `python3 -c "import identity; p,k=identity.load_or_create(); print(identity.pubkey_b64())"`
  prints a 44-char base64 string; second call returns the SAME key.
- Permissions on `~/.rookery/id_ed25519` are `0600`.

---

## T3 — `~/.rookery/known_hosts` store (`known_hosts.py`)

**Owner:** code
**Depends on:** —
**Files:** `known_hosts.py` (new)

SSH-style plain-text known_hosts: one line per peer.
Format: `<host_or_url> <pubkey_b64> <label>  # added <iso8601>`

**API:**
- `load() -> list[Entry]`
- `add(host, pubkey_b64, label)` — atomic write + .bak.
- `check(host, pubkey_b64) -> 'ok' | 'changed' | 'unknown'`

**Acceptance:**
- Round-trip add/load returns the same entry.
- `check()` returns `'changed'` when the same host has a different pubkey
  (the SSH-style MITM-warning case).
- File mode is `0600`.

---

## T4 — mDNS / DNS-SD discovery via shell-out (`discovery.py`)

**Owner:** code
**Depends on:** —
**Files:** `discovery.py` (new)

Discover peer mailrooms advertising `_rookery._tcp` on the LAN by shelling out
to `avahi-browse -trp _rookery._tcp` (Linux) or `dns-sd -B _rookery._tcp` (macOS).
Parse the output for `(name, host, port)`. **No `zeroconf` dep.**

**API:**
- `discover_mailrooms(timeout=2.0) -> list[{name, host, port}]`
- Returns `[]` if neither binary is present (NOT an error).

**Acceptance:**
- On a host with `avahi-browse` installed, calling against a manually-registered
  service returns the expected entry.
- Missing binary → returns `[]`, no exception.

---

## T5 — UDP-broadcast discovery fallback (`discovery.py`)

**Owner:** code
**Depends on:** T4
**Files:** `discovery.py` (extend)

When mDNS returns nothing (no `avahi` / `dns-sd`, or guest WiFi blocks multicast),
fall back to a simple UDP broadcast on port 8888.

**API:**
- `announce_loop(port=8765, name='mailroom')` — send `{"name", "port",
  "pubkey_b64"}` JSON datagrams to `<broadcast>:8888` every 5 s.
- `listen(timeout=2.0) -> list[{name, host, port, pubkey_b64}]`

**Acceptance:**
- Running `announce_loop` in one process and `listen` in another on the same
  host detects the announce within 5 s.
- `listen` cleans up its socket on timeout.

---

## T6 — `rookery serve` registers DNS-SD + UDP announce

**Owner:** code
**Depends on:** T1, T4, T5
**Files:** `rookery_cli.py` (extend `serve`), `discovery.py`

`rookery serve` spawns `mailroom_server.py` AND starts the discovery
announces (DNS-SD via `avahi-publish-service` shell-out if present, plus the
UDP broadcast from T5).

**Acceptance:**
- `rookery serve` starts the sidecar and returns when killed.
- `avahi-browse _rookery._tcp` (on Linux) sees the mailroom while it's running.
- `discovery.listen()` from another process also sees the announce.

---

## T7 — `POST /join` endpoint (handshake) (`handshake.py`)

**Owner:** code
**Depends on:** T2
**Files:** `handshake.py` (new), `mailroom_server.py` (one new route)

Peer B `POST /join` with body `{node_id, pubkey_b64, slug}`. Server creates a
pending request with id (random hex), stores `{request_id, node_id, pubkey,
slug, requested_at}` in an in-memory queue (`handshake.PENDING`). Returns
`{request_id, status: "pending"}`.

The endpoint is **public** (no auth) — it's the door peers knock on. Anti-DoS
later if needed.

**Acceptance:**
- `curl -X POST http://localhost:8765/join -d '{...}'` returns `{request_id, status}`.
- The request shows up in `handshake.list_pending()`.

---

## T8 — Approval queue + `POST /approve`, `GET /pending`, `GET /join/<id>`

**Owner:** code
**Depends on:** T7, existing `HardenedPolicy.mint_invite`
**Files:** `handshake.py`, `mailroom_server.py`

- `GET /pending` (auth) — list pending requests.
- `POST /approve` (auth) `{request_id, decision: "approve"|"deny"}` — on
  approve: call `pol.mint_invite(node_id, may_task=["*"], ttl_minutes=...)` and
  ATTACH the resulting `{token, expires_at}` to the queue entry.
- `GET /join/<request_id>` (PUBLIC, but request_id is unguessable) — peer B
  long-polls (~30 s) for `{status: "approved", token, card_pubkey}` or
  `{status: "denied"}`.

**Acceptance:**
- Approval flow end-to-end: B posts /join → A posts /approve → B's GET /join/id
  returns the minted token + card pubkey.
- Deny flow: GET /join/id returns `{status: "denied"}`.
- Unknown request_id: 404.

---

## T9 — A-side interactive approval prompt during `rookery serve`

**Owner:** code
**Depends on:** T6, T8
**Files:** `rookery_cli.py` (extend `serve`)

`rookery serve` runs a background thread that polls the local
`handshake.PENDING` queue and, when a new request appears, flashes to the
terminal:
```
[ROOKERY] Peer "claude-on-B" wants to join.
          Does the joining machine show this code?  →  crimson-fox-jump
          Approve? [y/N]
```
The interactive approve calls the existing `/approve` endpoint locally.

**Acceptance:**
- New request → prompt appears within ~1 s.
- `y` then Enter → request approved, queue entry updated, peer's GET /join/id
  returns the token.
- `n` → denied.
- Terminal is restored on Ctrl-C without zombie threads.

---

## T10 — `rookery approve` headless subcommand

**Owner:** code
**Depends on:** T8
**Files:** `rookery_cli.py` (extend `approve`)

For headless A: `rookery approve` lists pending requests with their slug, then
interactively prompts for each:
```
$ rookery approve
1) claude-on-B    slug=crimson-fox-jump    pubkey=qX8...  (45 s ago)
> Approve 1? [y/N/skip]:
```

**Acceptance:**
- Lists pending entries from `GET /pending`.
- Confirms each interactively; updates via `POST /approve`.

---

## T11 — Idempotent `~/.claude.json` merger (`config_manager.py`)

**Owner:** code
**Depends on:** —
**Files:** `config_manager.py` (new)

**API:** `merge_mcp_entry(name, command, args, env) -> {written: bool, backup: str|None}`

- Reads `~/.claude.json`, creates `{}` if missing.
- Ensures `mcpServers` key exists.
- Sets `mcpServers[name] = {"command", "args", "env"}` — replacing if name
  already exists (idempotent, NO duplicates).
- Writes to `~/.claude.json.tmp.<pid>` then atomic rename.
- Backs up the prior file to `~/.claude.json.bak.<epoch>` ONLY if it changed.

**Acceptance:**
- First call adds the entry; second call with same args is a no-op (no new
  backup written).
- Second call with DIFFERENT env updates the entry, writes a new backup.
- Corrupt JSON → refuses, does not overwrite, prints a clear error.

---

## T12 — `rookery up` end-to-end (`rookery_cli.py up`)

**Owner:** code
**Depends on:** T2, T3, T4, T5, T7, T8, T11
**Files:** `rookery_cli.py` (extend `up`)

Flow when user runs `rookery up [url]`:

1. Resolve target: if `url` given use it; else run `discovery.discover_mailrooms()`
   + `discovery.listen()` and merge.
2. Pick: 1 found → proceed; multiple → prompt; zero → error with hint.
3. Generate a 3-word slug from `/usr/share/dict/words` (or vendored 256-word
   list) → keep this rare-but-readable.
4. Load/create local Ed25519 keypair (`identity.load_or_create`).
5. `POST /join` with `{node_id, pubkey_b64, slug}` → get `request_id`.
6. Print: `Waiting for approval. Your code: <slug>`
7. Long-poll `GET /join/<request_id>` until approved or denied (timeout 5 min).
8. Save the served `card_pubkey` to `known_hosts` (after TOFU `check`).
9. `config_manager.merge_mcp_entry("rookery", "python3", ["rookery_mcp.py"],
   {"ROOKERY_NODE": node_id, "ROOKERY_URL": url, "ROOKERY_TOKEN": token})`.
10. Print success block + the wake-loop first-message text.

**Acceptance:**
- Against a locally-running `rookery serve` + auto-approve helper, one
  invocation gets a fully populated `~/.claude.json` entry, exits 0.
- Pubkey-change scenario (saved pubkey for host doesn't match served card) →
  exits 1 with a clear SSH-style warning, does not write config.
- Approval-denied scenario → exits 1, does not write config.

---

## T13 — Sidecar-served zipapp bootstrap (`bootstrap.py` + `/bootstrap`)

**Owner:** code
**Depends on:** T1
**Files:** `bootstrap.py` (new), `mailroom_server.py` (one route)

`GET /bootstrap` returns a single-file Python **zipapp** containing
`rookery_cli.py` + `identity.py` + `known_hosts.py` + `discovery.py` +
`config_manager.py` + `mailctl.py` + `rookery_mcp.py` + `rookery.py` +
`schema.sql` + `security.py`. The user runs on the peer ONCE:

```
curl -sSL http://A:8765/bootstrap | python3 -
```

The piped script writes the zipapp to `~/.local/bin/rookery` (`chmod +x`),
prints `OK rookery installed`, then exits. From then on, `rookery up` is the
daily UX.

**Acceptance:**
- `curl http://localhost:8765/bootstrap > /tmp/r.pyz && python3 /tmp/r.pyz --help`
  shows the `rookery_cli` help.
- The bootstrap script (the bytes returned by GET /bootstrap when invoked with
  no zipapp arg, OR a separate GET /install) writes
  `~/.local/bin/rookery` and chmods it 755.

---

## T14 — End-to-end LAN demo (`demo_up.sh`)

**Owner:** code
**Depends on:** T12, T13
**Files:** `demo_up.sh` (new)

Full flow proof on a single machine (simulating A + B):

1. Reset DB; start `rookery serve` in background (with `--card-key`).
2. Verify discovery works: `python3 -c "import discovery; print(discovery.discover_mailrooms())"`.
3. `rookery up http://127.0.0.1:8765 --auto-approve-with $ADMIN_TOK` — the
   `--auto-approve-with` flag (TEST ONLY) auto-approves via the existing
   `/approve` endpoint after a half-second delay. (Don't ship that flag in
   real CLI help; gate behind `ROOKERY_TEST_AUTOAPPROVE`.)
4. Inspect `~/.claude.json` to confirm the `rookery` MCP entry exists and
   has the right env.
5. Send a piece of mail from "admin" to "claude-on-B" via the sidecar /send.
6. Confirm the inbox shows the mail.
7. Clean up.

**Acceptance:** exits 0; the assertions print PASS for each step.

---

## T15 — README + QUICKSTART updates

**Owner:** docs
**Depends on:** T14
**Files:** `README.md`, `docs/QUICKSTART.md`

- README Files table: add `rookery_cli.py`, `discovery.py`, `identity.py`,
  `known_hosts.py`, `config_manager.py`, `bootstrap.py`.
- README Security/handshake section: explain the slug-and-approve trust model.
- QUICKSTART Scenario 4: REPLACE the current 7-step MCP-bridge instructions
  with the new one-command flow:
  ```
  # On A (once):
  rookery serve
  # On B (once):
  curl -sSL http://<A>:8765/bootstrap | python3 -   # installs ~/.local/bin/rookery
  rookery up                                         # mDNS + slug + auto-config
  ```

**Acceptance:** docs read coherently; no dangling references to the old flow.

---

## T16 — Wire `demo_up.sh` into `run_all_demos.sh` (free suite)

**Owner:** code
**Depends on:** T14
**Files:** `run_all_demos.sh`

Add `demo_up.sh` to the `FREE` array (alphabetical place, near `demo_invite.sh`).

**Acceptance:** `./run_all_demos.sh` runs `demo_up.sh` and reports PASS.

---

## Out of scope (deferred to v2)

- Push-notification approval (ntfy / Pushover) for remote-internet A.
- Windows-native mDNS (no `avahi` / `dns-sd`).
- UPnP / NAT-PMP firewall punching.
- Revocation UI (v1: hand-edit `~/.rookery/known_hosts`).
