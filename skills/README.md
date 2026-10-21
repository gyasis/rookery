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

For other harnesses, paste the file into their global-instructions setting.
