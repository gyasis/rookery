#!/usr/bin/env python3
"""msg_relay.py — branch/commit-message relay for agent-to-agent messaging.

Approach (a): ONE-BRANCH-PER-CONVERSATION.
Each conversation gets its own git branch, ``conv/<conversation-id>``. Every
message is a commit on that branch; the commit body carries a JSON envelope.
Two agents round-trip by appending commits to the same branch.

Envelope fields (exact, no extras)::

    {
      "from":        sender slug,
      "to":          recipient slug,
      "type":        message type (e.g. "request", "reply", "message"),
      "payload":     message body (string),
      "timestamp":   ISO-8601 UTC timestamp,
      "message_id":  stable unique id for the message
    }

This is the git-native transport only. It does NOT replace rookery's durable
SQLite mailroom; it is a lightweight, branch-scoped channel where the commit
history *is* the conversation log.

Approach (b) — a single shared branch with a commit stream — is deferred to a
follow-up task.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

ENVELOPE_FIELDS = ("from", "to", "type", "payload", "timestamp", "message_id")


@dataclass
class Envelope:
    """A single relayed message."""

    frm: str
    to: str
    type: str
    payload: str
    timestamp: str
    message_id: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Envelope":
        data = json.loads(raw)
        # Reject envelopes that carry fields we don't know about.
        unknown = set(data) - set(ENVELOPE_FIELDS)
        if unknown:
            raise ValueError(f"unknown envelope fields: {sorted(unknown)}")
        missing = set(ENVELOPE_FIELDS) - set(data)
        if missing:
            raise ValueError(f"missing envelope fields: {sorted(missing)}")
        return cls(**{f: data[f] for f in ENVELOPE_FIELDS})


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #
def _git(args: List[str], repo: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


def _branch_name(conversation_id: str) -> str:
    safe = conversation_id.strip().strip("/")
    return f"conv/{safe}"


def _ensure_branch(branch: str, repo: str) -> None:
    """Create ``branch`` from HEAD if it does not already exist."""
    probe = _git(["rev-parse", "--verify", branch], repo, check=False)
    if probe.returncode != 0:
        _git(["branch", branch], repo)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def write_msg(
    conversation_id: str,
    sender: str,
    recipient: str,
    payload: str,
    *,
    msg_type: str = "message",
    repo: str = ".",
    message_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """Post a message as an empty commit on the conversation branch.

    Returns the ``message_id`` of the committed envelope.
    """
    message_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    env = Envelope(
        frm=sender,
        to=recipient,
        type=msg_type,
        payload=payload,
        timestamp=timestamp,
        message_id=message_id,
    )
    branch = _branch_name(conversation_id)
    _ensure_branch(branch, repo)
    _git(["checkout", branch], repo)
    subject = f"[{msg_type}] {sender} -> {recipient}: {message_id}"
    _git(["commit", "--allow-empty", "-m", subject, "-m", env.to_json()], repo)
    return message_id


def read_msg(
    conversation_id: str,
    *,
    repo: str = ".",
    after: Optional[str] = None,
) -> List[Tuple[str, Envelope]]:
    """Read messages from a conversation branch, oldest-first.

    Each entry is ``(commit_sha, Envelope)``. Pass ``after`` (a commit sha) to
    read only messages newer than that commit.
    """
    branch = _branch_name(conversation_id)
    # %H  = full sha
    # %x00 = NUL separator between sha and body
    # %B  = raw body (subject + body)
    # %x1e = record separator between commits
    fmt = "%H%x00%B%x1e"
    args = ["log", branch, f"--format={fmt}"]
    if after:
        args += [f"{after}..{branch}"]
    result = _git(args, repo, check=False)
    if result.returncode != 0:
        return []
    messages: List[Tuple[str, Envelope]] = []
    for record in result.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, _, body = record.partition("\x00")
        # The envelope is the second paragraph of the commit body (after the
        # subject line + blank line).
        parts = body.split("\n\n", 1)
        env_raw = parts[1] if len(parts) == 2 else body
        try:
            env = Envelope.from_json(env_raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        messages.append((sha.strip(), env))
    messages.reverse()  # git log is newest-first; we want oldest-first
    return messages


# --------------------------------------------------------------------------- #
# demo: round-trip between two simulated agents
# --------------------------------------------------------------------------- #
def demo(repo: str = ".") -> None:
    """Run a round-trip: agent-A writes, agent-B reads and replies."""
    conv = f"demo-{uuid.uuid4().hex[:6]}"
    print(f"== conversation branch: conv/{conv} ==")

    a = write_msg(conv, "agent-a", "agent-b",
                  "ping — are you there?", msg_type="request", repo=repo)
    print(f"agent-a posted {a}")

    inbound = read_msg(conv, repo=repo)
    print(f"agent-b read {len(inbound)} message(s):")
    for sha, env in inbound:
        print(f"  {sha[:8]} {env.frm} -> {env.to} [{env.type}] {env.payload!r}")

    # agent-B replies on the same branch
    b = write_msg(conv, "agent-b", "agent-a",
                  "pong — yes, here", msg_type="reply", repo=repo)
    print(f"agent-b posted {b}")

    full = read_msg(conv, repo=repo)
    print(f"full conversation ({len(full)} messages):")
    for sha, env in full:
        print(f"  {sha[:8]} {env.frm} -> {env.to} [{env.type}] {env.payload!r}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="branch/commit-message relay")
    p.add_argument("--repo", default=os.getcwd(), help="repo path (default cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="post a message")
    w.add_argument("--conversation", required=True)
    w.add_argument("--from", dest="sender", required=True)
    w.add_argument("--to", dest="recipient", required=True)
    w.add_argument("--payload", required=True)
    w.add_argument("--type", default="message")

    r = sub.add_parser("read", help="read messages")
    r.add_argument("--conversation", required=True)

    sub.add_parser("demo", help="run a round-trip demo")

    args = p.parse_args(argv)

    if args.cmd == "write":
        mid = write_msg(args.conversation, args.sender, args.recipient,
                        args.payload, msg_type=args.type, repo=args.repo)
        print(mid)
    elif args.cmd == "read":
        for sha, env in read_msg(args.conversation, repo=args.repo):
            print(f"{sha[:8]} {env.frm} -> {env.to} [{env.type}] {env.message_id}")
            print(env.to_json())
    elif args.cmd == "demo":
        demo(repo=args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())