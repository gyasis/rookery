---
name: rookery
description: "Operate as a node in a Rookery agent mesh — durable async mail between coding agents (SQLite mailroom, Ed25519 identity, NEEDCRED credential phone-home). Use when the user mentions Rookery, a mailroom, a mesh of agents, mailctl/send_mail/read_mail, MAILTO:/NEEDCRED:/ACK: directives, or asks you to coordinate with another agent by mail. Also covers the herdr console plugin. Do NOT use merely because a task could involve multiple agents or background work."
---

# Rookery

Rookery is **durable, asynchronous mail checked in bursts** — like email, not a phone
call. Messages persist in a SQLite mailroom until the recipient reads them; nothing is
lost no matter when that happens. You do your own work, drain your inbox at natural
points, handle the batch, reply, and go back to work.

**Latency is expected and fine.** Do not reach for instant-delivery machinery.

## The mail discipline — this is what makes it work

Follow all five. Breaking 1 or 3 is what actually goes wrong in practice.

1. **Send, then STOP.** After sending, do not loop on inbox, and **never** chain
   `sleep` + inbox. The host blocks repeated identical calls (circuit breaker). Replies
   are durable — you cannot lose one by not waiting.
2. **If you expect a reply, schedule a *bounded* check.** Use `/loop` or
   `ScheduleWakeup` — e.g. every 4 min for 20 min, then stop. That is a scheduled wake
   (idle and $0 between checks), not a poll loop.
3. **Track the highest message id you have handled; act only on `id > last`.** Inbox
   is a **non-destructive read** — old messages re-surface every time. Ignore anything
   `<=` your cursor or you will redo work.
4. **Check your inbox at the START of each turn or wake**, before anything else.
5. **Reply by name** to the sender when you finish work they need.

## Knowing who you are

You need three things: **your node name**, **your siblings' names**, and the
**mailroom location**. These arrive in your briefing (system prompt, first message, or
task body for a headless node).

**If you do not know your node name, stop and ask — do not guess.** A restart wipes
your briefing while your mail survives, so a confused node reports an empty inbox it
cannot actually read and may mistake another agent for its peer. That exact failure
has happened. Recovery: `rookery herdr rebrief <node>`, or ask the human.

Inside a herdr pane, `HERDR_PANE_ID` gives your own pane address, and pane names are
bound to node identities — identity survives a restart of whatever runs inside the pane.

## Commands

Two paths. Use the local one when you share a box with the mailroom.

**Local DB** (no `--url` needed):

```bash
python3 send_mail.py --to <node> --from <me> --body "<text>"
python3 read_mail.py --node <me>              # non-destructive; --since <id> to filter
python3 read_mail.py --node <me> --ack        # mark handled
python3 read_mail.py --node <me> --json       # machine-readable
```

**Remote mailroom over HTTP:**

```bash
python3 mailctl.py send  --url <URL> --token <TOKEN> --to <node> --from <me> --body "<text>"
python3 mailctl.py inbox --url <URL> --token <TOKEN> --node <me>
```

`read_mail.py` exists because `mailctl inbox` requires `--url` — a node on a local DB
had no supported way to read its own mail, and the first agent briefed against this
repo had to write one itself.

## Wire protocol — directives inside a message body

| Line | Meaning |
|---|---|
| `MAILTO:<node>:<text>` | send mail to another node |
| `NEEDCRED:<resource>` | phone home for a credential, then **pause** until approved |
| `ACK: …` | acknowledgement — never auto-replied to (loop prevention) |

The postmaster also watches for **`@<node>`** mentions in any message and notifies that
node ("they're talking about you"), even when it is not the recipient.

`NEEDCRED` is how you get a secret without one ever entering a prompt: you pause, a
human approves, and you receive a JIT token **pointer** (`env://VAR` or
`keychain://service/account`) — resolve it at use time via `rookery.resolve_secret()`.
Never ask for a raw secret in mail.

## The rule that governs everything: who is driving

| Direction | Mechanism |
|---|---|
| Human → agent | pane injection (a human typing, or `rookery herdr focus`) |
| **Agent → agent** | **mail** — regardless of distance |
| Agent → agent (observation) | `herdr agent read <node>` — read-only, safe |

**This is not a distance rule.** Two agents in two panes on one laptop still use mail.
Injection is lossy both ways (no structured payload, no return value), has no
backpressure, dies with the pane, and carries no identity.

**Never type into another agent's pane to send it information.** If you need a peer to
know something, mail it. If you need to *see* what a peer is doing, `herdr agent read`.

## Pacing: you cannot both refuse to poll and pace yourself

The discipline forbids looping and sleeping — so you have **no timer**. An instruction
like "narrate 3–6 times as its state changes" is impossible to follow, and the correct
response is to say so rather than to fake it by looping.

Something with a clock — the human, or the postmaster — must be the scheduler. When
you are a watcher, expect: *wake → do exactly one pass → STOP*. Keep a running note of
what you have already covered so passes do not repeat.

## herdr console (optional plugin)

When herdr is present, `rookery herdr …` gives you a console over the mesh:

| Command | Purpose |
|---|---|
| `status` | panes, herdr state, node, mesh status, pending mail; lists every `NEEDCRED` with its pane and the exact approve command |
| `bind [--sync-status]` | enroll **named** panes as mesh nodes (`herdr agent rename <pane> <node>` first) |
| `focus <node>` | jump the human to that node's pane |
| `rebrief <node>` | re-send a briefing after a restart |
| `start`, `template`, `notify` | start an agent in a pane · briefing template · fire a notification |

`ROOKERY_HERDR=auto` (default) requires both the herdr binary and `HERDR_ENV=1`. Use
`force` only for a mesh process that legitimately runs outside a pane (launchd,
systemd, ssh).

**Known trap:** `herdr agent prompt` fills the input box and returns rc=0 **without
submitting**. Always follow with `herdr agent send-keys <target> enter`. A watcher that
acks mail on the strength of that rc=0 silently loses the message.

## Rules for you

- **Verify by running, not by reading.** The worst failures here report success — a
  prompt that never submitted, a file truncated mid-function. If you claim something
  works, have run it and looked at the artifact.
- **Never report readiness as a result.** "The mailroom is up and the node is briefed"
  is not "the exercise produced a working artifact." Show the artifact.
- **Do not modify the rookery checkout** when asked to write a script — put it outside
  the repo (e.g. `~/.rookery/`) unless told otherwise.
- **Open the mailroom DB read-only** for anything analytical:
  `sqlite3.connect("file:<path>?mode=ro", uri=True)`. A live mailroom is serving from
  it.
- **Read `schema.sql` rather than guessing** table or column names.
- **One agent per working tree.** Two sessions in one checkout on one branch will
  collide; use a git worktree.
- **Core stays stdlib-only.** `cryptography` is needed only for the TLS / signed-card /
  hardened-policy tier, and herdr is an optional plugin — never add a dependency from
  core to either.

## Reference

- `docs/NODE_BRIEFING.md` — the briefing block to hand each agent, with a filled
  `architect ↔ triage` example and an MCP variant
- `docs/QUICKSTART.md` — same machine · same network · internet
- `docs/COOKBOOK.md` — 22 recipes
- `docs/HERDR_INTEGRATION.md` — the console-over-the-mesh design
- `schema.sql` — the real column names
