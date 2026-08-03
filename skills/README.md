# skills/

Agent-facing skills shipped with Rookery. A skill teaches **any** agent session the
mail discipline before it ever joins a mesh — distinct from a **node briefing**
(`docs/NODE_BRIEFING.md`), which tells one specific node its name, its siblings, and
its mailroom at runtime.

| | Skill (`skills/rookery/SKILL.md`) | Node briefing (`docs/NODE_BRIEFING.md`) |
|---|---|---|
| Scope | any session, before joining | one node, at runtime |
| Carries | the discipline, the protocol, the commands | identity, siblings, mailroom URL + token |
| Delivered | installed once into the harness | system prompt / first message / task body |
| Survives restart | yes (it is on disk) | no — re-send with `rookery herdr rebrief` |

You want both. The skill stops an agent busy-waiting or re-processing stale mail; the
briefing stops it forgetting which node it is.

## Install

```bash
mkdir -p ~/.claude/skills/rookery
cp skills/rookery/SKILL.md ~/.claude/skills/rookery/SKILL.md
```

Skills must be **directory-form** — `~/.claude/skills/<name>/SKILL.md`. A flat
`~/.claude/skills/<name>.md` is silently ignored by the loader. Restart the agent
afterwards; the skills directory is read at startup only.

For other harnesses, paste the file into whatever global-instructions mechanism they
provide.
