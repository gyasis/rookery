# Rookery × herdr — the console over the mesh

Status: **shipped as an optional plugin**. Written as a design note 2026-08-01;
the five work items below landed 2026-08-03 as `plugin_herdr/` plus a core
plugin registry (`plugins.py`) and `demo_herdr.sh`. Core has no herdr
dependency in either direction.

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

## Shape: a plugin, not an integration

Rookery is a **standalone messenger**. It has to build, run and pass its suite
with no console attached and no knowledge of what a console does — so the
dependency points one way only:

```
    core  <--imports--  plugin_herdr/        (never the reverse)
```

Core exposes three extension points in **`plugins.py`** and nothing herdr-shaped:

| Extension point | Contract | Used by |
|---|---|---|
| `injector(name, send, check)` | `send(target, text) -> bool` | `terminal_node.py --injector` |
| `sink(fn)` | `fn(event, title, body, **meta) -> bool` | `postmaster.py --notify` |
| `command(fn)` | `fn(subparsers)` | `rookery <subcommand>` |

Plugins are **discovered, not named**: `plugins.load()` imports any `plugin_*`
module or package sitting beside it. Grep core for "herdr" and you get nothing.
`ROOKERY_PLUGINS=a,b` pins an explicit list, `ROOKERY_PLUGINS=` disables all,
`ROOKERY_PLUGINS_DEBUG=1` explains why one did not attach.

The whole herdr surface is three files in one directory:

```
plugin_herdr/__init__.py   attachment points, nothing else
plugin_herdr/bridge.py     the herdr CLI wrapper (every subprocess call)
plugin_herdr/cli.py        the `rookery herdr ...` subcommands
```

**Delete that directory and Rookery is unchanged** — suite green, `rookery
--help` no longer mentions herdr, `--injector` back to `{tmux,wezterm,zellij}`.
Verified by doing exactly that, not by inspection.

`register()` self-gates on the herdr binary: an install that ships the plugin
without herdr present is indistinguishable from one that never had it.
Everything in `bridge.py` degrades silently besides — no binary, no server, or
running outside a pane makes every call a no-op returning `None`/`False`/`[]`.
The single exception is `require()`, used where the operator explicitly named
herdr and a silent no-op would be a lie.

## Work items — all shipped 2026-08-03

1. **`--injector herdr`.** ✅ `terminal_node.py` keeps its three built-ins and
   looks anything else up in the registry, so the choices list grows only when
   a plugin is attached. Delivery is `herdr agent prompt <target> <text>`, and
   `--target` accepts a pane id *or* an agent name.
   Unlike the other three injectors it submits a *prompt* to a recognized
   agent, so there is no separate Enter keystroke. Because this is an explicit
   opt-in, it calls `require()` and fails loudly rather than no-opping — mail
   is acked either way, and silently-undelivered mail is unrecoverable.

2. **Notification bridge.** ✅ `postmaster.py --notify {needcred,all,none}`.
   Core calls `plugins.notify("needcred", …)` and counts how many sinks fired;
   it never mentions herdr, and zero sinks is the normal standalone case rather
   than a degraded one. Default is `needcred`, **not** "every new mail": under
   a fan-out the postmaster wakes a node per message, so `all` is one alert per wake. The
   `NEEDCRED` half is the value — it turns the approval queue from something
   you poll into something that finds you. The toast carries the resource, the
   node's pane if it has one, and the exact approve command.

3. **Pane ↔ node binding.** ✅ `rookery herdr bind [--sync-status]` reads
   `herdr agent list` and enrolls **named** panes (`herdr agent rename <pane>
   <node-id>`) as mesh nodes, writing the pane into `nodes.address`. Only named
   panes bind — guessing an identity from a terminal title would bind the wrong
   agent. `--sync-status` mirrors herdr's lifecycle onto `nodes.status`
   (`blocked` → `waiting`); `unknown` never overwrites, since herdr's own docs
   warn it does not mean done.

4. **Approval surface.** ✅ `rookery herdr status` puts the console and the
   mailroom side by side — pane, herdr state, node, mesh status, pending mail —
   then lists every `NEEDCRED` with its pane and the two commands that resolve
   it (`rookery herdr focus <node>`, `mesh_approve.py --id N --approve`). It
   also lists mesh nodes with *no* pane, which is the allocation rule made
   visible rather than a gap. `demo_herdr.sh` runs the whole loop on the mock
   engine ($0), showing both an ephemeral node (toast only) and an attended one
   (toast + jump).

5. **`HERDR_ENV` gating.** ✅ `ROOKERY_HERDR=auto|off|force`. `auto` (default)
   requires both the binary and `HERDR_ENV=1`, i.e. we are inside a herdr pane.
   `force` is for a mesh process that legitimately lives outside one (launchd,
   systemd, ssh) but still drives the console. `HERDR_PANE_ID` is exposed as
   `self_pane()` — a node started in a pane can learn its own address without
   being told.

### Why binding turned out to be more than convenience

The first dogfood run hit this: restarting a mesh agent **wiped its briefing
while its mail survived**. It came back not knowing which node it was, reported
an empty inbox it could no longer read, and mistook another pane for its peer —
while its actual mail sat unread. An identity that lives in the pane survives a
restart of whatever is *inside* the pane, which is exactly what item 3 buys.
The remaining half — making the briefing itself recoverable from the mesh
rather than from a pane's context — is not solved here.

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
