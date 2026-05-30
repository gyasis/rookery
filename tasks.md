# Rookery — `rookery up` v1 task list

**Source spec:** the paired-debate (Claude × Gemini, 2 rounds) on minimum-friction
peer enrollment. Decision: one-command join (`rookery up`), mDNS/UDP discovery,
A-side approval prompt with a 3-word "wormhole" slug as the trust root,
auto-merged `~/.claude.json`, sidecar-served zipapp bootstrap.

**Existing primitives the build STANDS ON (already shipped):**
- `mailroom_server.py` HTTP sidecar (`POST /invite` returns a per-node minted token + the Ed25519 card_pubkey pin — commit `c1b3cab`)
- `policy_hardened.py` `HardenedPolicy.mint_invite(node_id, may_task, ttl)` — fresh per-principal bearer token with TTL, in-memory eviction
- `gen_card_key.py` / `verify_card.py` — Ed25519 signed agent card
- `mailctl.py` — stdlib HTTP client (peer side)
- `rookery_join.py` — explicit invite-file consumer (`--mode verify|mcp|loop`)

**Conventions:** stdlib-only on the peer (B) wherever possible; one well-known dep
on A (`cryptography`, already used); tests are `bash`-style demo-script asserts
matching the existing `demo_*.sh` pattern; every change must keep
`./run_all_demos.sh` (free tier) green.

## Wave 1 — independent foundations (parallel)

- [ ] T001: Scaffold the `rookery_cli.py` stdlib argparse entrypoint with three subcommands `serve` / `up` / `approve` (stubs that print their name + parsed args; importable; placeholders for the flags each will need)
- [ ] T002: Per-node Ed25519 identity in `identity.py` — `load_or_create()` (creates `~/.rookery/id_ed25519` PEM mode 600, idempotent), `sign(bytes) -> bytes`, `pubkey_b64() -> str`
- [ ] T003: SSH-style known_hosts store in `known_hosts.py` (`~/.rookery/known_hosts` — `<host> <pubkey_b64> <label>  # added <iso8601>`): `load()`, `add(host, pubkey, label)`, `check(host, pubkey) -> 'ok'|'changed'|'unknown'`, atomic write with `.bak`, file mode 0600
- [ ] T004: mDNS / DNS-SD discovery in `discovery.py` — `discover_mailrooms(timeout=2.0) -> list[{name, host, port}]` via shell-out to `avahi-browse -trp _rookery._tcp` (Linux) or `dns-sd -B _rookery._tcp` (macOS); returns `[]` if neither binary present (NOT an error)
- [ ] T005: Idempotent `~/.claude.json` merger in `config_manager.py` — `merge_mcp_entry(name, command, args, env)` reads/creates the JSON, ensures `mcpServers`, sets `mcpServers[name]` (replacing if exists), writes atomically via `~/.claude.json.tmp.<pid>` + rename, backs up to `~/.claude.json.bak.<epoch>` only on actual change; rejects corrupt JSON

## Wave 2 — extensions to wave-1 files (depends on T001–T005)

- [ ] T006: UDP-broadcast discovery fallback in `discovery.py` — `announce_loop(port=8765, name='mailroom')` broadcasts JSON `{"name", "port", "pubkey_b64"}` to `<broadcast>:8888` every 5s; `listen(timeout=2.0) -> list[{name, host, port, pubkey_b64}]` (depends on T004)
- [ ] T007: New `handshake.py` plus `POST /join` route in `mailroom_server.py` — public endpoint, body `{node_id, pubkey_b64, slug}`, stores `{request_id, node_id, pubkey, slug, requested_at}` in `handshake.PENDING`, returns `{request_id, status: "pending"}` (depends on T002)

## Wave 3 — approval queue (depends on T007)

- [ ] T008: Approval queue endpoints in `handshake.py` and `mailroom_server.py` — `GET /pending` (auth) lists; `POST /approve` (auth) `{request_id, decision}` calls existing `pol.mint_invite()` on approve and attaches `{token, expires_at}` to the queue entry; `GET /join/<request_id>` (public, unguessable id) long-polls ~30s for `{status, token, card_pubkey}` (depends on T007)

## Wave 4 — CLI wiring of serve/up/approve (depends on T008 + foundations)

- [ ] T009: Extend the `serve` subcommand in `rookery_cli.py` — spawn `mailroom_server.py`, start `discovery.announce_loop`, and run a background thread that polls `handshake.PENDING` and flashes the approve prompt to the terminal (`Peer "X" wants to join. Does the joining machine show this code? -> <slug> Approve? [y/N]`); interactive `y/n` calls local `POST /approve` (depends on T001, T006, T008)
- [ ] T010: Implement the headless `approve` subcommand in `rookery_cli.py` — lists pending requests from `GET /pending` with their slug + pubkey + age, prompts per-entry (depends on T001, T008)
- [ ] T011: Implement the `up` subcommand in `rookery_cli.py` — discover (URL arg OR `discovery.discover_mailrooms` + `discovery.listen` merged), pick (1=auto, many=prompt, 0=error), generate 3-word slug, `identity.load_or_create()`, `POST /join`, print `Waiting for approval. Your code: <slug>`, long-poll `GET /join/<id>` up to 5 min, TOFU-check + `known_hosts.add` the served `card_pubkey`, call `config_manager.merge_mcp_entry("rookery", "python3", ["rookery_mcp.py"], {ROOKERY_NODE, ROOKERY_URL, ROOKERY_TOKEN})`, print success + wake-loop first-message (depends on T002, T003, T004, T005, T006, T007, T008)

## Wave 5 — bootstrap endpoint (depends on T001)

- [ ] T012: Sidecar-served zipapp installer — new `bootstrap.py` packaging `rookery_cli.py` + `identity.py` + `known_hosts.py` + `discovery.py` + `config_manager.py` + `mailctl.py` + `rookery_mcp.py` + `rookery.py` + `schema.sql` + `security.py` into a single `.pyz`, and `GET /bootstrap` in `mailroom_server.py` serving a one-line installer that writes `~/.local/bin/rookery` mode 755 (`curl -sSL http://A:8765/bootstrap | python3 -`) (depends on T001)

## Wave 6 — end-to-end proof (depends on T011, T012)

- [ ] T013: Write `demo_up.sh` proving the full LAN flow on one machine — reset DB, start `rookery serve` with `--card-key`, sanity-check `discovery.discover_mailrooms()`, run `rookery up http://127.0.0.1:8765` with the TEST-ONLY `ROOKERY_TEST_AUTOAPPROVE=$ADMIN_TOK` env auto-approving via `/approve` after a delay, assert `~/.claude.json` has the `rookery` MCP entry with the right env, send a piece of mail admin→claude-on-B and assert the inbox row, clean up (depends on T011, T012)

## Wave 7 — docs + suite wiring (depends on T013)

- [ ] T014: Update `README.md` files table + security/handshake section, and rewrite Scenario 4 of `docs/QUICKSTART.md` to lead with the new one-command flow (`rookery serve` on A; `curl http://<A>:8765/bootstrap | python3 -` then `rookery up` on B); remove the dangling 7-step manual instructions (depends on T013)
- [ ] T015: Add `demo_up.sh` to the FREE array in `run_all_demos.sh` (alphabetical near `demo_invite.sh`); confirm the suite passes (depends on T013)

## Out of scope (deferred to v2)

- Push-notification approval (`ntfy` / Pushover / desktop notifications) for remote-internet A.
- Windows-native mDNS (no `avahi` / `dns-sd` — UDP fallback works on Win but discovery integration deferred).
- UPnP / NAT-PMP automatic firewall punching.
- Revocation UI — v1 means hand-editing `~/.rookery/known_hosts`.
