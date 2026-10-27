# Data model: Event-driven mail wake

No database change. The inbox, identities, bindings and sessions stay as specs 001 to 004 made them. The feature
adds files that hold run state. Each file is small, and a person can delete it with no loss of mail.

## Files under the project root (`ROOT`)

| File | Owner | Content | Lifetime |
|---|---|---|---|
| `postmaster/pushed/AGENT_ID.json` | existing | the pending IDs that a notice announced | as today |
| `postmaster/backoff/AGENT_ID.json` | NEW | `{"until": epoch, "delay": s, "newest": mail_id, "logged": [mail_id, error]}` | removed after a wake that succeeds |
| `postmaster/lock/AGENT_ID.lock` | NEW | empty; the `flock` of one wake | the OS frees the lock at process end |
| `postmaster/lock/screen-SESSION_ID.lock` | NEW | empty; the `flock` of one screening pass | as above |
| `postmaster/lock/sweep-SESSION_ID.lock` | NEW | empty; the `flock` of the sweep of a session | as above |

Validation: `AGENT_ID` and `SESSION_ID` are Rookery IDs. The code checks them with the existing safe-name rule
before it builds a path. A back-off file that does not parse counts as no back-off.

## Files on a remote machine (`~/.rookery/remote/`)

| File | Content | Lifetime |
|---|---|---|
| `NODE.json` | `{"announced": [mail_id, ...]}`, only IDs that are still unread | rewritten after each check |
| `NODE.lock` | empty; the `flock` of the follower | the OS frees the lock at process end |
| `NODE.follow` | `{"start": epoch}`, the last trigger | rewritten by each trigger |

`NODE` is `ROOKERY_NODE`, checked with the safe-name rule.

## Configuration (`~/.rookery/config.json`)

| Key | Default | Meaning |
|---|---|---|
| `screener.enabled` | `false` | Screening events and the screener sweep run for every session. |
| `sweep_s` | `300` | The sweep interval (top level, not under `wake`, whose keys are binding kinds). Rookery raises a value below 300 to 300 and gives one notice. |
| `wake.*` (pane commands) | existing | Unchanged. The local and the remote wake use them. |

Team file: `screener: true` turns screening on for one session. `rookery up` stores it as a session binding of
kind `screener-enabled` with value `1`, as it stores `rookery-root`. So the schema stays the same. `session`
becomes optional (FR-024).

## State of one wake

```text
event (pending mail, Stop hook, sweep)
  -> push gate says no            -> exit, no log line (as today: no push is not an outcome)
  -> no root                      -> log no-root, exit
  -> lock held by another wake    -> log wake-busy, exit
  -> lock taken -> wake_one       -> pushed | wake-failed | no-idle-state | no-wake-binding | (busy: quiet)
     -> release lock -> recheck   -> new mail to push and round < 3 -> take the lock again
                                  -> else exit
```

## State of the remote follower

```text
trigger (Stop hook or remote send) -> write NODE.follow {start: now} -> spawn follower
follower: lock held -> exit (the running follower reads the new start)
          lock taken -> loop: check -> push? notice, write NODE.json, exit
                                    -> no push: checks since start < 5 -> sleep 300 s -> re-read start -> loop
                                    -> 5 checks done -> exit
```
