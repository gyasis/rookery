# branch/commit-message relay (approach a: one-branch-per-conversation)

`msg_relay.py` is a lightweight, git-native inter-agent messaging transport.
Each conversation gets its own branch `conv/<conversation-id>`; every message
is a commit on that branch, and the commit body carries a JSON envelope.

This does **not** replace rookery's durable SQLite mailroom — it is a
branch-scoped channel where the commit history *is* the conversation log.

## Envelope format

Every message commit has a short subject line and a JSON body. The body is the
envelope, with exactly these fields and no extras:

| field         | meaning                                            |
|---------------|----------------------------------------------------|
| `from`        | sender slug                                        |
| `to`          | recipient slug                                     |
| `type`        | message type (`"request"`, `"reply"`, `"message"`, …) |
| `payload`     | message body (string)                              |
| `timestamp`   | ISO-8601 UTC timestamp                             |
| `message_id`  | stable unique id for the message                   |

Example commit body:

```json
{
  "from": "agent-a",
  "to": "agent-b",
  "payload": "ping — are you there?",
  "message_id": "msg-a1b2c3d4",
  "timestamp": "2026-09-19T12:00:00+00:00",
  "type": "request"
}
```

## Helpers

### `write_msg(conversation_id, sender, recipient, payload, *, msg_type="message", repo=".", ...)`

Posts a message as an **empty commit** on `conv/<conversation-id>`, creating
the branch from HEAD if it does not already exist. Returns the `message_id`.

### `read_msg(conversation_id, *, repo=".", after=None)`

Reads messages from the conversation branch oldest-first as
`(commit_sha, Envelope)` tuples. Pass `after=<sha>` to read only messages newer
than that commit. Commits whose body does not parse as a valid envelope are
skipped.

## Worked example (approach a)

```python
import msg_relay as mr

conv = "planning-2026-q4"

# agent-A posts a request
mr.write_msg(conv, "agent-a", "agent-b",
             "draft the migration plan?", msg_type="request")

# agent-B reads the conversation so far
for sha, env in mr.read_msg(conv):
    print(env.frm, "->", env.to, env.type, repr(env.payload))

# agent-B replies on the same branch
mr.write_msg(conv, "agent-b", "agent-a",
             "plan is in progress", msg_type="reply")

# the full round-trip is now the commit log of conv/planning-2026-q4
```

## CLI

```sh
python msg_relay.py write --conversation planning-2026-q4 \
    --from agent-a --to agent-b --payload "hello" --type request
python msg_relay.py read  --conversation planning-2026-q4
python msg_relay.py demo
```

## Scope and deferred work

This slice implements **approach (a)** only: one branch per conversation,
messages are commits on that branch, round-trip between two simulated agents.

**Approach (b)** — a single shared branch carrying a commit stream that all
conversations append to — is deferred to a follow-up task.