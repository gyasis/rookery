# Feature Specification: `rookery up` builds the Rookery project workspace

**Feature Branch**: `003-rookery-up`.
**Created**: 2026-10-23.
**Status**: Draft.
**Input**: User description: "rookery up builds the Rookery project workspace (phase 2)." The full input is
`SPEC_INPUT_rookery-up.md`. It holds the user's decisions of 2026-10-23 and an architecture review of the code on
`master` (b01916f). The input file is not committed, so this spec copies what it needs.

**Built from (read, not re-derived)**: the OKF bundle `~/Documents/code/inter-agent-comms/okf/`:
`design/folder-layout.md` (main), `design/icm-rookery-workspace.md`, `design/rookery-sessions.md`,
`design/harness-contract.md`, `requirements/rookery-0-7.md` (phase 2), `risks/register.md` (R8 to R14,
R19), `roles/boss.md`. Specs 001 and 002. Constitution v1.2.1. The PRD is
`~/dev/prd/scratch/rookery_peer_mesh_2026-10-15.md`. Its section 2 text (`agents/<node>/`, `wt/<node>/`,
`ROOKERY_TEAM`) is out of date. This spec follows the OKF design and the code.

**What this feature is**: phase 2 of Rookery 0.7. One command, `rookery up`, configures a team of agents for a
project. It builds the Rookery project root and makes one workspace for each agent. It registers each agent in a
Rookery session, gives code-writing agents their own git worktree, and starts each agent in its own pane.
The user calls this one of the most important parts of Rookery.

**Why now**: specs 001 and 002 built sessions, identities, the turn block, the gate and the postmaster. A
user still builds each workspace, identity and pane by hand. The review of 2026-10-23 found places where the
built code conflicts with the phase 2 design. This spec settles three of them (D1 to D3) and keeps four open
(Q1 to Q4) for clarify, research and plan.

### Done before this feature (evidence: `master` b01916f)

| Item | Where | State |
|---|---|---|
| Default root `~/.rookery/projects/<project>/`, outside the repo | `turn_block.py:47-52`, `cli_init.py:548` (001 FR-023) | done (RQ-2.1 in part) |
| Managed section in the host CLAUDE.md / AGENTS.md | `cli_init.py` (001) | done (RQ-2.1 in part) |
| `agents/<session>/<name>/` with `runs/<TASK-ID>/HANDOFF.md`, `outbox/`, `state/` | `turn_block.py:57-62` | done (RQ-2.4 in part) |
| The code reads `ROOKERY_SESSION`, `ROOKERY_NODE`, `ROOKERY_ROOT` | `rookery_identity.py`, `turn_block.py` | done (RQ-2.8 in part) |
| Sessions, identities, bindings (pulled forward from phase 2) | 001 FR-025 to FR-036 | done |
| Resume from the newest HANDOFF.md; outbox watcher | RQ-1.12, RQ-1.9 | done |
| R8: two agents never share one identity | `test_identity.test_two_panes_get_two_identities` | proven |
| One gate for every send; reads never mark mail read; notices with no body | 002 | done |
| A local mailroom needs no token | PR #14 | done |

### Obsolete items

- `ROOKERY_TEAM`. `ROOKERY_SESSION` replaces it.
- The PRD's `agents/<node>/` and `wt/<node>/`. The paths have a session level.
- The sketch in `icm-rookery-workspace.md` lines 26-32 (no session level). `folder-layout.md` supersedes it.
- `rookery-sessions.md:68` "rookery inbox (read and mark read)". After 002, `rookery ack` marks mail read.
- RQ-2.7 "identity via flags". Identity comes from the environment. Launch flags carry only instructions.

## Clarifications

### Session 2026-10-24

- Q: Do the agents that `rookery up` starts use MCP or the CLI only (Q2, and so Q1)? → A: CLI only for these
  agents. The MCP server and the global `~/.claude.json` entry stay as they are for other sessions.
- Q: How do the Rookery hooks reach a workspace or a worktree (Q3)? → A: `rookery up` generates
  `_config/settings.json` for each agent and passes it with `--settings` at launch.
- Q: How does an agent's persona and scope card persist into a new session (Q4)? → A: the team file is the
  source. `rookery up` regenerates `_config/` from it in each session. Agents do not edit `_config/`.
- Q: What may the `_threads/` mirror hold (FR-026)? → A: metadata only (thread, sender, recipient, topic,
  time, state). No bodies. Bodies stay behind the scoped reads of 002 D12.
- Q: How does Rookery tell its worktrees from human worktrees under `wt/`? → A: the worktree registry is the
  only proof of ownership. Rookery acts only on worktrees it registered, and refuses a session name whose
  `wt/<session>/` exists but is not in the registry.

## Decisions

The user made these decisions (2026-10-23) and locked them.

- **D1. The old `rookery up` becomes `rookery join`.** Today `rookery up` (`rookery_cli.py:76`, `cli_up.py`)
  brings a peer node online through a remote mailroom. It does discovery, pins the host key (TOFU) and merges
  an entry into `~/.claude.json`. This feature renames that command `rookery join`. The workspace command of
  this feature takes the name `rookery up`. Docs, tests, the zipapp build and the agent briefings follow the
  rename.
- **D2. The gate log has one fixed home: `~/.rookery/logs`.** Today `gate_root()` (`turn_block.py:93-95`)
  uses `ROOKERY_ROOT`, else `~/.rookery`. This feature sets `ROOKERY_ROOT` for each project. If the gate log
  followed it, the one gate log of 002 D15 would split into one log for each project. The gate log stays in
  one file under `~/.rookery/logs`, whatever `ROOKERY_ROOT` holds.
- **D3. `rookery up` always sets `ROOKERY_ROOT` for each agent, and the code reads it first.** Today the code
  takes the project from the name of the current folder (`cli_rookery_session.py:111,244,274`,
  `cli_hook.py:623`, `postmaster.py:676`). In a workspace or a worktree, that name is the agent or the repo,
  not the project. Each of these paths uses `ROOKERY_ROOT` when it has a value.

## Open questions

The user left these open on purpose. `/speckit-clarify`, research and plan must settle them. FR-030 to
FR-032 carry them. Clarify settled Q1 and Q2 on 2026-10-24 (see Clarifications).

- **Q1 (settled by Q2). `ROOKERY_NODE` has two meanings.** It is the 0.7 identity (`rookery_identity.py:443`). It is also the
  legacy MCP node ID (`rookery_mcp.py:45`). The old `rookery up` (now `join`) writes one fixed value into the
  global `~/.claude.json` MCP entry (`cli_up.py:200`). A value set for each agent could clash with it. No test
  shows which value wins. Spec 002 left the split out of scope. Q1 depends on Q2.
- **Q2 (settled: CLI only). Do agents need MCP at all? The user leans to CLI only.** The CLI (`rookery send`, `inbox`, `peek`,
  `ack`) and the turn block may be enough for agents. Research must compare the two. What does MCP give that
  the CLI does not? What breaks if the agents that `rookery up` starts use the CLI only? What does each
  harness need (Claude Code; Codex, R19)?
- **Q3 (settled: generated `_config/settings.json` with `--settings`). The Rookery hooks do not reach a
  workspace.** `rookery init` writes the hooks into the host repo's
  `.claude/settings.json` (`cli_init.py:394`). An agent whose current folder is its workspace does not load
  them. A worktree has them only if the team commits them. With no Stop hook, the agent sends no mail. The design
  names `_config/settings.json` and a `--settings` flag (`folder-layout.md:27,38`).
- **Q4 (settled: the team file is the source of `_config/`). "Workspace = identity" is not true in the
  code.** Identity is a database row
  (`rookery_identity.py:405-470`). A new session gives a new, empty workspace (001 D6). `--from` copies only
  `HANDOFF.md` (001 D21). So `_config/` (persona and scope card) is lost each session. The ICM design says
  `_config/` persists across tasks (`icm-rookery-workspace.md:44`).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One command builds a team of reader agents (Priority: P1)

The user has a project repo. The user writes a short team file that names the agents: a name, a harness, and
a role (reader or writer). The user runs `rookery up`. Rookery makes the project root, starts a Rookery
session, makes one workspace and one identity for each agent, and writes each agent's generated files. The
host repo does not change.

**Why this priority**: this is the core of phase 2. Today the user builds each piece by hand.

**Independent Test**: in a scratch repo, run `rookery up` with a team file of two readers. Check that the
root and two workspaces exist. Check that `rookery identity list` shows two identities in one session. Check that
`git status` in the host repo is clean.

**Acceptance Scenarios**:

1. **Given** a repo with no Rookery root, **When** the user runs `rookery up` with a two-reader
   team file, **Then** Rookery makes `~/.rookery/projects/<project>/`, a new session and two identities.
   Each identity gets `agents/<session>/<name>/` with its generated files.
2. **Given** the same team, **When** the user runs `rookery up` again, **Then** Rookery makes no second
   session and no duplicate identities. It regenerates the generated files and does not change files that
   agents wrote (`runs/`, `outbox/`, `HANDOFF.md`).
3. **Given** a team file with a bad agent name, **When** the user runs `rookery up`, **Then** Rookery
   changes nothing, exits non-zero, and names the bad entry.
4. **Given** any `rookery up` run, **When** it ends, **Then** `git status` in the host repo is the same as
   before the run (R10).

---

### User Story 2 - Each agent runs in its own pane and can send and get mail (Priority: P1)

After `rookery up` builds the workspaces, it runs each agent in its own pane. Each agent runs in the right
folder, with its own environment and its own launch flags. The agent knows who it is. It can read its mail
and send mail through the gate. The postmaster can wake it.

**Why this priority**: a workspace that no agent runs in gives no value. Mail is the reason Rookery exists.

**Independent Test**: with a fake launch command that records its flags and environment, run `rookery up`
for two agents. Check each recorded run: its folder, its `ROOKERY_*` variables (absolute paths) and its
flags. Then, in each started agent's environment, run `rookery identity show` and send one mail through the
gate.

**Acceptance Scenarios**:

1. **Given** a team of two agents, **When** `rookery up` runs them, **Then** each one runs with the launch
   command that the configuration gives for that binding kind. Rookery has no code for one terminal tool
   (constitution IX).
2. **Given** a started agent, **When** it runs `rookery identity show`, **Then** it sees its own name and
   session. The root is the project root, not its current folder (D3).
3. **Given** a started agent, **When** its turn ends with a `rookery` block, **Then** the mail goes through
   the gate. The gate delivers a mail to its teammate. The hooks reach the agent through its
   `_config/settings.json` (FR-031).
4. **Given** a started agent, **When** mail arrives for it and it is idle, **Then** the postmaster wakes
   it in its own pane.

---

### User Story 3 - Existing commands keep working after the rename (Priority: P1)

A user who joined a peer with the old `rookery up` now runs `rookery join`. Every gate decision still goes to
one log file. Commands that worked from a repo folder still find the right project.

**Why this priority**: the user asked that this feature not break what exists.

**Independent Test**: run the existing peer tests against `rookery join`. Start two agents in two projects
and check that both gate decisions land in the one file under `~/.rookery/logs`. Run the session commands
from a workspace folder and check that they find the project root from `ROOKERY_ROOT`.

**Acceptance Scenarios**:

1. **Given** the old peer-join flow, **When** the user runs `rookery join` with the same flags, **Then**
   it behaves as the old `rookery up` did (D1).
2. **Given** `ROOKERY_ROOT` set to a project root, **When** the gate handles any send, **Then** the decision goes
   to the one gate log under `~/.rookery/logs` (D2).
3. **Given** `ROOKERY_ROOT` set and the current folder is a workspace, **When** a session command, hook
   or postmaster needs the project, **Then** it uses `ROOKERY_ROOT`. It does not use the name of the current folder
   (D3).
4. **Given** `ROOKERY_ROOT` not set, **When** the user runs a command from the repo folder, **Then** it
   behaves as before this feature.

---

### User Story 4 - Code-writing agents get their own worktree (Priority: P2)

A writer agent gets its own git worktree of the host repo, outside the repo, on its own branch. When the work
ends, the user runs `rookery finish` for that agent. When a team is gone, `rookery gc` lists worktrees that
nobody uses, for the user to confirm.

**Why this priority**: writers need their own copy of the code, so that two agents do not edit one working
tree. Readers (P1) give value without it.

**Independent Test**: run `rookery up` with one writer. Inspect the worktree path, its branch name, and that the
host repo's `git status` and test count do not change. Run `rookery finish` and `rookery gc` on a scratch repo.

**Acceptance Scenarios**:

1. **Given** a writer in the team file, **When** `rookery up` runs, **Then** Rookery makes
   `wt/<session>/<name>/<repo>/` on branch `agent/<session>/<name>/<TASK-ID>`. The writer starts in it.
2. **Given** a writer worktree, **When** the user runs the human's test collector in the host repo,
   **Then** the count is the same as before (R10).
3. **Given** a finished writer, **When** the user runs `rookery finish <name>`, **Then** Rookery shows the
   change for human review. Only after the human confirms does it commit, push and delete the worktree, and
   update the registry.
4. **Given** worktrees of an ended session, **When** the user runs `rookery gc`, **Then** Rookery lists
   them and deletes nothing. It never deletes a branch (R14).

---

### User Story 5 - Agents can find each other and the team (Priority: P2)

A new agent with no memory reads one file. It learns who is on the team, where each agent runs, what each one
does, and what the team must deliver. Rookery generates these files from the database. Agents only read them.

**Why this priority**: an agent that knows the roster asks the right peer and does not ask the user.

**Independent Test**: run `rookery up`, then read `ROOKERY.md` and `_directory/INDEX.md`. Change the roster
(add an agent) and run `rookery up` again. Check that the generated files change to match.

**Acceptance Scenarios**:

1. **Given** a team, **When** `rookery up` runs, **Then** the root holds `ROOKERY.md` and `_directory/INDEX.md`. No
   harness loads `ROOKERY.md` as a context file, because of its name. The index lists each agent's name, harness, pane,
   status, role and team.
2. **Given** a team file with a task and acceptance criteria, **When** `rookery up` runs, **Then** it writes
   `_teams/<session>.md` with the task, the criteria and the members (RQ-R.4).
3. **Given** quarantined mail in the database, **When** the generator runs, **Then** it writes the mirror
   under `_mail/quarantine/<reason>/` and `_mail/review/`. The database stays the source (C5).
4. **Given** the generator runs, **When** it reads mail, **Then** it marks nothing read (002 D11).

---

### User Story 6 - Reader agents keep the host project rules (Priority: P2)

A reader agent runs in its workspace, not in the host repo. It still follows the host project's own rules.

**Why this priority**: an agent that loses the project rules does wrong work (R11).

**Independent Test**: put a rule that only the host CLAUDE.md has into a scratch repo. Start a reader with
`rookery up`. Check that the reader's generated context file imports the host file by absolute path.

**Acceptance Scenarios**:

1. **Given** a host CLAUDE.md, **When** `rookery up` writes a reader's CLAUDE.md, **Then** the file imports
   the host file with `@<absolute path>`.
2. **Given** a harness that reads AGENTS.md, **When** `rookery up` writes its files, **Then** AGENTS.md
   reaches the host rules through a symlink fallback.

---

### User Story 7 - Guardrails stop accidents (Priority: P3)

Each agent writes only in its own folders. Two writers do not edit the same file without a warning. Two dev
servers do not take the same port. Under 002 D24, these are guardrails against accidents, not security: any
local program can still write anywhere.

**Why this priority**: accidents waste work, but they do not lose mail. Readers and writers work without
these guardrails.

**Independent Test**: with the hook, try a Write outside the agent's folders and check that the hook denies it.
Write a file that another agent owns and check the warning. Start two dev servers from two writers with their
port maps and check that both bind.

**Acceptance Scenarios**:

1. **Given** a Claude Code agent, **When** it tries Write, Edit or NotebookEdit outside its workspace and
   worktree, **Then** the hook denies it (R9).
2. **Given** a Bash write outside its folders, **When** the watcher runs, **Then** it flags the write.
3. **Given** a file that the claims registry gives to agent A, **When** agent B writes it, **Then** B gets
   a warning (R12).
4. **Given** two writers, **When** each starts a dev server from its port map, **Then** both bind (R13).

---

### Edge Cases

- The project root exists from spec 001 (made by `rookery init`), with agents that the user made by hand.
  `rookery up` adds to it and does not break those agents.
- Two agents in the team file have the same name. Rookery refuses the file.
- An agent name is not valid as a part of a git branch name (for example `a..b` or `x.lock`).
  `safe_name` allows it today. Rookery must refuse such a name for a writer.
- A session name is the same as a folder that a human made under `wt/`. Today
  `~/.rookery/projects/rookery/wt/` holds human feature worktrees. Rookery refuses that session name, because
  the folder is not in the worktree registry (FR-021a). It never takes over or lists a folder it did not
  register.
- The launch command for a binding kind is missing from the configuration. Rookery builds the workspaces,
  runs no agent, and says which kind has no launch command.
- One agent does not come up (the pane tool is not running). Rookery reports which agent did not come up and keeps the
  others.
- `rookery up` runs while the agents of the session are still running. Rookery does not bring up a second copy
  of an agent that runs.
- A writer's worktree has the Rookery hooks committed, and `--settings` adds them again. One turn still
  sends each mail once (FR-031).
- The host repo has no CLAUDE.md. The reader's generated file has no import, and Rookery says so.
- A writer's branch exists from an earlier run. Rookery reuses it and does not reset it.
- The user runs `rookery up` inside a worktree of the host repo. The project is still the host project.

## Requirements *(mandatory)*

**Phase requirements covered**: RQ-2.1, RQ-2.2, RQ-2.3, RQ-2.4, RQ-2.5, RQ-2.6, RQ-2.7, RQ-2.8, RQ-2.9.
Also RQ-2.10, RQ-2.11, RQ-2.12, RQ-2.13, RQ-2.14, RQ-2.15, RQ-2.16, RQ-2.17 (all 17). Also RQ-R.3 and RQ-R.4.
Conflicts C5, C8 and C9 apply here. Risks R9 to R14 need their proof tests here. R8 is proven (phase 1).
R19 (Codex) gates the Codex launch.

### Functional Requirements

**Decisions D1 to D3 (keep the built code working)**

- **FR-001**: The command that brings a peer node online through a remote mailroom MUST have the name
  `rookery join`. It MUST keep the same flags and behavior as the old `rookery up` (D1). Docs, tests, the zipapp
  and the agent briefings MUST name `rookery join`.
- **FR-002**: The gate log MUST stay one file under `~/.rookery/logs`, whatever `ROOKERY_ROOT` holds (D2).
- **FR-003**: Every code path that finds the project root MUST use `ROOKERY_ROOT` when it has a value. In that case it MUST
  NOT take the project from the name of the current folder (D3). With `ROOKERY_ROOT` not set,
  the behavior MUST be the same as before this feature.

**The project root and the team (RQ-2.1, RQ-2.2, RQ-2.8, RQ-R.4)**

- **FR-004**: `rookery up` MUST read a team file that names each agent: a name, a harness, a role (reader or
  writer) and, for a writer, the repo. The team file MAY give a task, acceptance criteria, a persona and a
  scope for each agent.
- **FR-005**: `rookery up` MUST make the project root at `~/.rookery/projects/<project>/` (or the root that
  `ROOKERY_ROOT` gives), outside every repo. It MUST NOT change the host repo, apart from the managed section
  that `rookery init` owns (RQ-2.1, R10).
- **FR-006**: `rookery up` MUST open a Rookery session for the team, or reuse the session that the team
  file or the root names. It MUST make one identity for each agent in that session, and MUST NOT make a
  duplicate on a second run.
- **FR-007**: `rookery up` MUST write `ROOKERY.md` at the root, as the L0 routing file. It MUST NOT name it
  CLAUDE.md or AGENTS.md (RQ-2.2, C9).
- **FR-008**: Each agent MUST run with `ROOKERY_SESSION`, `ROOKERY_NODE`, `ROOKERY_ROOT`,
  `ROOKERY_PROJECT_ROOT` and `ROOKERY_PORTS` set, each path absolute (RQ-2.8). For these agents,
  `ROOKERY_NODE` has one meaning: the 0.7 identity (FR-030).
- **FR-009**: `rookery up` MUST inspect the whole team file before it changes anything. On a bad file it MUST
  change nothing, exit non-zero and name each bad entry.
- **FR-010**: A second run of `rookery up` for the same team MUST regenerate the generated files and MUST NOT
  change files that agents write (`runs/`, `outbox/`, `HANDOFF.md`).

**Workspaces (RQ-2.4, RQ-2.5, RQ-R.3)**

- **FR-011**: Each agent MUST get `agents/<session>/<name>/` with a generated CLAUDE.md and AGENTS.md,
  `CONTEXT.md`, `_config/` (persona, scope card, `settings.json`), `runs/<TASK-ID>/` with the stage folders
  `01_intake`, `02_work` and `03_report`, and `outbox/` (RQ-2.4). `_config/` is a generated view of the
  team file (FR-032).
- **FR-012**: A reader's generated CLAUDE.md MUST import the host CLAUDE.md with `@<absolute path>`. AGENTS.md
  MUST reach the host rules through a symlink fallback (RQ-2.5, R11).
- **FR-013**: A worker persona MUST state its scope in negative terms and MUST list only its own team's
  scope cards (RQ-R.3).

**Launch (RQ-2.7, RQ-2.14, C8)**

- **FR-014**: `rookery up` MUST run each agent with the launch command that the configuration gives for
  that agent's binding kind. Rookery MUST NOT hold code for one terminal tool (constitution IX). The
  default configuration MAY give herdr and tmux commands, as `wake` does today.
- **FR-015**: A reader MUST run with its workspace as its current folder. A writer MUST run with its
  worktree as its current folder (RQ-2.7).
- **FR-016**: A writer MUST get its instructions through launch flags: `--append-system-prompt-file` and
  `--settings` for Claude Code, `--append-system-prompt` for pi and omp (RQ-2.7, C8). Its identity MUST
  come from the environment (FR-008), not from the flags.
- **FR-017**: `rookery up` MUST bind each started agent to its pane, so that the postmaster can wake it.
- **FR-018**: `rookery up` MUST NOT run a second copy of an agent that already runs. It MUST report each agent that
  did not come up, with the reason, and MUST keep the agents that did.
- **FR-019**: `rookery up` MUST NOT run a Codex agent until R19 is proven (Codex identity, AGENTS.md
  imports and hook events, checked against Codex's own loader).

**Worktrees, finish and gc (RQ-2.6, RQ-2.12, RQ-2.13, RQ-2.15)**

- **FR-020**: A writer MUST get a git worktree at `wt/<session>/<name>/<repo>/` on branch
  `agent/<session>/<name>/<TASK-ID>`. `rookery up` MUST reuse an existing branch and MUST NOT reset it
  (RQ-2.6).
- **FR-021**: `rookery up` MUST refuse a writer name or session name that is not a valid git branch part.
- **FR-021a**: Rookery MUST keep a worktree registry, and the registry MUST be the only proof that Rookery
  made a worktree. `rookery up` MUST refuse a session name whose `wt/<session>/` folder exists but is not in
  the registry. `rookery finish` and `rookery gc` MUST act only on worktrees in the registry.
- **FR-022**: `rookery finish <name>` MUST show the change for human review. Only after the human confirms
  MUST it commit, push, delete the worktree and update the registry (RQ-2.12).
- **FR-023**: `rookery gc` MUST list stale worktrees that Rookery made. It MUST NOT delete a worktree without
  the human's confirmation. It MUST NEVER delete a branch. It MUST NOT list or touch a folder that is not in
  the worktree registry (FR-021a, RQ-2.13, R14).
- **FR-024**: Rookery MUST NOT use in-repo worktrees, symlinked `node_modules`, OverlayFS, a custom shell or
  `LD_PRELOAD`, or a hand-set `GIT_DIR` or `GIT_WORK_TREE` (RQ-2.15).

**Generated files (RQ-2.3, RQ-2.16)**

- **FR-025**: Rookery MUST generate `_directory/INDEX.md` (name, harness, pane, status, current task, team),
  the port map and the claims, plus `_teams/` and `_threads/`, from the database. Agents MUST only read these
  files (RQ-2.3).
- **FR-026**: The `_threads/` mirror MUST hold metadata only: thread, sender, recipient, topic, time and
  state. It MUST NOT hold a message body. An agent reads a body only through the scoped reads of 002 D12
  (`rookery peek`, `rookery inbox`). A test MUST show that no body text appears under `_threads/`.
- **FR-027**: Rookery MUST generate `_mail/quarantine/<reason>/` and `_mail/review/` from the database. The
  database MUST stay the source (RQ-2.16, C5).
- **FR-028**: Each generator MUST read the database directly and MUST NOT mark mail read (002 D11).

**Guardrails (RQ-2.9, RQ-2.10, RQ-2.11)**

- **FR-029**: Rookery MUST give these guardrails, as guards against accidents under 002 D24 (not as
  security):
  - a Claude Code PreToolUse hook that denies Write, Edit and NotebookEdit outside the agent's workspace and
    worktree. A watcher flags Bash writes outside them (RQ-2.9, R9);
  - a claims registry, where the boss gives file ownership, and a warning when an agent writes a file that
    another agent owns (RQ-2.10, R12);
  - a port map for each agent from the port registry, given in `ROOKERY_PORTS` (RQ-2.11, R13).
  The spec of 002 (D24) points to RQ-2.9 for stronger local controls. This feature MUST correct that
  pointer: RQ-2.9 is a guardrail.

**Open questions (settle in clarify, research and plan)**

- **FR-030**: Agents that `rookery up` runs MUST read and send mail through the CLI only (`rookery send`,
  `inbox`, `peek`, `ack`, `identity list`) and the turn block. Their generated briefing and context files
  MUST NOT name an MCP tool. `rookery up` MUST NOT write or change an MCP entry, and MUST NOT change the global
  `~/.claude.json`. The MCP server stays as it is for other sessions (Q1, Q2). A test MUST show that a mail
  sent by such an agent carries its 0.7 identity, not the fixed node of the global MCP entry.
- **FR-031**: Every Claude Code agent that `rookery up` runs MUST run the Rookery hooks, so that its Stop
  hook sends its mail. `rookery up` MUST generate `_config/settings.json` for each agent, with the Rookery
  hooks at absolute paths, and MUST pass it with `--settings` at launch (Q3). It MUST NOT write hooks into the
  host repo or into `~/.claude/settings.json`. A test MUST show that one turn sends its mail once when the
  worktree's repo also has the hooks committed.
- **FR-032**: An agent's persona and scope card MUST persist when the agent moves to a new session. The team
  file MUST be their source. `rookery up` MUST regenerate `_config/` (persona, scope card, `settings.json`)
  from the team file in each session, and agents MUST only read `_config/` (Q4). A test MUST show that the
  same team file gives the same `_config/` in a new session.

**Prose and docs (RQ-2.17)**

- **FR-033**: The spec, plan, tasks, docs and the generated agent files (`ROOKERY.md`, `CONTEXT.md`, persona,
  scope cards) MUST pass `ste-lint FILE --max-score 2.0 --glossary ste-glossary.txt` (RQ-2.17).
- **FR-034**: The folder layout in the docs MUST show the folders that the code writes:
  `postmaster/outbox/`, `postmaster/pushed/`, `logs/` and `agents/<session>/<name>/state/`.

### Key Entities

- **Team file**: the user's input to `rookery up`. It names each agent (name, harness, role, repo for a
  writer) and may give the task, the acceptance criteria, a persona and a scope. It is the source of each
  agent's `_config/`.
- **Project root**: `~/.rookery/projects/<project>/`. It holds `ROOKERY.md`, the generated folders, the
  workspaces and the worktrees.
- **Workspace**: `agents/<session>/<name>/`. One for each identity. It holds the agent's generated files,
  `_config/` (generated from the team file), `runs/` and `outbox/`.
- **Worktree**: `wt/<session>/<name>/<repo>/`. One for each writer, on branch
  `agent/<session>/<name>/<TASK-ID>`.
- **Launch command**: a configured command for each binding kind. It brings up one agent in one pane with its
  folder, environment and flags.
- **Generated views**: `ROOKERY.md`, `_directory/`, `_teams/`, `_threads/` (metadata only), `_mail/`. Rookery writes them
  from the database. Agents read them.
- **Worktree registry**: the record of each worktree that Rookery made (path, session, agent, branch). It is
  the only proof of ownership for `rookery finish` and `rookery gc`.
- **Guardrail data**: the claims registry and the port map.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user builds a team of three agents (two readers, one writer) with one command and one team
  file. Before this feature it took one manual step for each workspace, identity, worktree and pane.
- **SC-002**: After any `rookery up`, `git status` in the host repo shows no change, and the host's test
  collector finds the same number of tests as before (R10).
- **SC-003**: A second `rookery up` for the same team makes 0 new sessions, 0 duplicate identities and 0
  changes to files that agents wrote.
- **SC-004**: In 100% of starts, each agent sees its own name and session, and its project root is the
  Rookery project root.
- **SC-005**: Each started agent sends one mail to a teammate, and the teammate gets it, in the
  end-to-end test.
- **SC-006**: Every gate decision of agents in two projects lands in one gate log.
- **SC-007**: Every existing test of the old peer-join flow passes against `rookery join`.
- **SC-008**: `rookery gc` deletes 0 branches in every test, and it deletes no worktree without the
  confirmation.
- **SC-009**: A reader agent quotes a rule that only the host CLAUDE.md holds (R11).
- **SC-010**: Each of R9 to R14 has a proof test that passes, and the risk register says `proven` for each.

## Assumptions

- The team file format (for example YAML or Markdown with front matter) is a plan decision. The spec fixes
  only what the file must say (FR-004).
- Claude Code is the first harness. pi and omp follow the design's flags. Codex waits for R19 (FR-019).
- herdr and tmux are the panes in use. The default configuration gives a launch command for each, as `wake`
  does. Other tools need only a configuration entry.
- The postmaster runs as today. This feature does not change how it wakes an agent; it only adds bindings.
- A local mailroom needs no token (PR #14). `rookery up` brings up no mailroom.
- The boss role (RQ-R.4) is the user's team file in this feature. An agent that builds teams by skill is a
  later feature.
- Community folders and provenance naming stay deferred (`requirements/rookery-0-7.md`, Deferred).
- `rk` stays retired.
