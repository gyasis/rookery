---
name: rookery-up
description: "Interview the user, write a Rookery team file, check it, and hand back the exact `rookery up` command, explaining every part so the user learns to build it from the CLI. Use when the user says /rookery-up, \"set up a rookery session\", \"build a rookery team\", \"make me a team file\", or \"how do I write the rookery up command\". Do NOT use to read or send mail (that is the rookery skill)."
---

# /rookery-up — the team wizard

You are a tutor, not an installer. The goal is that the user can write the command alone next time.
So: ask, write the file, check it, print the command, and **explain each part**. Do not run
`rookery up` unless the user says to run it in this conversation; then run `--no-launch` first.

The command you are building:

```
rookery up TEAM_FILE [--session NAME] [--sweep SECONDS] [--no-launch] [--json]
```

## Step 0 — look before asking (no questions yet)

Run these and keep the results for the interview:

```bash
pwd                                   # the Rookery root = the folder where `rookery up` runs
rookery session list                  # names in use; the same name RESUMES that session
grep -l "<!-- ROOKERY START -->" CLAUDE.md AGENTS.md 2>/dev/null   # empty = `rookery init` not done
command -v rookery-wizard             # the checker used in step 3
```

If `rookery init` is not done, say so: the user runs `rookery init` once in that folder first
(`--harness claude|codex|pi|omp`). Do not run it yourself unless asked.

## Step 1 — the interview

Ask in few rounds. In Claude Code use AskUserQuestion (up to 4 questions per call); in another
harness ask in plain text with the options listed. Before each question, say in one line which
key or flag it sets. Put the recommended choice first.

**Round A — the session**
| Question | Sets | Notes |
|---|---|---|
| Session name? | `"session"` or `--session NAME` | Blank = Rookery makes a three-word name. A name from `rookery session list` that is **open** resumes it; Rookery refuses an **ended** one. Rookery keeps `legacy` for itself. 1-64 chars `A-Z a-z 0-9 . _ -`. |
| Name in the file, or only on the command line? | where the name lives | The flag wins over the file. In the file = shorter command. |
| What is the task? Any acceptance checks? | `"task"`, `"acceptance"` | Optional. Every agent sees them. |

**Round B — the agents** (ask how many and the names first, then one round per agent)
| Question | Sets | Options |
|---|---|---|
| Harness | `"harness"` | `claude`, `pi`, `omp`, `kimi`. **Not codex** — `up` refuses it ("Codex waits for R19"). |
| Role | `"role"` | `boss` (plans and sends asks, no repo), `reader` (reads and reports, no worktree), `writer` (own worktree and branch) |
| Writer only: task id, repo, claims | `"task_id"`, `"repo"`, `"claims"` | A writer needs a `task_id`; the branch is `<session>/<name>/<task_id>`. `repo` is relative to the team file, blank = this repo. `claims` = globs only this writer edits. A boss has none of these. |
| Persona (optional) | `"persona"` | One or two sentences. |
| Pane | `"binding"` | `herdr-pane` (default: leave the key out) or `tmux-pane`. |

**Round C — the session options**
| Question | Sets | Notes |
|---|---|---|
| Screening on? | `"screener": true` | Default off. On means the T1 model screens mail: it **costs model calls**. Say so. |
| Sweep interval? | `--sweep SECONDS` (or `"sweep_s"` in the file) | The safety-net watcher. Default 120, at least 60. Leave blank for the default. |
| File name? | `TEAM_FILE` | Default `team-<session>.json` in the root. Ask before you overwrite a file. |

## Step 2 — write the file

Write only the keys the user chose. Leave defaults out (no `binding` for `herdr-pane`, no
`screener` when off). Example:

```json
{
  "session": "quiet-river-fox",
  "task": "Fix the token refresh race",
  "acceptance": ["test_refresh passes 100 times"],
  "agents": [
    {"name": "lead", "harness": "claude", "role": "boss", "persona": "You plan and review."},
    {"name": "fixer", "harness": "kimi", "role": "writer", "task_id": "T1", "claims": ["auth/*.py"]}
  ]
}
```

## Step 3 — check it with Rookery's own rules

```bash
rookery-wizard --check TEAM_FILE      # exit 0 = Rookery accepts it; else it prints Rookery's reasons
```

It imports the real team loader from the installed zipapp and writes nothing. On a refusal, fix the
file with the user and check again. Never hand back a command for a file that fails the check.

## Step 4 — hand back the command and teach it

Show the file, then the two commands, then a table of the parts. Use the user's real values:

```
Step 1 — dry build (folders, worktrees, ROOKERY.md; no agent starts)
  rookery up team-quiet-river-fox.json --no-launch
Step 2 — launch (each agent in its pane; the same command resumes later)
  rookery up team-quiet-river-fox.json
```

| Part | What it does |
|---|---|
| `team-….json` | the team file; always the first argument |
| `--session NAME` | names the session; wins over the file (show it only when used) |
| `--sweep N` | the watcher interval (show it only when used) |
| `--no-launch` | build only |
| `--json` | one JSON object instead of text |

Then the three follow-up commands: `rookery session list`, `rookery session show NAME`,
`rookery session end NAME`. Remind: in each new Claude pane the user answers the folder-trust dialog
once; with no session name the output prints `Session: NAME (name made)` — write it down to resume.

Close with one line the user can try alone next time: "Next time, write the file and run
`rookery-wizard --check FILE`, then `rookery up FILE --no-launch`."

## Rules

- Never run `rookery up` (with or without `--no-launch`) unless the user asks in this conversation.
- Never write the Rookery database or `~/.rookery/config.json`.
- Never end, reuse or close a session or pane here. Report a name collision; let the user choose.
- A terminal-only version of this interview exists: `rookery-wizard` with no arguments.
