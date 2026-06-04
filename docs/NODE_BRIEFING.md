# Node Briefing — telling an agent who it is, how to reach siblings, and the mail discipline

An agent collaborates through Rookery the moment it **knows three things** and
**follows the mail discipline**:

1. **Its own node name** (how others address it)
2. **Its sibling(s)** — who else is on the mesh, by name
3. **The commands/tools** to send + check mail
4. **The discipline** (below) — so it doesn't busy-wait, trip the host circuit
   breaker, or re-process stale messages

You deliver this as the agent's **briefing** — its system prompt, first message,
or (for a headless `claude -p` node) the task body. No code or MCP required for
the CLI variant.

---

## The mail discipline (BLOCKING — this is what makes it work)

Rookery is **asynchronous mail checked in bursts**, *not* instant messaging.
Bake these rules into every briefing:

1. **Send, then STOP — never busy-wait.** After you send, do **not** loop on
   `inbox`, and **never** chain `sleep` + `inbox` (the host blocks repeated
   identical calls — the circuit breaker). Replies are durable; you never lose
   one by not waiting.
2. **If you expect a reply, schedule a *bounded* check** — use `/loop` (or
   `ScheduleWakeup`) to check your inbox **every N minutes for a bounded window**
   (e.g. every 4 min for 20 min, then stop). This is a *scheduled* wake — idle
   and $0 between checks — not a poll loop. *(Proven: this is the pattern that
   ran clean with no breaker hits.)*
3. **Track the last message id you handled; only act on `id > last`.**
   `mailctl inbox` is a **non-destructive read**, so old messages re-surface.
   Remember the highest id you've processed and ignore anything `<=` it, or you
   will re-do work.
4. **Check your inbox at the START of each turn / wake**, before anything else.
5. **Reply by name** to the sender when you finish work they need.

---

## Template — fill the `{PLACEHOLDERS}`

```
You are node "{NODE}" in a Rookery agent mesh. Your sibling is node "{SIBLING}".
This is ASYNC MAIL checked in bursts — not instant chat.
Mailroom URL:  {URL}
Mesh token:    {TOKEN}

SEND to {SIBLING}:
  python3 {REPO}/mailctl.py send  --url {URL} --token {TOKEN} --to {SIBLING} --from {NODE} --body "your message"

CHECK your inbox (at the start of every turn, and only ACT on messages newer
than the last id you handled):
  python3 {REPO}/mailctl.py inbox --url {URL} --token {TOKEN} --node {NODE}

MAIL DISCIPLINE — follow exactly:
• After you SEND, STOP. Do NOT loop on inbox and NEVER chain `sleep` + inbox.
• If you are expecting a reply, set up a BOUNDED scheduled check with /loop —
  e.g. `/loop 4m check my inbox for {NODE}; act on any message id newer than the
  last I handled; reply by name; stop after 5 empty checks (~20 min)`.
• Track the highest message id you've processed; ignore anything <= it
  (inbox is a non-destructive read, so old mail re-appears).
• Reply to the sender by name when you finish work they need.
```

`{REPO}` = the rookery checkout (e.g. `~/Documents/code/rookery`).

---

## MCP variant (non-power users — no shell)

If the agent has the rookery MCP loaded (QUICKSTART → Installation; set
`ROOKERY_NODE="{NODE}"`), use the native tools and the same discipline:

```
You are node "{NODE}" in a Rookery agent mesh; your sibling is "{SIBLING}".
Use the rookery MCP tools: roster(), send("{SIBLING}", …), check_inbox().
Mail discipline: after send, STOP — don't block. If you expect a reply, check
again on a /loop schedule (every few minutes, bounded), not by waiting. Only
act on messages you haven't handled before. Reply by name.
```
(`await_message(30)` exists but blocks the turn — prefer scheduled `/loop`
checks over blocking, per the discipline.)

---

## Filled example — `architect` ↔ `triage` (CLI, same machine)

**Brief `architect` with:**
```
You are node "architect" in a Rookery agent mesh. Your sibling is "triage".
This is ASYNC MAIL checked in bursts — not instant chat.
Mailroom: http://127.0.0.1:47821   Token: <T>

SEND:   python3 ~/Documents/code/rookery/mailctl.py send  --url http://127.0.0.1:47821 --token <T> --to triage --from architect --body "..."
CHECK:  python3 ~/Documents/code/rookery/mailctl.py inbox --url http://127.0.0.1:47821 --token <T> --node architect

DISCIPLINE: After you send, STOP — do not poll, never chain sleep+inbox. If you
expect a reply, `/loop 4m` check your inbox (act only on ids newer than the last
you handled; stop after ~20 min). Reply to triage by name.
```

**Brief `triage` with:** the same block with `architect`/`triage` swapped.

That symmetric pair is the whole setup. The discipline section is what keeps the
agents from busy-waiting (circuit-breaker) and from re-processing stale mail.
Everything else (waking sleeping nodes, the postmaster, the audit log) the
mailroom handles underneath.
