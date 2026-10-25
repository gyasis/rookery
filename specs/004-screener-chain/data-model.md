# Data model: the screener chain and the broker gates

All changes are additive (constitution I, FR-035). `rookery.init_db` adds them. New inbox columns go in
`_add_column` calls and in the check of `turn_block._schema_current`. New tables go in `_SESSION_TABLES_SQL` as
`CREATE TABLE IF NOT EXISTS`. The change deletes no row. Times are integers from `rookery.now()`, the same as the
inbox. The exception is `session_context.updated_at`, which is ISO text like the other 003 tables.

## Inbox: one new column and one new status

| Column | Type | Rule |
|---|---|---|
| `thread` | TEXT NULL | the `thread` value of the block (at most 128 characters, `turn_block.MAX_THREAD_CHARS`). NULL for a mail with no block or no thread |

New status `escalated`. Transitions (each one goes through `rookery._decide`):

| From | To | By |
|---|---|---|
| `held` | `escalated` | `rookery.escalate`: a failed or too-large T1 call in live mode, a disagreement with no T2, a live anomaly, `rookery screen escalate` |
| `escalated` | `pending` | `rookery.release`: a later model answer `deliver`, T2 `deliver`, a person |
| `escalated` | `quarantined` | `rookery.quarantine`: a later model reject label, T2, a person |

The existing transitions do not change. New `held` reasons (in `inbox.reason`, written at store time):
`screening` (live T1 in `pre` mode), `budget`, `duplicate`. The existing `loop` stays.

## `session_context` and `agent_scope` (D8)

`rookery up` writes both from the team file, in the same transaction as the session (it replaces the rows of
that session). The screener reads only these rows.

| `session_context` | Type | Rule |
|---|---|---|
| `session_id` | TEXT PK | references `rookery_sessions` |
| `task` | TEXT NULL | team `task` |
| `acceptance` | TEXT NOT NULL | JSON list of text, `[]` when absent |
| `ask_budget` | INTEGER NULL | team `ask_budget` (new optional team key, a positive integer) |
| `updated_at` | TEXT NOT NULL | ISO time |

| `agent_scope` | Type | Rule |
|---|---|---|
| `agent_id` | TEXT PK | references `identities` |
| `session_id` | TEXT NOT NULL | |
| `does` | TEXT NOT NULL | JSON list of text |
| `does_not` | TEXT NOT NULL | JSON list of text |

## `threads` (D16)

| Column | Type | Rule |
|---|---|---|
| `session_id` | TEXT NOT NULL | |
| `thread` | TEXT NOT NULL | |
| `status` | TEXT NOT NULL | `held`, `open`, `approved` or `closed` |
| `opened_by` | TEXT NOT NULL | sender of the first mail |
| `first_mail` | INTEGER NOT NULL | inbox ID of the first mail |
| `opened_at` | INTEGER NOT NULL | |
| `closed_by` | TEXT NULL | a `decided_by` form, e.g. `screener:tev1:4b`, `human:gyasis`, `boss:<agent>`, `handoff:<agent>` |
| `closed_at` | INTEGER NULL | |
| `close_reason` | TEXT NULL | `veto`, `boss`, `user`, `done` |
| | | PRIMARY KEY `(session_id, thread)` |

Transitions:

| From | To | When |
|---|---|---|
| (none) | `held` | Rookery stores the first mail as held |
| (none) | `open` | Rookery stores the first mail as pending |
| `held` | `open` | Rookery releases the first mail |
| `held`, `open` | `approved` | T1 is live and the first mail gets `deliver` |
| any but `closed` | `closed` | a live veto, `rookery thread close`, a boss close, a `handoff` with `status: done` from `opened_by` |

`closed` is final. Nothing reopens a thread. A new thread value makes a new thread.

## `screen_mail` (D10, D12)

One row for each screened mail. The claim and the restart point.

| Column | Type | Rule |
|---|---|---|
| `mail_id` | INTEGER PK | inbox ID |
| `session_id` | TEXT NOT NULL | |
| `state` | TEXT NOT NULL | `claimed`, `decided`, `failed`, `escalated` |
| `owner` | TEXT NULL | `<host>:<pid>:<random>` of the claiming process |
| `claimed_at` | INTEGER NULL | |
| `attempts` | INTEGER NOT NULL DEFAULT 0 | model tries |
| `retry_at` | INTEGER NULL | next retry of a failed call in live mode |
| `envelope` | TEXT NULL | the compact envelope JSON (D8). NULL when the mail has no envelope (T0 only) |
| `ask_hash` | TEXT NULL | SHA-256 of the normalized `say` text, for an ask (D17) |
| `final_label` | TEXT NULL | the label that acted, or the last label in shadow mode |
| `final_by` | TEXT NULL | `decided_by` of that label |
| `decided_at` | INTEGER NULL | |

The gate writes the row with state `decided` for a mail that only T0 decides (a T0 quarantine at store). The
screener does not screen that mail. The screener claims the other mails with `INSERT OR IGNORE`. A claim older
than `lease_s` goes back to the queue. In shadow mode a failure gives `failed`, which is final. In live mode a
failure gives `escalated` with a `retry_at`.

Index: `(session_id, state)`. Index on `ask_hash` for the duplicate look-up.

## `screen_decisions` (D10)

One row for each answer of each check or tier.

| Column | Type | Rule |
|---|---|---|
| `id` | INTEGER PK | |
| `mail_id` | INTEGER NULL | NULL only for a B3 refusal, where Rookery stores no mail |
| `session_id` | TEXT NOT NULL | |
| `stage` | TEXT NOT NULL | `t0`, `t1`, `suggest`, `hunch`, `t2` |
| `check_name` | TEXT NOT NULL | `injection`, `duplicate`, `budget`, `thread`, `gates`, `t1`, `suggest`, `hunch`, `t2` |
| `attempt` | INTEGER NOT NULL DEFAULT 1 | the model try; T0 is always 1 |
| `label` | TEXT NULL | a T1 label, `skipped`, a T0 record (`injection-suspect`, `duplicate`, `over-budget`, `would-deny`, `denied`), an anomaly (`g1-task-complete`, `g2-no-gap`, `g3-gap-in-scope`, `g5-ask-not-allowed`), or an agent name for `suggest` |
| `confidence` | REAL NULL | model answers only |
| `band` | TEXT NULL | `accept`, `review`, `escalate`, `unset` |
| `mode` | TEXT NOT NULL | `shadow` or `live`: the mode of the check at that time |
| `action` | TEXT NULL | what the record did: `none`, `release`, `quarantine`, `hold`, `escalate`, `refuse` |
| `decided_by` | TEXT NOT NULL | one of the FR-010 forms (validated by `screener.check_decided_by`) |
| `backend_impl` | TEXT NULL | `promptchain`, `stdlib` or `fake` (D6) |
| `failure` | TEXT NULL | the cause when there is no label: `envelope-too-large`, `timeout`, `http <code>`, `bad-answer`, `no-backend` |
| `detail` | TEXT NULL | pattern name, normalized-question hash, budget numbers. Never a mail body |
| `prompt_tokens` | INTEGER NULL | from `usage` |
| `reasoning_tokens` | INTEGER NULL | from `usage`; 0 or NULL proves thinking is off (SC-013) |
| `created_at` | INTEGER NOT NULL | |
| | | UNIQUE `(mail_id, check_name, attempt)` |

Rules:

- A record has a `label` or a `failure`, never both and never neither (FR-012).
- In shadow mode `action` is always `none` (FR-014).
- Each insert also writes one gate-log line with `path: "screener"` (FR-033).

## `screen_calls` (D20)

| Column | Type | Rule |
|---|---|---|
| `id` | INTEGER PK | |
| `decision_id` | INTEGER NOT NULL | references `screen_decisions` |
| `mail_id` | INTEGER NOT NULL | |
| `call_label` | TEXT NOT NULL | the label the person says is right |
| `verdict` | TEXT NOT NULL | `right` or `wrong` (computed against the decision label) |
| `source` | TEXT NOT NULL | `mark`, `release`, `quarantine`, `unquarantine` |
| `by` | TEXT NOT NULL | `human:<user>` |
| `created_at` | INTEGER NOT NULL | |

The newest call for a decision counts. A manual action writes one call for each `t0`, `t1`, `hunch` and `t2`
decision of the mail.

Mapping from an action to a call:

| Action of a person | Call for a model decision (`t1`, `hunch`, `t2`) | Call for a T0 record that would block |
|---|---|---|
| release, unquarantine | `call_label = deliver` | `wrong` |
| quarantine with a T1 label as the reason | `call_label` = the reason | `right` |
| quarantine with another reason (`off-team`, `malformed`, `injection-suspect`, `loop`) | `call_label` = the reason (it never equals a T1 label, so it is a disagreement) | `right` |
| `mark ID right` | `call_label` = the decision label | `right` |
| `mark ID LABEL` | `call_label` = LABEL | not allowed (use `right` or `wrong`) |
| `mark ID wrong` | refused: give the right label | `wrong` |

`verdict` is `right` when `call_label` equals the decision label, else `wrong`. A `suggest` decision gets a call
only from `mark`.

## Monitor verdicts (FR-027)

| Verdict | T1 label | Action in live mode |
|---|---|---|
| APPROVE | `deliver` | `release` |
| VETO_DONE | `task-already-done` | `quarantine task-already-done` |
| VETO_WRONG_PEER | `wrong-recipient` | `quarantine wrong-recipient` with `suggest` |
| VETO_UNNEEDED | `unneeded` | `quarantine unneeded` |
| VETO_OFF_TEAM | (T0 off-team) | `quarantine off-team` (as today) |
| TAKEOVER, hold for a human | (none) | `escalate` |

## The envelope (D8)

```json
{"mail": 812, "session": "calm-amber-heron", "kind": "ask", "from": "researcher", "to": "counsel",
 "task": "T-7", "thread": "contract-review", "self_check": {"task_complete": false, "gap": "...",
 "gap_in_my_scope": false}, "read": ["docs/brief.md"], "ask": "<say text>",
 "from_scope": {"does": [], "does_not": []}, "to_scope": {"does": [], "does_not": []},
 "acceptance": ["..."]}
```

Compact JSON (`separators=(",", ":")`), keys in this order. The envelope omits empty or absent fields. It omits `evidence`,
because it can hold pasted work. At most `envelope_max_chars` characters (first value 3,000).

## Configuration (D21)

```json
"screener": {
  "checks": {"injection": "shadow", "duplicate": "shadow", "budget": "shadow", "thread": "shadow",
             "gates": "shadow", "t1": "shadow", "hunch": "shadow", "t2": "shadow"},
  "bands": {"t1": null},
  "base_url": "http://192.168.0.159:8799/llm/ollama",
  "model": "tev1:4b",
  "timeout_s": 300,
  "hunch": {"model": "qwen2.5:3b", "url": null},
  "t2": {"command": null, "batch": 20},
  "envelope_max_chars": 3000,
  "ask_budget": 20,
  "duplicate_window_s": 3600,
  "review_mode": {"default": "pre", "agents": {}},
  "lease_s": 600
}
```

`bands.t1` is `{"accept": A, "review": R}` with `0 <= R <= A <= 1`, or null. Only `t1` has bands: hunch acts by
agreement with T1 (D11). A bad value is an error at
screener start, and the error names the key.

## Session names (D22)

No schema change. `rookery_sessions.name` gets `<mood>-<color>-<animal>` when the caller gives no name.
