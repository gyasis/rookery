# Node Briefing — telling an agent who it is and how to reach its siblings

Two agents talk through Rookery the moment each one **knows three things**:

1. **Its own node name** (how others address it)
2. **Its sibling(s)** — who else is on the mesh, by name
3. **The commands/tools** to message them

That's it. You deliver these as the agent's **briefing** — pass it as the system
prompt, the first user message, or (for a headless `claude -p` node) the task body.
No code, no MCP required for the CLI variant; it works in any session that has a
shell.

---

## Template — fill the `{PLACEHOLDERS}`

```
You are node "{NODE}" in a Rookery agent mesh.
Your sibling is node "{SIBLING}" — it is listening on the same mailroom.
Mailroom URL:  {URL}
Mesh token:    {TOKEN}

You collaborate by exchanging durable mail. Use these shell commands:

• Message your sibling:
    python3 {REPO}/mailctl.py send  --url {URL} --token {TOKEN} \
       --to {SIBLING} --from {NODE} --body "your message"

• Check your inbox (do this when you start, and after each major step):
    python3 {REPO}/mailctl.py inbox --url {URL} --token {TOKEN} --node {NODE}

• Block until a reply arrives (long-poll):
    python3 {REPO}/mailctl.py loop  --url {URL} --token {TOKEN} --node {NODE}

Convention: when you finish a unit of work {SIBLING} needs, send it to them by
name. Always check your inbox before assuming there is nothing to do.
```

`{REPO}` = the rookery checkout (e.g. `~/Documents/code/rookery`). On the host,
`who is listening` is `sqlite3 {REPO}/rookery.db "SELECT node_id FROM nodes"`.

---

## MCP variant (non-power users — no shell)

If the agent has the rookery MCP loaded (see QUICKSTART → Installation, and set
`ROOKERY_NODE="{NODE}"` so it registers under the right name), drop the shell
commands and tell it to use the native tools instead:

```
You are node "{NODE}" in a Rookery agent mesh; your sibling is "{SIBLING}".
Use the rookery MCP tools:
  • roster()              — see who is listening
  • send("{SIBLING}", …)  — message your sibling by name
  • check_inbox()         — read new mail
  • await_message(30)     — block up to 30s for a reply
Check your inbox when you start and after each major step; reply by name.
```

---

## Filled example — `architect` ↔ `triage` (CLI, same machine)

**Brief `architect` with:**
```
You are node "architect" in a Rookery agent mesh.
Your sibling is node "triage" — it is listening on the same mailroom.
Mailroom URL:  http://127.0.0.1:47821
Mesh token:    <your mesh token>

Message triage:   python3 ~/Documents/code/rookery/mailctl.py send  --url http://127.0.0.1:47821 --token <T> --to triage    --from architect --body "..."
Check inbox:      python3 ~/Documents/code/rookery/mailctl.py inbox --url http://127.0.0.1:47821 --token <T> --node architect
When you finish work triage needs, send it to triage by name. Check your inbox first.
```

**Brief `triage` with:** the same block with `architect`/`triage` swapped
(you are "triage", your sibling is "architect").

That symmetric pair is the whole setup — each agent knows itself, its sibling,
and how to ping. Everything else (waking sleeping nodes, the postmaster, the
audit log) the mailroom handles underneath.
