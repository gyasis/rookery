## Rookery (project: {project})

These rules apply only when Rookery gives you an identity at session start. If it does not, ignore this section.

The shared Rookery root is `{root}`.

### Know who you are and read your mail

- Run `rookery identity show` to see who you are.
- Run `rookery inbox` to read your unread mail. This does not mark the mail as read.
- Run the command on the last line of the output, `rookery ack ID ...`, to mark that mail as read.

### Send mail with a block

To send mail, end your final message with one `rookery` block. A `rookery` block is a fenced block that has the info string `rookery`. It holds one JSON object. Put nothing after it.

Write no block when you have nothing to send. Rookery then sends nothing.

Do not send a block only to say that you got or read mail. Mark the mail as read with `rookery ack`, and write no block.

On a long task, end each turn with a block that has `kind` `handoff` and `status` `in-progress`. Set `to` to the agent that gets the task at the end. This block updates your `HANDOFF.md` and sends no mail. When you finish the task, set `status` to `done`. Rookery then sends the hand-off mail.

The example below uses a `json` fence on purpose. Do not copy that fence. Use `rookery` for a real block.

```json
{
  "to": "reviewer",
  "task": "T042",
  "kind": "handoff",
  "say": "The parser is done. Please check the edge cases.",
  "status": "done",
  "read": ["{root}/T042/notes-T042.md"]
}
```

Fields you must set:

- `to`: the name of the agent that gets the block.
- `task`: the task ID. Use letters, digits, `.`, `_` and `-` only. Use 1 to 64 characters.
- `kind`: one of `ask`, `answer`, `decline`, `handoff`, `status`, `recruit`.
- `say`: your message. Use 1 to 1,200 characters.
- `status`: `done` or `in-progress`.

Fields you can set:

- `read`: a list of file paths. Each `read` entry is one path: one list item, one line of text. Each path has at most 256 characters and is inside `{root}`.
- `thread`: a thread name. Use at most 128 characters.

The whole block must be at most 4,096 bytes.

### Write files

- Put the task ID in the path of each file that you write.
- Put a `task:` line in each file that you write.
- Put long text in a file under `{root}`. List its path in `read`. Do not put long text in `say`.
