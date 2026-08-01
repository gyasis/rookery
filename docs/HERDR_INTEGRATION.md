# Rookery × herdr — the console over the mesh

Status: **design note**, nothing implemented yet. Written 2026-08-01.

[herdr](https://herdr.dev) is a terminal workspace manager for coding agents — a
mouse-first multiplexer whose distinguishing feature is that it *recognizes*
agents inside panes and reports each one's lifecycle state (`working`, `blocked`,
`done`, `idle`, `unknown`) in a sidebar. A background server owns the panes, so
they survive detach, terminal close, and SSH disconnect.

This note argues herdr is the right **presentation layer** for a Rookery mesh,
fixes the mechanism boundary between the two, and lists the work to wire them up.

## The mechanism boundary: who is driving, not how far away

The README warns against reaching for "instant-delivery machinery (blocking
waits, or typing into another agent's terminal via tmux)". That is easy to
misread as *pane injection is for local, mail is for remote*. It isn't a distance
rule. Even with two agents in two panes on one laptop, mail is the better
agent-to-agent substrate:

- **Injection is lossy both ways.** You send keystrokes and read back scraped
  screen text — no structured payload, no return value. herdr's own skill file
  cautions that `unknown` does not mean done, and that `idle` vs `done` depends
  on whether a tab was *seen* in the UI. Coordination logic on top of that is
  guessing.
- **No backpressure.** Keys typed into an agent mid-turn interleave with whatever
  it is doing. Mail is claimed when the recipient drains its inbox, on its own
  schedule.
- **Nothing survives.** If the target pane exited, crashed, or sits at an
  unexpected prompt, the keystrokes are gone. Mail persists in the mailroom until
  claimed, and the mailroom doubles as a queryable audit log.
- **The target must be alive and warm.** Injection cannot address a sleeping
  node; the postmaster wakes one from $0.
- **No identity.** Any pane may drive any pane, unauthenticated — versus Ed25519
  identity, signed cards, and per-target authorization.

The rule that actually holds:

| Direction | Mechanism |
|---|---|
| **Human → agent** | pane injection — steering, interrupting, pairing |
| **Agent → agent** | mail — regardless of distance |

Rookery already encodes this. `terminal_node.py` (substrate A) is described as
the path by which "a human/terminal-agent **joins**" the mesh — injection as the
human on-ramp, not as the agent-to-agent channel. Nothing here changes the
model; it names the boundary explicitly so the herdr work does not blur it.

## Why herdr specifically, and not just a fourth multiplexer

`terminal_node.py` already treats pane control as pluggable across tmux, wezterm,
and zellij. herdr could be a fourth entry and stop there — but it would waste the
one thing herdr has that the others do not: **agent state**.

The other three multiplexers know they own a pane. herdr knows the pane contains
a *coding agent* and what that agent is doing. That maps directly onto mesh
state Rookery already tracks:

| Rookery state | herdr surface |
|---|---|
| node paused on `NEEDCRED`, awaiting approval | agent shows `blocked` |
| node woken by postmaster, working a turn | agent shows `working` |
| node drained inbox, turn complete | agent shows `done` / `idle` |
| node reaped (ephemeral, or evicted from warm pool) | pane gone or back to a shell |

The alignment worth building on is **`blocked` ↔ `NEEDCRED`**. A credential
phone-home is precisely "an agent is stuck waiting on a human," which is the
question herdr's sidebar exists to answer. Today a `credential_requests` row is
visible only if you are watching a log; through herdr it becomes a visible,
attention-grabbing state across every workspace on the box.

## Proposed shape

**herdr is the console. Rookery is the network.** Every agent gets a mailroom
identity; herdr renders state and gives the human a keyboard. `herdr agent
prompt` is reserved for *you* — agents talk to each other over mail.

```
        ┌──────────── mailroom.db (SQLite) ────────────┐
        │  nodes · inbox · credential_requests          │
        └───▲──────────────────────────────┬───────────┘
   postmaster poll (non-LLM, ~$0)          │ notification bridge
            │ wakes node on real mail      ▼
   ┌────────┴─────────┐        herdr notification show
   │ headless nodes   │        ┌──────────────────────┐
   │ (no pane, $0)    │        │ herdr sidebar        │
   └──────────────────┘        │  architect  blocked  │ ← NEEDCRED
                               │  reviewer   working  │
   ┌──────────────────┐        │  postmaster idle     │
   │ attended nodes   │◀───────└──────────────────────┘
   │ (herdr panes)    │  human steers via pane injection
   └──────────────────┘
```

Allocation rule: **ephemeral nodes get no pane** — they are headless invocations
that exit, and giving them panes fights the $0-when-idle model. Panes are for the
postmaster, persistent partners, and anything a human wants to watch or join.

## Work items

Roughly ordered by value-to-effort.

1. **`--injector herdr` in `terminal_node.py`.** The smallest useful change: one
   branch in `inject()` and one entry in the `--injector` choices. Delivery is
   `herdr agent prompt <target> <text>` for an agent-occupied pane. Note this
   differs from the other three injectors — herdr submits a *prompt* to a
   recognized agent rather than typing raw characters, so it does not need a
   separate Enter keystroke.

2. **Notification bridge.** Postmaster calls `herdr notification show` on new
   mail and on `NEEDCRED`. This is the highest-value item: it turns the approval
   queue from something you poll into something that finds you. Must degrade
   silently when herdr is absent — the bridge is optional, never a dependency.

3. **Pane ↔ node binding.** `herdr agent list` returns JSON; use it to enroll
   panes as mesh nodes, so a node's identity and its pane are the same thing.
   Enables `herdr agent focus <node>` to jump to whichever agent is blocked.

4. **Approval surface.** With 2 and 3, `NEEDCRED` becomes: notification fires →
   sidebar shows `blocked` → focus the pane → `rookery approve`. Worth a demo
   script (`demo_herdr.sh`) in the style of the existing ones.

5. **`HERDR_ENV` gating.** herdr sets `HERDR_ENV=1` inside its panes and blocks
   nested launches. Any herdr-aware code must check it and no-op outside, the way
   herdr's own skill file requires.

### Not proposed

Replacing the tmux/wezterm/zellij injectors, or making herdr a dependency. The
core stays Python-stdlib-only and multiplexer-agnostic; herdr is one more
optional substrate that happens to carry richer state.

## Known interaction: the SessionStart hook tax

The README records that plain `claude -p` pays **~115s per call** in this
environment because of the user's `SessionStart` hooks, and that the fix is a
warm Agent SDK session with `setting_sources=[]`.

**herdr's Claude integration installs another `SessionStart` hook**
(`~/.claude/hooks/herdr-agent-state.sh`, wired into `~/.claude/settings.json`,
10s timeout). Every ephemeral `claude` node the postmaster spawns now pays it on
top of whatever was already there.

Mitigations, both already in the design:

- `engine=claude-sdk` for persistent partners — `setting_sources=[]` skips hooks
  entirely, so the tax does not apply where it would hurt most.
- Accept the hook only for attended panes, where herdr state is what you are
  paying for.

This should be **measured** before any large fan-out; the number above is
inferred from the two designs, not benchmarked together.
