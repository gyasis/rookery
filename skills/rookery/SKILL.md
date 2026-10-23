---
name: rookery
description: "How to read and send mail in a Rookery 0.7 session. Use when a line starts with `[rookery] new mail for`, or when the user mentions Rookery, a Rookery session, or handing work to another agent in the session. Do NOT use merely because a task could involve multiple agents or background work."
---

# Rookery (0.7)

This skill is the one source of Rookery instructions for agents. Rookery gives agents durable mail. The project section in CLAUDE.md or AGENTS.md has the same rules.

## When this applies

Run `rookery identity show`. It prints who you are.

If it prints no session, Rookery mail is not on for you. Do nothing with Rookery unless the user asks.

Take your name from Rookery (`identity show` or the session start). Never guess a name.

## Read mail

1. Run `rookery inbox`. It changes nothing.
2. Act on the mail.
3. Run the command on the last line of the output: `rookery ack ID ...`.

Run `rookery peek` to look at your mail and change nothing.

In an MCP session, the `check_inbox` tool also marks nothing read. Call the `ack` tool with the IDs it lists. This is the same as `rookery ack ID ...`.

## Send mail

End your FINAL message of the turn with one fenced block. The info string is `rookery`. The block holds one JSON object. Put nothing after it.

- No block means Rookery sends no mail.
- Do not send a block only to say that you read mail. Run `rookery ack` instead.
- You must set these fields: `to`, `task`, `kind` (`ask`, `answer`, `decline`, `handoff`, `status`, `recruit`), `say`, `status` (`done` or `in-progress`).
- Optional fields: `read` (a list of file paths in the Rookery root) and `thread`.
- Put long text in a file under the Rookery root. List its path in `read`.
- Put the task ID in the path of each file you write, and a `task:` line inside it.

To send one mail now, outside the end of a turn, run `rookery send TO "SAY" --task TASK --kind KIND`. It goes through the same checks as a block. Rookery takes the sender from your identity. There is no `--as`. Exit 0 means sent. Exit 1 means not sent, and the reason is on stderr.

On a long task, end each turn with `kind` `handoff` and `status` `in-progress`. This updates HANDOFF.md and sends no mail. When you finish the task, use `status` `done`. Rookery then sends the hand-off mail.

## Never poll

Do not loop. Do not sleep. Do not schedule inbox checks.

The postmaster pushes a line that starts with `[rookery] new mail for` into an idle agent when mail needs action. When you see it, read your mail as above.

Mail of kind `status`, and most mail of kind `answer`, waits for your next turn. Nothing is lost.

## Loop breaker

Rookery stops agent-to-agent loops. The limits are 10 mails for each task and pair, and 30 for each pair. A human prompt resets them.

If a block comes back `loop-held`, stop. Wait for the human.

## Legacy mesh (pre-0.7)

Use this only when there is NO Rookery session and the user works with the old node mesh or `mailctl`. The old mesh has a different design. See `docs/NODE_BRIEFING.md`. These old commands are legacy. Each has a `rookery` command:

- `send_mail.py` is legacy. Use `rookery send TO "SAY"`.
- `read_mail.py` is legacy. Use `rookery inbox`, then `rookery ack ID ...`.
- `mailctl.py` is legacy. Use `rookery send`, `rookery inbox` and `rookery ack`.

Do not use old mesh habits in a 0.7 session.
