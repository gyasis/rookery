---
type: DataModel
task: 002-mail-gate
updated: 2026-10-23
revision: 2
---

# Data model: every Rookery send goes through one gate

There is no schema change. This feature adds no table, no column and no quarantine reason. `init_db`
changes nothing, so Rookery needs no migration. Quickstart step 1 still checks this on a COPY of the live
database: the row counts and the schema stay the same (constitution I, FR-026). Spec 001 owns
`rookery_sessions`, `identities`, `bindings` and `loop_counts`
(`specs/001-rookery-init-turn-block/data-model.md`). This feature does not change them.

The mailroom keeps its one shared token in the process, not in the database (D7).

## Gate input (`mail_gate.submit`)

| Field | Rule |
|---|---|
| `path` | one of `block`, `send`, `mcp-stdio`, `mcp-http`, `send-mail`, `mailroom`, `a2a`, `node-runner` |
| `principal` | `(session, identity)` from local resolution, or `None`. An HTTP path always passes `None` |
| `raw_sender` | the path's raw name when there is no principal and no asked sender: a node name, `human` for `send_mail.py`, or `anon` for HTTP with no sender |
| `asked_sender` | the name from `--from`, a body field or a header, or `None` |
| `to` | a name or an agent ID |
| `body`, `topic` | text. `topic` can be empty |
| `task` | the block `task`, else `topic`, else `-`. The loop key |
| `pre_store` | a callable, or `None`. It gets the resolved recipient row after check 6 (screening). It returns `None` or `("held", reason)` |
| `root` | the Rookery root, for the log |

The sender of the mail is `asked_sender` when given, else the agent ID of the principal, else `raw_sender`.
The session of the mail is the session of the principal, else `legacy`.

## Gate decision (output of `mail_gate.submit`)

| Field | Rule |
|---|---|
| `outcome` | `sent`, `quarantined`, `loop-held`, `refused`, `db-busy`, `send-failed` |
| `reason` | for `quarantined`: a `QUARANTINE_REASONS` value. For `refused`: `system-name` or `bad-input`. Else NULL |
| `warning` | `no-session` when the sender has no session and Rookery delivered the mail. Else NULL |
| `msg_id` | the stored row, or NULL for `refused`, `db-busy` and `send-failed` |
| `bounce_id` | the bounce row, when Rookery sent one |
| `decided_by` | `gate` for every decision this feature adds. The block path keeps `turn-block-parser` (`turn_block.py:477`) |

Order of checks:

1. The sender is a system name (asked, or raw): `refused`/`system-name`.
2. The sender does not match `^[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,64})?$`, or `to` or `body` is empty or
   not text: `refused`/`bad-input`. Check 1 ignores case and any `@<peer>` part (`Rookery`, `postmaster@x`).
3. Choose the session. A principal gives its session. No principal gives `legacy`, and the warning
   `no-session`.
   - With no session, checks 4 and 5 do not run. Rookery stores the recipient as given. The gate does not
     rewrite it into an identity ID.
4. The sender has a session. The recipient is not in that session but is in another session: quarantine
   `off-team`.
5. The sender has a session. The recipient is in no session: quarantine `wrong-recipient`, with suggestions.
   A recipient name that matches an identity in the sender's session becomes that identity's agent ID.
6. The policy denies the content: quarantine `injection-suspect`. This check runs for every sender. For a
   sender with no session, Rookery stores the quarantine and its bounce in `legacy`.
   When checks 4 to 6 pass, `pre_store` runs. A `("held", reason)` answer stores the mail held with that reason (the block path uses it for `handoff-pending`). A quarantine always comes before a hold.
7. The loop counter is at its limit: store held with reason `loop`. No bounce. The counter uses the
   sender's session, `legacy` when there is none.
8. Else: store `pending` in the session of check 3.

`inspect_inbound` (screening) runs before `BEGIN IMMEDIATE`. A slow custom policy does not hold the write lock. Check 6 uses its verdict. The transaction covers the steps that read and write the database. A locked database gives `db-busy`, and the transaction stores nothing.

A block that does not parse goes through `mail_gate.quarantine_malformed`. It holds the mail, quarantines it as `malformed` and sends the bounce. `turn_block` never calls `rookery.send` for it.

## Quarantine reasons (RQ-0.3) — `rookery.QUARANTINE_REASONS`

No change. The set stays: `wrong-recipient`, `task-already-done`, `unneeded`, `off-team`, `malformed`,
`injection-suspect`, `loop`.

## Mail row — table `inbox` (no column change)

| Rule | Source |
|---|---|
| `session_id` is the sender's session. For a sender with no session, and for a bounce to it, it is `legacy` | FR-005, FR-014 |
| `sender` is the asked sender, else the agent ID of the principal, else the raw name. A system sender name appears only for Rookery's own mail | FR-008, FR-016 |
| A read never changes `status`. Only an ack moves `pending` to `done` | FR-020, FR-032 |

State changes that this feature adds or uses:

```text
(gate) ── pending ── ack ──> done
   │
   ├── held(loop)        (release, as in spec 001)
   └── held ──> quarantined(reason) + bounce row
```

## Unread query (FR-015)

`mail_gate.unread(conn, identity)` returns the mail that matches one of these two rules, and no other mail:

- `session_id` is the session of the identity, and `recipient` is its agent ID.
- `session_id` is `legacy`, and `recipient` is the name or the agent ID of the identity.

A reader with no identity uses `unread_legacy(raw_name)`: `legacy` mail to that raw name. Counts and notices
use the same query.

## Gate log record — `<root>/logs/gate.jsonl`

`ts`, `path`, `principal`, `sender`, `asked_sender`, `recipient`, `session`, `outcome`, `reason`, `warning`,
`msg_id`, `bounce_id`, `decided_by`. `principal` is the agent ID of the resolved identity, or NULL. The
log shows a mismatch when `asked_sender` and `principal` differ. One record for each call to `submit()` and `system_send()` (FR-030). `rookery loop reset` writes one record with outcome `loop-reset` (FR-037).

The log path is `<ROOKERY_ROOT>/logs/gate.jsonl`, else `~/.rookery/logs/gate.jsonl`. The mailroom and the shared MCP server have no project name, so they use the second form. A failed log write prints one line to stderr. It never raises.
