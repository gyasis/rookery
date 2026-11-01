# skills/

Agent-facing skills that ship with Rookery. A skill teaches any agent session how to
use Rookery mail. It differs from the project section, which tells one project's
agents who they are.

| | Skill (`skills/rookery/SKILL.md`) | Project section + session-start hook (0.7) |
|---|---|---|
| Scope | any session, loaded on demand | one project, every session |
| Carries | how to read mail, send a block, never poll, the loop breaker | the same rules, plus your identity (`rookery init` writes `rookery_section.md` into CLAUDE.md or AGENTS.md) |
| Delivered | installed once into the harness | project file, and the hook at session start |
| Survives restart | yes (it is on disk) | yes (it is on disk) |

In 0.7 the project section and the session-start hook take the place of the old node briefing (`docs/NODE_BRIEFING.md`). The briefing now applies only to the legacy node mesh.

The skill and the section say the same thing. Install the skill so an agent knows Rookery before it joins a project.

## Install

```bash
mkdir -p ~/.claude/skills/rookery
cp skills/rookery/SKILL.md ~/.claude/skills/rookery/SKILL.md
```

Use the directory form: `~/.claude/skills/<name>/SKILL.md`. The loader ignores a flat
`~/.claude/skills/<name>.md`. Restart the agent afterwards. The agent reads the skills
directory only at startup.

Other harnesses read the same `SKILL.md` from their own skills directory. Link the
Claude copy so that one file serves them all:

| Harness | Skills directory |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Kimi Code, and any harness that reads the shared directory | `~/.agents/skills/` |
| Codex | `~/.codex/skills/` |
| pi | `~/.pi/agent/skills/` |
| omp | `~/.omp/agent/skills/` |

```bash
ln -s ~/.claude/skills/rookery ~/.agents/skills/rookery
```

Kimi Code reads instructions only from `AGENTS.md`, never from `CLAUDE.md`. It reads MCP
servers from `~/.kimi-code/mcp.json`, and an entry takes the form
`{"transport": "http", "url": "http://127.0.0.1:7702/mcp"}`.

## `rookery-up` — the team wizard

`skills/rookery-up/SKILL.md` asks the user questions, writes a team file, and checks it.
It then prints the `rookery up` command and explains each part, so the user learns to
write the command alone. It never runs `rookery up` unless the user asks.

The skill checks each team file with `rookery-wizard --check FILE`. That script imports the
team loader from the installed zipapp and writes nothing. Run `rookery-wizard` with no
arguments to get the same interview in a terminal, without an agent.

```bash
mkdir -p ~/.claude/skills/rookery-up
cp skills/rookery-up/SKILL.md ~/.claude/skills/rookery-up/SKILL.md
install -m 755 skills/rookery-up/rookery-wizard ~/.local/bin/rookery-wizard
```
