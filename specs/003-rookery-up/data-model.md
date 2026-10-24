# Data model: `rookery up`

**Feature**: [spec.md](spec.md). **Decisions**: [research.md](research.md) D4, D6, D12, D16.

## Schema change (additive only)

Four new tables go in `rookery._SESSION_TABLES_SQL` with `CREATE TABLE IF NOT EXISTS`. No table or column
changes. `turn_block._schema_current` keeps its old table set, so that a hook never needs the write lock to
open a database (wave 2 audit). A new `turn_block.ensure_up_tables(conn)` adds the four tables to an old
database. Only the new commands (`up`, `finish`, `gc`, `regen`) and the guard hook call it. The quickstart runs `init_db` on a COPY of the live database and checks that each old row count
stays the same.

```sql
CREATE TABLE IF NOT EXISTS up_agents (
  agent_id     TEXT PRIMARY KEY,      -- identities.agent_id
  session_id   TEXT NOT NULL,
  harness      TEXT NOT NULL,         -- claude | pi | omp
  role         TEXT NOT NULL,         -- reader | writer
  repo         TEXT,                  -- absolute path of the host repo (writer)
  task_id      TEXT,                  -- writer branch part; NULL for a reader
  binding_kind TEXT NOT NULL,         -- a key of the launch configuration, e.g. herdr-pane
  updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worktrees (
  path       TEXT PRIMARY KEY,        -- absolute: <root>/wt/<session>/<name>/<repo>
  session_id TEXT NOT NULL,
  agent_id   TEXT NOT NULL,
  repo       TEXT NOT NULL,           -- absolute path of the host repo
  branch     TEXT NOT NULL,           -- agent/<session>/<name>/<task_id>
  state      TEXT NOT NULL DEFAULT 'pending',  -- pending | active | failed | finished
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS claims (
  session_id TEXT NOT NULL,
  agent_id   TEXT NOT NULL,
  pattern    TEXT NOT NULL,           -- a glob, relative to the repo top folder
  active     INTEGER NOT NULL DEFAULT 1,       -- 0 when a later team file drops the pattern
  updated_at TEXT NOT NULL,
  PRIMARY KEY(session_id, agent_id, pattern)
);
CREATE TABLE IF NOT EXISTS ports (
  agent_id TEXT NOT NULL,
  name     TEXT NOT NULL,             -- e.g. api, web
  port     INTEGER NOT NULL UNIQUE,
  PRIMARY KEY(agent_id, name)
);
```

Times are ISO 8601 text, as in `rookery_sessions`.

## Entities

### Team file (input, not stored)

The user's JSON file. [contracts/up.md](contracts/up.md) gives the full shape. Rules (FR-004, FR-009,
FR-019, FR-021):

| Field | Rule |
|---|---|
| `session` | Must be present. `safe_name`. Also a valid branch part when the team has a writer |
| `task`, `acceptance` | optional. Text and a list of text |
| `agents` | Must be present. A list with one or more entries |
| `agents[].name` | Must be present. `safe_name`. Unique in the file. A valid branch part for a writer |
| `agents[].harness` | Must be present. `claude`, `pi` or `omp`. `codex` fails: "Codex waits for R19". |
| `agents[].role` | Must be present. `reader` or `writer` |
| `agents[].repo` | writer only. Default the host repo. Must be a git top folder |
| `agents[].task_id` | Must be present for a writer. A valid branch part |
| `agents[].binding` | optional. A key of `launch`. Default the configuration's `default_binding` |
| `agents[].persona` | optional text |
| `agents[].scope` | optional. `{"does": [text], "does_not": [text]}` |
| `agents[].claims` | optional. A list of globs |

A team file that fails any rule changes nothing. The error lists each bad entry by its index and name.

### Project root

`<ROOKERY_ROOT>` or `~/.rookery/projects/<project>/`, absolute and outside every repo (D5). It is not stored.
`rookery up` writes only the generated views and the workspaces in it, and makes the worktrees under `wt/`.

### Session ownership

Sessions are global in the database. When `rookery up` makes a session, it adds the session binding
`rookery-root:<root>`. A later `rookery up` reuses the session only from the same root. The binding table
already exists, so this needs no schema change (wave 8 audit).

### Identity and its profile

An `identities` row (spec 001) plus one `up_agents` row. `rookery up` makes both in one transaction. A
second run updates `up_agents` and keeps the identity.

### Workspace

`<root>/agents/<session>/<name>/`. The folder is not stored: `turn_block.workspace()` gives the path.
Generated files and agent files are in [research.md](research.md) D7.

### Worktree registry row

One `worktrees` row for each worktree that Rookery made. It is the only proof that Rookery owns a path
under `wt/` (FR-021a).

```text
(none) --rookery up--> pending --git worktree add ok--> active --rookery finish (3x confirmed)--> finished
                          \--git worktree add fails--> failed --next rookery up--> pending
```

- `rookery up` adds the row as `pending` before it runs `git worktree add`. It sets `active` when git
  succeeds, and `failed` with the git error in the run report when git fails. No command deletes a
  registry row (constitution, Security: a row delete needs 3x confirmation).
- A later `rookery up` for the same agent sets a `failed` row back to `pending` and runs git again.
- A `pending` or `failed` row still proves ownership of its path for FR-021a, so a later run never takes
  that path for a human folder.
- `finish` changes `active` to `finished` only after commit, push and `git worktree remove` succeed.
- `gc` reads rows and writes none.

Stale (for `gc`): the session's state is `ended`; or the row is `finished` and the path exists; or the row is
`active` and the path is missing; or the row is `failed`; or the row is `pending` and older than one hour
(a run that stopped part way).

### Claim

A `claims` row: an agent owns the files that match a glob. The guard hook compares the absolute target of a
write with each other agent's active patterns, from the repo top folder. A match gives a warning, not a deny
(RQ-2.10). A second `rookery up` adds the team file's new patterns, sets `active = 1` on the patterns that stay,
and sets `active = 0` on the patterns that the team file dropped. No command deletes a claims row.

### Port map

`ports` rows for one agent. `rookery up` gives each agent one port for each name in `ports.names`, from
`ports.range`. A port is free if no row has it and a test bind on `127.0.0.1` succeeds. `ROOKERY_PORTS` is the
JSON object `{"<name>": <port>}`.

### Generated views

Files under the root that `rookery up` and `rookery regen` write from the database. Agents only read
them. No generator writes a row or marks mail read. [research.md](research.md) D15 lists them.

## Environment of a started agent (FR-008)

| Variable | Value |
|---|---|
| `ROOKERY_SESSION` | the session ID |
| `ROOKERY_NODE` | the agent ID (`ag-…`), the 0.7 identity only (FR-030) |
| `ROOKERY_ROOT` | the absolute project root |
| `ROOKERY_PROJECT_ROOT` | the absolute host repo top folder |
| `ROOKERY_PORTS` | JSON port map |
| `ROOKERY_DB` | passed through if the user set it |

`ROOKERY_NODE` holds the agent ID, not the name, so that an identity with the same name in another session
cannot match.
