# Specification Quality Checklist: Every Rookery send goes through one gate

**Purpose**: Check the spec for completeness and quality before planning
**Created**: 2026-10-21
**Updated**: 2026-10-23 (revision 2: local identity is bookkeeping, research D24)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections complete

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios exist
- [x] Edge cases appear in the spec
- [x] Scope has clear limits
- [x] Dependencies and assumptions appear in the spec

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Implementation details: the spec names the send paths (`rookery send`, the MCP tool, `send_mail.py`, the
  mailroom, A2A, the node runner) and the audit file:line references. These are the surfaces that users
  see and the audit evidence that this feature must close. They are not design choices. The spec does not
  choose a function name, a schema, a language or a test layout. This is the same convention as spec 001.
- Requirement IDs: no RQ IDs exist for this feature in `requirements/rookery-0-7.md`. The spec cites the
  phase-1 and phase-0 IDs that it extends (RQ-0.2, RQ-0.3, RQ-1.19, RQ-1.20, RQ-1.22, RQ-1.24, RQ-1.25).
  WAIVER: `spec-pane cover` checks that every `RQ-<phase>.N` ID of the requirements file has a citation
  (`~/.local/bin/spec-pane:96-120`). This feature extends spec 001's IDs, so `cover` for phase 1 fails by
  design. `plan.md` (Constitution Check) records the same waiver. The real fix is to add RQ IDs for this
  feature to the external requirements file. That is the user's decision.
- Two choices that the input left open: the spec decides how current MCP, `mailctl.py` and
  `send_mail.py` users keep working, and the form of the mention notice. Both appear under Assumptions, so that
  `/speckit-clarify` can review them. Neither has a clarification marker. Revision 2 (2026-10-23): a sender
  with no session now delivers to `legacy` with a log warning, so there is no move-path bounce.
- FR-006 changes one sentence of spec 001 FR-039: the loop breaker now applies to every send path.
- Constitution VIII: spec.md passes `ste-lint --max-score 2.0 --glossary ste-glossary.txt`.
