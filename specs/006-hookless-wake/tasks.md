# Tasks: Hookless wake through herdr (006)

**Spec**: `specs/006-hookless-wake/spec.md`. Quick fix (D1), so there is no separate plan.md.

## Done (2026-10-27)

- [x] T001 `postmaster._herdr_state` and the `FileNotFoundError` branch of `wake_one`: herdr fallback for an agent with no `turn.json` (FR-001 to FR-004, FR-006).
- [x] T002 `postmaster._notice(with_env=True)`: the notice names `ROOKERY_SESSION` and `ROOKERY_NODE` (FR-005).
- [x] T003 `rookery_wake.DEFAULT_SWEEP_S = 120`, `MIN_SWEEP_S = 60`, `sweep_interval(config, override)` (FR-013, FR-015).
- [x] T004 `postmaster._sweep_should_end` and the 5-second wait between rounds (FR-008, FR-009).
- [x] T005 `rookery up --sweep SECONDS`, team key `sweep_s`, session binding `sweep-s` (`rookery_identity.set_session_binding`) (FR-014).
- [x] T006 `rookery session end` prints the watcher state (FR-011).
- [x] T007 Harness `kimi` in `rookery_config.default.json` and `rookery_team.HARNESSES` (FR-012).
- [x] T008 Tests: `test_wake_hookless.py` (34 tests after the audit and T011). Updated the sweep tests for 120 s and the 5-second wait. Pointed `test_docs_005` at the `0.8.0` section.
- [x] T009 Docs: CHANGELOG `Unreleased`, README wake section, `skills/rookery/SKILL.md`.
- [x] T010 Full suite: 1702 passed, 7 skipped. Live wake of Kimi in session `grid`: `outcome: pushed`, notice in pane `w8:p2`.

## Open

- [x] T011 Wait for an empty box (D6, FR-016): `plugins.input_probe`, `plugin_herdr._input`, outcome `draft-in-box`, config `draft_wait`. 8 tests in `TestDraftInBox`. Live probe on the grid: Claude, Kimi and Codex read as empty. The probe drops the dim Codex placeholder. omp gives no answer.
- [ ] T012 Install: rebuild `~/.local/bin/rookery`, copy the skill, restart `rookery-mcp` and `rookery-mailroom`, then run `rookery up --session NAME` again for each session.
- [x] T013 Noted, not fixed: SC-005 missed: `postmaster.py` has 88 lines added and 20 removed (about 20 of the added lines are checks moved into `_sweep_should_end`).
