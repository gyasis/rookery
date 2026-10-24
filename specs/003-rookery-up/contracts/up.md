# Contract: `rookery up`, `join`, `finish`, `gc`, `regen` and the hooks

**Feature**: [../spec.md](../spec.md). **Decisions**: [../research.md](../research.md).

Exit codes follow `cli_rookery_session`: 0 success, 2 bad input, 3 no session, 4 database busy. Each command
has `--json`. Each error text says what to do next and passes `ste-lint` (FR-033).

## `rookery join` (renamed, D1)

```text
rookery join [URL] [--node-id ID] [--insecure-tls]
```

The same arguments and behavior as the old `rookery up` (`cli_up.py`, now `cli_join.py`). Only the name
changes.

## `rookery up`

```text
rookery up TEAM_FILE [--no-launch] [--json]
```

- `TEAM_FILE` is a path to a JSON team file. If the argument starts with `http://` or `https://`, the
  command exits 2: "`rookery up` now builds a team. To join a peer, use `rookery join URL`."
- `--no-launch` builds everything and runs no agent.
- Order of work: [../research.md](../research.md) D22.

### Team file

```json
{
  "session": "auth-fix",
  "task": "Fix the token refresh race.",
  "acceptance": ["test_refresh passes 100 times", "no change to the public API"],
  "agents": [
    {"name": "scout", "harness": "claude", "role": "reader",
     "persona": "You read code and report. You do not edit.",
     "scope": {"does": ["read the auth module"], "does_not": ["edit files", "run the server"]}},
    {"name": "fixer", "harness": "claude", "role": "writer", "task_id": "T1",
     "binding": "tmux-pane", "claims": ["auth/*.py"]}
  ]
}
```

Rules: [../data-model.md](../data-model.md) "Team file".

### Output

Text: one line for each agent (`made`, `reused`, `running`, `started`, `not started: <reason>`), the
first-start note for each new Claude Code agent (D10), and the path of `ROOKERY.md`.

JSON:

```json
{"ok": true, "root": "/home/u/.rookery/projects/app", "session_id": "rs-1a2b3c4d",
 "agents": [{"name": "scout", "agent_id": "ag-0123456789", "role": "reader",
             "workspace": "/…/agents/auth-fix/scout", "worktree": null,
             "launch": "started", "binding": "tmux-pane:%12", "first_start_dialogs": true}],
 "errors": []}
```

`ok` is false if one or more agents did not start. The exit code is still 0 when the build succeeded,
because the workspaces exist and the report names each failure (FR-018). A bad team file gives exit 2 and
`{"ok": false, "errors": [{"index": 1, "name": "x..y", "problem": "…"}]}`.

## `rookery finish`

```text
rookery finish NAME [--session S] --message TEXT [--json]
```

It shows the change, asks three times (or reads `BROTUNDWASSER=1`), then commits, pushes and deletes the
worktree. It never deletes the branch ([../research.md](../research.md) D13). An answer other than `yes`
stops it with exit 2 and changes nothing.

## `rookery gc`

```text
rookery gc [--json]
```

It lists stale registry rows: path, session, agent, branch, reason (`session-ended`, `finished-path-exists`,
`path-missing`, `failed`, `pending-stale`). It deletes nothing and writes nothing.

## `rookery regen`

```text
rookery regen [--json]
```

It writes the generated views of the root again from the database (D15). It changes no row.

## Hooks in `_config/settings.json` (D8, D16)

The file is the output of `cli_init.merge_hooks(path, project, with_permissions=True)` plus two handlers:

| Event | Matcher | Command |
|---|---|---|
| `SessionStart` | (none) | `rookery hook session-start --project <project>` |
| `UserPromptSubmit` | (none) | `rookery hook prompt --project <project>` |
| `Stop` | (none) | `rookery hook stop --project <project>` |
| `PreToolUse` | `Write\|Edit\|NotebookEdit` | `rookery hook guard --project <project>` |
| `PostToolUse` | `Bash` | `rookery hook watch --project <project>` |

For a writer whose repo has Rookery handlers committed, the first three handler texts are copied from that
file. Else they are byte for byte the same as those of `rookery init`. So Claude Code runs each
once when a repo also has them (FR-031). A test compares the two outputs.

### `rookery hook guard`

Input: the PreToolUse JSON on stdin. It reads `tool_input.file_path` (or `notebook_path`).

- Target inside the agent's workspace or active worktree: no output, exit 0.
- Target outside them:

  ```json
  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
   "permissionDecisionReason": "Rookery guard: <path> is outside your folders (<workspace>, <worktree>). Write only in your own folders."}}
  ```

- Target inside the worktree and claimed by another agent: allow, with
  `"permissionDecisionReason": "Rookery claims: <agent> owns <pattern>. Tell that agent before you change it."`
  and a `turn-block` log line.
- No identity in the environment, or an internal error: no output, exit 0, and a log line. A guard error never
  blocks the agent.

### `rookery hook watch`

Input: the PostToolUse JSON on stdin. It reads `tool_input.command`. For each write target outside the
allowed folders, it writes a `watch-flag` line to the `turn-block` log and returns
`{"systemMessage": "Rookery watch: this command wrote <path>, outside your folders."}`. It always exits 0.

## Configuration (`rookery_config.default.json`, new keys)

```json
{
  "default_binding": "herdr-pane",
  "launch": {
    "herdr-pane": {
      "env_flag": "--env",
      "steps": [
        {"argv": ["herdr", "tab", "create", "--cwd", "{cwd}", "--label", "{label}", "--no-focus", "{env}"],
         "capture": "\"pane_id\"\\s*:\\s*\"([^\"]+)\""},
        {"argv": ["herdr", "pane", "run", "{pane}", "{cmd}"]}
      ]
    },
    "tmux-pane": {
      "env_flag": "-e",
      "steps": [
        {"argv": ["tmux", "new-window", "-d", "-P", "-F", "#{pane_id}", "-n", "{label}",
                  "-c", "{cwd}", "{env}", "{cmd}"],
         "capture": "^(%\\d+)$"}
      ]
    }
  },
  "alive": {
    "herdr-pane": ["herdr", "pane", "get", "{value}"],
    "tmux-pane": ["tmux", "display-message", "-p", "-t", "{value}", "#{pane_id}"]
  },
  "harness": {
    "claude": {"cmd": ["claude"], "settings_flag": "--settings",
               "prompt_file_flag": "--append-system-prompt-file", "add_dir_flag": "--add-dir"},
    "pi":  {"cmd": ["pi"],  "prompt_flag": "--append-system-prompt"},
    "omp": {"cmd": ["omp"], "prompt_flag": "--append-system-prompt"}
  },
  "ports": {"range": [47100, 47999], "names": ["api", "web"]}
}
```

- `{env}` expands to `env_flag K=V` for each variable of FR-008. `{cmd}` expands to the harness command
  and its flags (D11). A step whose `capture` does not match fails that agent's start, and the report gives the
  step output.
- Treat the herdr `capture` pattern as UNTESTED until the quickstart's live herdr check passes (D9).

## Generated files (FR-025 to FR-028, FR-033)

Each generated Markdown file starts with:

```text
<!-- generated by rookery <command> at <time>; do not edit -->
```

`ROOKERY.md`, `CONTEXT.md`, `persona.md` and `scope-card.md` pass `ste-lint --max-score 2.0
--glossary ste-glossary.txt`. A test renders each one from a fixture team file and runs `ste-lint` on it.
