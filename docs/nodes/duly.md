# Node contract: `duly` (the Duly work machine)

Target path in the Rookery repo: `docs/nodes/duly.md`. Both agents read this file before they send or read mail
between razer and `duly`. It records the limits of each side. If code and this file disagree, fix one of them in
the same change.

Written 2026-10-25 by the WSL (`duly`) side. Source: `~/Documents/generated/duly-mailbox-razer-brief.md` on razer.

## 1. Roles

| | `duly` (WSL, Duly work machine) | `razer` (mailroom machine) |
|---|---|---|
| Network | Can reach razer. Razer cannot reach it. | Cannot open a connection to `duly`. |
| Role | **Drives all traffic.** It polls, claims, acks and sends. | Runs the mailroom. It puts mail in the queue and waits. |
| Identity | Node token for `duly`. Never its IP: it has used `.127` and `.128`. | Node `razer`. |
| Holds | The Harbor vault, `cred`, work data. | The mailroom DB, the file store, the timer. |

Razer never starts a connection to `duly`. "Razer sends" means that razer adds a row to its own mailroom.
`duly` collects the row on its next poll.

## 2. Transport

- **LAN only.** Do not use gists, GitHub, cloud storage or an outside relay, even for masked content.
- HTTPS to the mailroom on razer, with a bearer token. `duly` pins the SHA-256 of the DER cert. It does not
  check the hostname, because the cert SAN does not hold the LAN IP.
- The token lives in `~/dev/.env` as `ROOKERY_RAZER_TOKEN` on `duly`. Get it through `cred` service
  `rookery-razer`. Do not put it in mail, a file store or a repo.
- The policy is `DefaultPolicy`, with one shared token. The mailroom trusts the `sender` and `node` fields.
  Thus each side uses only its own name.
- SSH is for setup and debug only (`service-not-ssh.md` R-SVC2). No mail goes over SSH.
- The client on `duly` is `~/.local/bin/duly-mail`.
- Files: until the mailroom has `POST /files` and `GET /files/<sha256>`, text files go inline. Each file is a
  `file: <sha256> <name> (<n> bytes, inline)` line and a block fenced with `~~~~~~`. The sha256 is of the text
  in the block. The receiver checks it.

## 3. Message states (from `schema.sql` and `rookery.py`)

| Status | Set by | Column | Meaning for the other side |
|---|---|---|---|
| `pending` | send | `created_at` | in the queue, not picked up |
| `inflight` | `/claim` | `claimed_at` | **picked up.** This is the read receipt. |
| `done` | `/ack` | `delivered_at`, `delivered=1` | **handled** |
| `held` / `quarantined` / `escalated` / `dlq` | gate, screener (004), relay | `reason`, `note` | stopped; read the reason |

- Trap: the column `delivered` means **acked**, not picked up. Use `claimed_at` for "did `duly` read it".
- `requeue_stale()` returns `inflight` rows with no ack to `pending`. `duly` must ack every message that it handled.
  Otherwise the message comes back.

## 4. Limits each side must respect

**`duly` side:**
1. It polls only. It cannot receive a push. Delay = time to the next session start or the next `duly-mail fetch`.
2. It is off the home LAN some days. Then it cannot reach razer. Replies wait in a local outbox and go out on the
   next fetch that reaches razer.
3. Every outbound body and file goes through `harborize temp` (reversible mask) first. If the residual scan
   exits 3, nothing goes out.
4. The vault stays on `duly`. Names and ids in mail from `duly` are realistic fakes.
5. Auto mode blocks raw remote writes. `duly` sends only through `duly-mail`, which has an allow rule.
6. Memory is small (WSL). Keep files in one task small. Large data stays on `duly`. The reply says where it is.

**`razer` side:**
1. It cannot restore masked values. It must not try, and it must not ask for the vault.
2. It cannot wake `duly`. For mail with no claim after N hours, it notifies Gyasi on razer.
3. It reads `duly` liveness from the mailroom log of `/inbox` polls. Do not send heartbeat mail.
4. Tasks ask for work and results. They do not ask for PHI or work credentials. `duly` refuses those tasks.

**Both sides:**
- Loop limits (`turn_block.py`): 10 messages per task and pair, 30 per pair (`loop_limit`, `loop_pair_limit`
  in the Rookery config). Do not send status chatter.
- A `kind: ACK` message never gets an automatic reply.

## 5. Task format

- Task id: `YYYY-MM-DD-<slug>`, for example `2026-10-25-lab-result-model-prd`. Use one task id per task thread.
- `rookery send duly "<title>" --task <id> --kind ask`. Put the body in the mail. Put large text in a file.
- Each task states: what to do, why, acceptance criteria, and what comes back (`expects:`).
- Lint every task and every PRD with `ste-lint` before you send it.

## 6. Lifecycle of one task

1. `razer` sends `ask` -> `pending`.
2. `duly` runs `duly-mail fetch` -> `/inbox`, then `/claim` -> `inflight`. Razer now sees `claimed_at`.
3. `duly` works. If it needs input, it sends `kind: ask` back on the same task id.
4. `duly` runs `duly-mail reply <id>` -> mask, scan, upload files, `kind: answer`. Then it acks the task -> `done`.
5. Razer reads the answer and fetches the files by sha256.

## 7. Change control

- Change this file and the code in the same commit.
- A change to a limit in section 4 needs Gyasi's approval.
