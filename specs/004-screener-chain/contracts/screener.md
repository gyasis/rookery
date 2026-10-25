# Contract: the screener commands, the backends and the records

All commands are subcommands of `rookery`, in a new module `cli_screen.py` (registered next to the others at
`rookery_cli.py:116`). Each command that prints has `--json`. Each error goes to stderr with exit code 2 and
names the item that is wrong. Every text that an agent or a person reads passes `ste-lint --max-score 2.0`
(FR-032).

## `rookery screen run`

```
rookery screen run [--session NAME] [--once] [--poll SECONDS]
```

- Loops over the open sessions (or the one session NAME) and screens the mail that has no `screen_mail` row
  (D12). `--poll` defaults to 2 s. `--once` does one pass and exits 0.
- At start it prints one line: the backend path (`promptchain` or `stdlib`), the Python, the base URL, the model,
  and each check mode. It exits 2 when the configuration is not valid.
- A session error goes to `~/.rookery/logs/screener.jsonl` (`turn_block.log_event`, stream `screener`, a rotated
  file) with the session, the mail ID and the cause. The loop then goes to the next session.
- SIGTERM ends the loop after the current mail. A claimed mail with no answer goes back to the queue after
  `lease_s`.

## `rookery screen status`

```
rookery screen status [--session NAME] [--json]
```

JSON form (FR-033):

```json
{"backend": "promptchain", "python": "/home/gyasis/miniconda3/bin/python3", "model": "tev1:4b",
 "host": {"url": "http://192.168.0.159:8799/llm/ollama", "answers": true, "checked_at": 1761400000},
 "checks": {"injection": "shadow", "duplicate": "shadow", "budget": "shadow", "thread": "shadow",
            "gates": "shadow", "t1": "shadow", "hunch": "shadow", "t2": "shadow"},
 "sessions": [{"session": "calm-amber-heron", "backlog": 3, "oldest_age_s": 41, "escalated": 1,
               "failed": 0, "decided": 120, "held_screening": 2}]}
```

`host.answers` comes from `GET {base_url}/v1/models` with a 5 s timeout. That is a metadata call, not a model
call. The text form prints the same items, one per line.

## `rookery screen review`, `mark`, `report`, `export`

```
rookery screen review [--session NAME] [--check CHECK] [--limit N] [--json]
rookery screen mark DECISION_ID (right | wrong | LABEL) [--by NAME]
rookery screen report [--check CHECK] [--json]
rookery screen export [--check CHECK] [--out FILE]
```

- `review` lists decisions with no call. Each row shows the decision ID, the mail ID, the check, the label, the confidence, the
  sender, the recipient, the task and the thread. It never prints a mail body. To see a body, the person uses
  `rookery peek`.
- `mark` writes one `screen_calls` row (data-model mapping). `--by` defaults to `getpass.getuser()`, and Rookery
  stores the call as `human:<name>`.
- `report` JSON form (FR-016):

```json
{"checks": {"t1": {"compared": 152, "agree": 140, "rate": 0.921,
                    "disagreements": {"deliver->unneeded": 7, "unneeded->deliver": 5},
                    "by_confidence": {"0.9": {"compared": 120, "rate": 0.96}, "0.8": {"compared": 20, "rate": 0.8}},
                    "live_ready": true, "missing": 0}}}
```

  `live_ready` is true for a model check (`t1`, `hunch`, `t2`) when `compared >= 150`, and for `t1` also when the
  configuration holds its bands. `missing` is
  `max(0, 150 - compared)`.
- `export` writes one JSON line for each compared decision (FR-018, D20):

```json
{"mail_id": 812, "session": "calm-amber-heron", "check": "t1", "envelope": {...}, "label": "deliver",
 "confidence": 0.97, "decided_by": "screener:tev1:4b", "call_label": "deliver", "call_by": "human:gyasis",
 "called_at": 1761400100}
```

## `rookery screen mode`

```
rookery screen mode CHECK (shadow | live | off)
```

- Writes `screener.checks.CHECK` in `~/.rookery/config.json`, and keeps every other key.
- For `t1`, `hunch` and `t2` with `live`: refuses (exit 2) when the person compared fewer than 150 decisions. For
  `t1` it also refuses when the configuration holds no bands. The refusal text gives the number that is missing (FR-017, SC-005). For example: "Rookery cannot
  switch t1 to live mode. It has 149 compared decisions and needs 150. Compare 1 more decision with `rookery
  screen mark`."
- A change takes effect at the next pass of a running screener, which reads the configuration again each pass.

## `rookery screen release`, `quarantine`, `escalate`

```
rookery screen release MAIL_ID [--by NAME]
rookery screen quarantine MAIL_ID REASON [--note TEXT] [--suggest AGENT] [--by NAME]
rookery screen escalate MAIL_ID [--reason TEXT] [--by NAME]
```

- `release` and `quarantine` work from `held` or `escalated` (D14). They write the user calls (D20).
- `cli_screen.py` imports `screener` only inside these handlers, never at the top of the module (D6, F20).
- `escalate` moves a `held` mail to `escalated` (TAKEOVER, hold for a human, FR-028) and sends the notices.
- `mail_screen.py --release`, `--quarantine` and `--unquarantine` also write the user calls. Their `--by` default
  changes from `human` to `human:<user>`.

## `rookery thread close`

```
rookery thread close SESSION THREAD [--as AGENT] [--json]
```

- With no `--as`: the user closes the thread. `closed_by = human:<user>`, `close_reason = user`.
- With `--as AGENT`: AGENT must have the role `boss` in that session, else exit 2. `closed_by = boss:<agent>`,
  `close_reason = boss`.
- A closed thread stays closed. The command on a closed thread exits 0 and says it is already closed.

## The decision backend (D6)

```python
class Answer(NamedTuple):
    label: str            # one of the labels given
    confidence: float     # 0..1
    usage: dict           # {"prompt_tokens": int|None, "reasoning_tokens": int|None}
    impl: str             # "promptchain" | "stdlib" | "fake"

class BackendError(Exception):
    cause: str            # "timeout" | "http <code>" | "bad-answer" | "no-backend" | "unreachable"

def system_one(cfg) -> Backend        # T1 and suggest
def hunch_vote(cfg) -> Backend        # second opinion, HUNCH_VOTE=6
def fake(path) -> Backend             # ROOKERY_SCREENER_FAKE
backend.decide(state: str, labels: list[str], instructions: str) -> Answer
```

- `ROOKERY_SCREENER_FAKE=<file>` replaces both model backends with `fake`. The file is JSON:
  `{"t1": [{"match": "<substring of state>", "label": "deliver", "confidence": 0.95}, {"fail": "timeout"}],
  "hunch": [...]}`. The first rule whose `match` is in the state answers. A rule with `fail` raises
  `BackendError(fail)`. The fake counts its calls in `<file>.calls` (one line for each call), so a test can
  prove 0 calls.
- The T1 question is one `choice` question named `route`. Its instructions say what each label means. The
  `wrong-recipient` question is a second `choice` named `suggest`. Its labels are the roster of the session
  without the sender and the first recipient, plus `none` (FR-008).

## The T2 command (D15)

- stdin: one envelope JSON for each line, with the key `mail_id`.
- stdout: one JSON line for each mail: `{"mail_id": 812, "label": "deliver", "reason": "..."}`.
- The exit code is 0. The timeout is `timeout_s`. A mail with no valid line stays `escalated`, and it gets a
  failure record `bad-answer`.

## The gate log line (FR-033)

Each decision writes one line to `~/.rookery/logs/gate.jsonl`:

```json
{"ts": 1761400000, "path": "screener", "session": "rs-1a2b3c4d", "msg_id": 812, "stage": "t1",
 "check": "t1", "label": "deliver", "confidence": 0.97, "mode": "shadow", "action": "none",
 "decided_by": "screener:tev1:4b", "failure": null}
```

## The bounce and notice texts

Each text is a constant in `screener_t0.py` or `screener.py`, and `test_text_hints` lints it.

| Case | Text |
|---|---|
| B3 refusal | "Rookery did not deliver this mail. The thread THREAD has closed. Start a new thread for new work." |
| Over budget | "Rookery held this ask. The task TASK has used its budget of N asks. Ask the boss for more budget, or finish the task." |
| Duplicate | "Rookery held this ask. You sent the same question to RECIPIENT at TIME. Wait for that answer." |
| Escalation notice | "Mail ID from SENDER to RECIPIENT waits for a decision. Reason: REASON. Use rookery screen release or rookery screen quarantine." |
| Post-mode notice | "Rookery delivered mail ID from SENDER to RECIPIENT. The screener says LABEL. Use rookery screen review to check it." |
