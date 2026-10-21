# Turn fixtures

Each file is the final message of one agent turn. The tests run each file through
`turn_block.dispatch_text()` and `rookery hook stop`, then compare the result with the outcome
table in `specs/001-rookery-init-turn-block/data-model.md` ("Outcome of one block").

The tests use session `alpha` with the identities `me` and `law-counsel`.

## US1 fixtures (T013)

| File | What it holds | Expected outcome | `HANDOFF.md` |
|---|---|---|---|
| `no-block.txt` | no `rookery` block | no block: no mail | unchanged |
| `valid-ask.txt` | `to: law-counsel`, `task: TASK-12`, `kind: ask`, `status: in-progress`, one `thread` | valid, other kind: 1 row | rewritten |
| `two-blocks.txt` | two valid blocks | only the last block is used: 1 row | rewritten |
| `example-then-real.txt` | an example in a `json` fence, then a real block | the real block is used: 1 row | rewritten |
| `only-example-block.txt` | one `rookery` block in the middle of the message | that block is the last block: 1 row | rewritten |
| `unclosed-fence.txt` | a `rookery` fence that never closes | no block (an unclosed fence is not a block): no mail | unchanged |
| `backticks-in-say.txt` | a valid block whose `say` holds backticks | valid, other kind: 1 row | rewritten |
| `malformed-json.txt` | block body is not valid JSON | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-missing-to.txt` | no `to` field | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-kind-veto.txt` | `kind: veto` | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-call-field.txt` | a `call` field | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-unknown-field.txt` | a field that is not in the schema | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-status.txt` | a `status` value that is not `done` or `in-progress` | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-oversize.txt` | a block of more than 4,096 bytes | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-say-long.txt` | a `say` of 1,201 chars | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-read-multiline.txt` | a `read` entry with a line break | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-read-long.txt` | a `read` entry of 257 chars | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-read-outside.txt` | a `read` path outside the Rookery root | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-read-dotdot.txt` | a `read` path with `..` | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `malformed-task-path.txt` | `task: TASK/12` | malformed: 1 row quarantined `malformed` + 1 bounce | unchanged |
| `wrong-recipient.txt` | `to: law-cousel` (in no session) | 1 row quarantined `wrong-recipient` + 1 bounce with `suggest` | rewritten |
| `off-team.txt` | `to` is only in another session | 1 row quarantined `off-team` + 1 bounce | rewritten |
| `handoff-in-progress.txt` | `kind: handoff`, `status: in-progress` | no mail (outcome `handoff-pending`) | rewritten, `status: in-progress` |
| `handoff-done.txt` | `kind: handoff`, `status: done` | 1 row (`pending`, or `held` with `ROOKERY_HOLD=1`) | rewritten, `status: done` |

## US2 fixtures (T021)

| File | What it holds | Expected outcome | `HANDOFF.md` |
|---|---|---|---|
| `ask-in-progress.txt` | an `ask` with `status: in-progress`, two `artifacts`, two `read` entries and a `self_check.gap` | valid, other kind: 1 row | rewritten |
