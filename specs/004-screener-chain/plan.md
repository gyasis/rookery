# Implementation Plan: the screener chain and the broker gates

**Branch**: `004-screener-chain` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/004-screener-chain/spec.md` (decisions D1 to D5, five clarifications
of 2026-10-24 and 2026-10-25)

## Summary

The gate stores each mail as it does today. It now also runs the new T0 checks in the same transaction: the
injection pattern set, duplicate asks, the ask budget, closed threads (B3) and the worker-gate anomalies. These
checks need no model (D9). A new process, `rookery screen run`, reads the stored mail and builds a short envelope
for each one. It asks tev1:4b one closed question over four labels. It sends a review-band answer to
`hunch --vote 6`, and gives escalated mail to a configured T2 command (D12, D15). The screener uses PromptChain
when it imports, else a standard-library copy with the same interface (D6, the user's choice).

Every new check starts in shadow mode. It records what it would do and changes no status (D4). The user compares
the decisions with `rookery screen mark` and with the manual release and quarantine commands. The user switches a
model check to live mode only after 150 compared decisions (D20). In live mode, a failed model call holds the
mail as `escalated` (fail closed). The postmaster never delivers an escalated mail (D14). Threads get a table and
a status, and a closed thread denies later mail (D16). A session with no name gets a name of three words (D22).

## Technical Context

**Language/Version**: Python 3.10+. Miniconda 3.13 and `/usr/bin/python3` 3.10 both run the screener (D6).
**Primary Dependencies**: standard library and `sqlite3`. PromptChain (`promptchain.utils.decision_step`) is an
optional import, used only in the screener process (D6). `hunch` is an external program. The T2 model comes in
through configuration only.
**Storage**: the mailroom SQLite database. One new inbox column (`thread`), one new status (`escalated`), and six
new tables: `session_context`, `agent_scope`, `threads`, `screen_mail`, `screen_decisions`, `screen_calls`
([data-model.md](data-model.md)).
**Testing**: `unittest` files. Each test uses a temporary `HOME`, `ROOKERY_DB` and `ROOKERY_ROOT`
(`test_hooks.Base`). Model tests use the `fake` backend (`ROOKERY_SCREENER_FAKE`), which counts its calls. The
backend contract test uses a local `http.server` lane and a fake `hunch` program in `fixtures/bin`. No test calls
a model (FR-036).
**Target Platform**: Linux, one user. The model lane is on the Mac Studio (`192.168.0.159:8799`).
**Project Type**: CLI, hooks and one long-running process, in a flat Python repo.
**Performance Goals**: the send path makes 0 model calls, and the T0 checks add SQL only (SC-003). Each mail gets
its T1 record within 60 s while the host answers (SC-002). The hook target of spec 001 stays (under 1 s on 10,000
rows).
**Constraints**: the envelope state is at most 3,000 characters (D8, from the served limit of 2,048 tokens). No
token cap (R-ML1). Thinking is off by capability, and the live check measures it (D7). The live database is never
opened for writing in a test.
**Scale/Scope**: one user, a few sessions of about 5 agents, tens to hundreds of mails a day.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Gates from `.specify/memory/constitution.md` (v1.2.1):

- [x] **I. Mailroom is the record**: every decision, call and thread is a row (D10, D16). The screener reads
      only the database. `rookery up` copies the team context into two tables (D8). No row is deleted.
      Quickstart 1 runs the change on a copy of the live database.
- [x] **II. Proven, not claimed**: each FR has a test in the trace below. R15 to R18 get the proof tests of the
      register. A task moves R16, R17 and R18 to proven, and R15 to built (D24). The live check stays UNTESTED until
      it runs (quickstart 5).
- [x] **III. Any harness**: the contract stays the `rookery` block and the CLI. The screener reads stored mail,
      so it works for every harness. No hook is the only path.
- [x] **IV. Code decides; models advise**: T0 is code. Each model check starts in shadow mode, and code refuses
      the live switch before 150 compared decisions. The question is a closed choice. Thinking is off, and
      `reasoning_tokens` measures it. No token cap. Every record carries `decided_by`, and the code validates it.
- [x] **V. Simple and surgical**: no new package that the code needs. PromptChain is optional and only in the screener
      (the user's choice, justified in D6). The T0 checks reuse the gate's transaction and its `pre_store` hook.
- [x] **VI. Observable and frugal**: `rookery screen status --json` and `report --json`. One gate-log line for
      each decision. The screener log is a rotated file. Nothing starts or restarts the screener automatically.
      The second opinion runs only in the review band, so the shared lane gets no extra load while the bands are
      unset.
- [x] **VII. Agents' world out of the human's**: no file goes into the host repo. Configuration is in
      `~/.rookery/config.json`.
- [x] **VIII. Plain technical English**: the plan files pass `ste-lint`. The bounce, notice and refusal texts
      are constants with a lint test (FR-032).
- [x] **IX. Rookery native**: no code names a terminal tool or a T2 model. The model lane, the model name, hunch
      and T2 come from configuration. Rookery makes the session names (D22).
- [x] **Development Workflow**: `spec-pane cover . 3` passes for spec.md. It runs again on plan.md and tasks.md.
- [x] **Security**: mail is data. The envelope leaves out `evidence`, and review lists never print a body. The
      injection pattern set is code. Notices carry no body. The change adds no destructive action.

**Post-design re-check (after Phase 1)**: all gates pass. Two items stay open by design. First, the bands have
no measured values. They stay unset, and the live switch refuses until the user sets them from the report (D11).
Second, R15 is proven only after a shadow run of 150 real decisions after the merge.

## Project Structure

### Documentation (this feature)

```text
specs/004-screener-chain/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: facts F1 to F19, decisions D6 to D24
├── data-model.md        # Phase 1: tables, statuses, transitions, mapping, envelope, configuration
├── quickstart.md        # Phase 1: copy check, tests, scratch run, live switch, live check, deploy
├── contracts/screener.md  # Phase 1: commands, backend interface, T2 contract, log line, texts
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
rookery.py                 EDIT schema: inbox.thread, six tables; send(thread=); escalate(); release and
                                quarantine accept escalated (D14)
schema.sql                 NO CHANGE (columns are added by init_db, as in 002)
turn_block.py              EDIT _schema_current knows the new column and tables; pass thread and the parsed
                                block to mail_gate.submit; handoff status done closes the thread (D16)
mail_gate.py               EDIT submit(thread=, block=); call screener_t0.evaluate in the transaction; B3 refuse;
                                pre_store hold for live t1 in pre mode (D9, D13)
screener_t0.py             NEW  T0 checks: injection patterns, duplicate, budget, thread, anomalies; records;
                                bounce texts (D16 to D19)
screener_config.py         NEW  read and check the screener key with defaults; write a check mode (D21)
screener_backend.py        NEW  Answer, BackendError; PromptChain path; stdlib path; fake (D6, D7)
screener.py                NEW  run loop, claims, envelope, T1, suggest, hunch, T2, live actions, escalation
                                notices, calls, agreement, export (D8, D10 to D15, D20)
cli_screen.py              NEW  screen run, status, review, mark, report, export, mode, release, quarantine,
                                escalate; thread close
rookery_cli.py             EDIT register cli_screen.add_parser
cli_workspace.py           EDIT rookery up writes session_context and agent_scope (D8)
rookery_team.py            EDIT optional ask_budget key (positive integer)
rookery_identity.py        EDIT _new_session names a session with rookery_names when no name is given (D22)
rookery_names.py           NEW  three word lists of 64 words; make_name()
postmaster.py              EDIT skip escalated at :144 and :475 (D14)
rookery_views.py           EDIT threads() from the threads table; mail_mirror lists escalated (D14, D16)
mail_screen.py             EDIT release/quarantine/unquarantine write user calls; --by default human:<user>
rookery_config.default.json EDIT screener key (data-model)
bootstrap.py               EDIT _PY_MODULES: screener, screener_t0, screener_config, screener_backend,
                                cli_screen, rookery_names
fixtures/screener/         NEW  team.json, fake_t1.json, injection fixtures, ping-pong fixture
fixtures/bin/hunch         NEW  fake hunch that answers from a fixture file and logs its arguments
skills/rookery/SKILL.md README.md docs/QUICKSTART.md CHANGELOG.md   EDIT screener commands, shadow mode
test_screen_schema.py test_screen_backend.py test_screen_t0.py test_screen_run.py   NEW
test_screen_calls.py test_screen_cli.py test_threads.py test_escalated.py test_session_names.py   NEW
test_gate_scan.py test_views.py test_text_hints.py test_held_quarantine.py   EDIT
```

**Structure Decision**: keep the flat layout. The screener has four modules (T0, configuration, backend, run
loop), so that each test file maps to one concern.

## Requirement Trace

Phase requirements and the FRs that build them:

| RQ | FR | RQ | FR |
|---|---|---|---|
| RQ-3.1 | FR-002 (superseded by D2), FR-006 | RQ-3.8 | FR-020 to FR-023 |
| RQ-3.2 | FR-007 | RQ-3.9 | FR-024 to FR-026 |
| RQ-3.3 | FR-003, FR-008, FR-009 | RQ-3.10 | FR-027 |
| RQ-3.4 | FR-010 | RQ-3.11 | FR-018 |
| RQ-3.5 | FR-011 | RQ-3.12 | FR-028, FR-029 |
| RQ-3.6 | FR-014 to FR-017 | RQ-3.13 | FR-032 |
| RQ-3.7 | FR-019 | RQ-3.14 | FR-013, FR-013a |
| RQ-R.1 | FR-030 | RQ-R.2 | FR-031 |

| FR | Built in | Proof |
|---|---|---|
| FR-001 | gate stores; screener reads later (D9) | `test_screen_t0`: `submit` with the fake backend file set makes 0 fake calls. `test_screen_run`: the screener decides a stored mail |
| FR-002 | no change to `_send_counted` (D2) | `test_gate` loop tests stay green. `test_gate_scan`: no screener module passes `loop` to `quarantine` |
| FR-003 | `screener.py` tiers; `t2.command` (D15) | `test_screen_run`: no T2 command leaves the mail `escalated`. A fixture T2 script decides it. Scan: no model name literal outside `screener_config` defaults |
| FR-004 | `screener_config` modes (D21) | `test_screen_cli`: defaults are `shadow`. `off` writes no record. The 002 T0 tests stay green |
| FR-005 | claim query (D12) | `test_screen_run`: system mail and T0-quarantined mail get no row. A restart goes on from the first mail with no row |
| FR-006 | `screener_t0.evaluate` | `test_screen_t0`: one record for each check, each with `t0:<check>` |
| FR-007 | T1 question (contract) | `test_screen_backend`: the ask has the four labels, no `think` key, no token cap |
| FR-008 | `suggest` question | `test_screen_run`: the roster labels leave out the sender and the first recipient, plus `none`. The answer fills `suggest` |
| FR-009 | `band()` rule (D11) | `test_screen_run`: accept acts (live), review calls hunch, hunch agreement acts, disagreement escalates. Unset bands: 0 hunch calls |
| FR-010 | `check_decided_by` | `test_screen_run`: every record matches one of the five forms. A bad form raises |
| FR-011 | envelope builder (D8) | `test_screen_run`: a 3,001-character state gives `envelope-too-large`, 0 fake calls, no `deliver` (R16, SC-007). `evidence` is never in the envelope |
| FR-012 | `BackendError` causes | `test_screen_backend`: timeout, HTTP 500, an answer outside the labels, hunch exit 0 with `error`, hunch exit 1, each gives a failure and no label |
| FR-012a | live failure path (D14) | `test_escalated`: live T1 with a failing fake gives `escalated`, no release after the lease, a later good answer releases it |
| FR-013 | session-scoped queries | `test_screen_run`: two sessions, the envelope of each holds only its own roster and cards |
| FR-013a | per-session `try`, claims, `--session` | `test_screen_run`: a failure in session A still screens session B. Two processes (`--once` in two subprocesses) give one T1 record for each mail |
| FR-014 | shadow `action = none` | `test_screen_run`: 50 mails of every kind, 0 status changes (SC-001) |
| FR-015 | `mark`, manual actions (D20) | `test_screen_calls`: each row of the mapping table |
| FR-016 | `report` | `test_screen_calls`: 150 decisions, 140 right gives 150 and 0.933, both forms |
| FR-017 | `mode` refusal | `test_screen_calls.LiveSwitch`: 149 refuses with 1 missing, 150 with bands accepts (SC-005) |
| FR-018 | `export` | `test_screen_calls`: one line for each compared decision with the keys of the contract, no body |
| FR-019 | `review_mode`, `pre_store` (D13) | `test_screen_run`: live t1 `pre` holds with `screening`, then releases. `post` delivers at once and sends a notice on a reject |
| FR-020 | `inbox.thread`, `threads` (D16) | `test_threads`: the column holds the block value. The first mail makes the row with `held` or `open`. Same value in two sessions gives two threads |
| FR-021 | approved-thread skip | `test_threads`: live, the second mail gets `skipped` and 0 fake calls. A decline, a duplicate and a loop hold get a full T1 |
| FR-022 | B3 in the gate | `test_threads`: live, refused, not stored, a bounce. Shadow, stored and `would-deny` (SC-008) |
| FR-022a | closers | `test_threads`: each of the four closers, `closed_by` recorded. A boss check with a non-boss refuses. No timer closes |
| FR-023 | `rookery_views.threads` | `test_views`: real threads with status. A mail with no thread is not listed |
| FR-024 | no change | `test_gate` B1 tests stay green |
| FR-025 | budget (D17) | `test_screen_t0`: the team value wins over the configuration. Over budget, the record and `g5`. Live, held and a bounce |
| FR-026 | duplicate (D17) | `test_screen_t0`: the same normalized text inside the window gives `duplicate`. Outside the window it does not. Live, held and a bounce |
| FR-027 | verdict table | `test_screen_run`: each verdict maps to its action in live mode |
| FR-028 | `escalated` (D14) | `test_escalated`: postmaster `_mention_pass` and wake skip it (SC-009). `release` and `quarantine` work from it |
| FR-029 | notices | `test_escalated`: the boss gets a notice with no body. The plugin sink gets the event. No boss: only the sink |
| FR-030 | anomalies (D19) | `test_screen_t0`: G1, G2, G3, G5 each give their record. Live, `escalated`. No G4 or P1 check exists (scan) |
| FR-031 | B3 refuse; decline `suggest` | `test_threads`: a refused mail wakes nobody. `test_screen_run`: a decline that names a peer keeps it as `suggest` |
| FR-032 | text constants | `test_text_hints`: each new text passes `ste-lint` (SC-012) |
| FR-033 | gate-log line; `status` | `test_screen_cli`: one gate line for each record. Status JSON keys of the contract. Host check uses the local fake lane |
| FR-034 | backend ask | `test_screen_backend`: no `max_tokens`, `num_predict`, `num_ctx` or `think` key. Quickstart 5 measures `reasoning_tokens` |
| FR-035 | additive schema | `test_screen_schema`: old database migrates twice. `ScreenSchemaLiveCopy` on a copy (quickstart 1) |
| FR-036 | fake backend | `test_screen_backend`: with `ROOKERY_SCREENER_FAKE` set, the HTTP and hunch paths are never built. A scan: no test file names the lane address except the contract test, which binds `127.0.0.1` |
| FR-037 | `rookery_names` (D22) | `test_session_names`: 1,000 sessions, all different, three words, none like an ID (SC-015). A given name works as before |

SC-001 to SC-015 map to the same tests. SC-002 and SC-013 also need quickstart 5. SC-010 is the 002 loop test,
run again with the ping-pong fixture (R18). SC-011 is `spec-pane cover`. SC-014 is the register task (D24).

Risk proofs: R15 `test_screen_calls.LiveSwitch` (built). R16 `test_screen_run` envelope test. R17
`test_screen_t0` injection fixture with `injection` live, plus a wake check that the recipient is not woken.
R18 the ping-pong fixture.

## Complexity Tracking

No gate needs a justification. These items go past the literal FR text:

| Item | Why the design needs it | Simpler choice and why not |
|---|---|---|
| Two paths in `screener_backend` | the user chose PromptChain with a standard-library fallback (D6) | one path: PromptChain stops the screener under `/usr/bin/python3`; a copy alone can drift |
| `session_context` and `agent_scope` | the envelope needs the scope cards and the criteria from the database (D8) | read generated files: they are mirrors, and the screener would need each session's root |
| `screen_mail` table | one claim and restart point for each mail (D12) | claims in `screen_decisions`: a mail has many records, and two processes could both start |
| Bands unset by default | the OKF gives no measured value (D11) | first values: they have no source and would run hunch on every mail |
| `rookery thread close --as` | FR-022a names the boss as a closer | the user command only: the boss could not close a thread |

## Open items for tasks

- The OKF register (R15 to R18) and the design notes of D24 are edits in the OKF bundle, outside this repo. A
  task makes them after the tests pass.
- Quickstart 5 runs once under each Python before the PR, and its results go into quickstart.md.
- The zipapp and `SKILL.md` deploy waits for the user (quickstart 6).
