# Quickstart: check `rookery up`

**Feature**: [spec.md](spec.md). Run each step from the repo root. Steps 1 to 4 are automatic. Steps 5 to 8
are live checks. Treat each live check as UNTESTED until a person or a session runs it and records the output.

## 1. The schema change on a COPY of the live database

```bash
cp ~/Documents/code/rookery/rookery.db /tmp/rookery-copy.db
python3 - <<'EOF'
import sqlite3, rookery
c = sqlite3.connect("/tmp/rookery-copy.db")
before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ("inbox", "rookery_sessions", "identities", "bindings")}
rookery.init_db(c)
after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
new = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert before == after, (before, after)
assert {"up_agents", "worktrees", "claims", "ports"} <= new
print("ok", after)
EOF
```

Expected: `ok` and the same counts. The live database does not change.

## 2. The tests

```bash
python3 -m unittest discover -p 'test_*.py'
```

Expected: every test passes. [plan.md](plan.md) lists the new tests in "Requirement trace".

## 3. A scratch team with fake panes

```bash
export HOME=$(mktemp -d) ROOKERY_DB=$HOME/r.db
git init -q $HOME/app && cd $HOME/app && echo "Rule: say BANANA." > CLAUDE.md \
  && git add . && git -c user.email=t@t -c user.name=t commit -qm init
cat > team.json <<'EOF'
{"session": "s1", "task": "demo", "agents": [
  {"name": "scout", "harness": "claude", "role": "reader", "binding": "tmux-pane"},
  {"name": "fixer", "harness": "claude", "role": "writer", "task_id": "T1", "binding": "tmux-pane"}]}
EOF
PATH=<repo>/fixtures/bin:$PATH rookery up team.json --json
git status --porcelain            # expected: only team.json (untracked, the user's own file)
ls $HOME/.rookery/projects/app    # ROOKERY.md _directory _teams _threads _mail agents wt
rookery up team.json --json       # second run: "reused", no new session
rookery gc --json                 # expected: []
```

## 4. Lint the plan files and the generated files

```bash
for f in specs/003-rookery-up/*.md specs/003-rookery-up/contracts/*.md; do
  ste-lint "$f" --max-score 2.0 --glossary ste-glossary.txt | tail -1; done
```

A test checks the generated files (FR-033).

## 5. Live: herdr launch and capture (D9)

In a herdr session, with a scratch team of one reader and `"binding": "herdr-pane"`:

```bash
rookery up team.json --json
```

Expected: one new herdr tab. The JSON shows `"binding": "herdr-pane:<id>"`, and `herdr pane get <id>` succeeds.
If the capture fails, record the `tab create` output and fix the `capture` pattern in the configuration.

## 6. Live: a Claude Code reader quotes a host rule (R11, SC-009)

In the pane of step 5: accept the trust dialog and the import dialog (D10). Ask: "What does the project rule
say?" Expected: the agent answers BANANA.

## 7. Live: two agents send mail through the gate (SC-005)

With one reader and one writer up, ask the reader to send the writer one mail with a `rookery` block. Expected:
the writer's pane gets a wake. `rookery peek` in the writer's pane shows the mail. `~/.rookery/logs/gate.jsonl`
has the decision (D2).

## 7a. Live: a committed hook runs once (FR-031)

Use a scratch repo where `rookery init` wrote the hooks and the hooks are committed. Bring up one writer in that
repo and one reader. Ask the writer to send the reader one mail with a `rookery` block. Expected: the reader's
inbox has exactly one new row, and `~/.rookery/logs/gate.jsonl` has exactly one decision for it.

## 8. Live: the guard (R9)

Ask the writer to write `/tmp/outside.txt` with its Write tool. Expected: Claude Code shows the Rookery guard
deny. Ask it to run `echo x > /tmp/outside.txt` in Bash. Expected: a `Rookery watch` message, and a
`watch-flag` line in `<root>/logs/turn-block.jsonl`.

## 9. Deploy (the user's decision)

After merge: fast-forward `~/Documents/code/rookery`, rebuild the zipapp with `bootstrap.build_pyz`, install
`skills/rookery/SKILL.md` into `~/.claude/skills/rookery/`, and save a copy of the live database first. `rookery up`
starts no daemon, so no service restarts.

## Live check results (2026-10-24)

The checks ran in a separate herdr workspace with a scratch repo, database and root. A shim put this branch's
`rookery` on the pane `PATH`. Claude Code 2.1.309 ran in each pane.

| Check | Result | Evidence |
|---|---|---|
| 5. herdr launch and capture (D9) | pass | `rookery up` bound `herdr-pane:w5:p2` and `herdr-pane:w5:p3`. `herdr pane get` found both. The `join_cmd` text reached the pane shell intact. |
| D10 first-start dialogs | pass | Each new pane showed the trust dialog. The reader also showed the external import dialog for the host `CLAUDE.md`. |
| 6. A reader quotes a host rule (R11, SC-009) | pass | The reader answered BANANA in 6 s and read no file. |
| 7. Two agents send mail (SC-005) | pass | The block path stored row 1 (scout to fixer). The postmaster pushed a wake to fixer. fixer read the mail, marked it read, and its reply stored row 2. The gate log in `~/.rookery/logs` has both decisions (D2). |
| 7a. A committed hook runs once (FR-031) | pass | The committed and the generated Stop handler have the same text. One turn of fixer gave 1 row, 1 gate decision and 1 Stop event. |
| 8. Guard and watch (R9) | pass | The Write tool to the host repo got the Rookery guard deny, and the file does not exist. The Bash write gave the agent the `Rookery watch` message and a `watch-flag` log line. |
