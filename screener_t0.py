"""screener_t0 -- the screening record writer and the decided_by check (004 T007, FR-010, FR-012, FR-014, FR-033).

record() inserts one screen_decisions row and writes one gate-log line with path "screener".
check_decided_by() matches the five forms of FR-010. Standard library plus turn_block only: the hook path
imports this module, so it never imports screener, screener_backend or promptchain.
"""
import math
import re

import rookery
import turn_block

STAGES = ("t0", "t1", "suggest", "hunch", "t2")
CHECK_NAMES = ("injection", "duplicate", "budget", "thread", "gates", "t1", "suggest", "hunch", "t2")
BANDS = ("accept", "review", "escalate", "unset")
ACTIONS = ("none", "release", "quarantine", "hold", "escalate", "refuse")
MODES = ("shadow", "live")

# t0:<check>, screener:<model>, hunch:vote6:<model>, t2:<name>, human:<name>. A model name can hold ":" (tev1:4b).
_DECIDED_BY = re.compile(r"(?:t0:[a-z0-9_-]+|screener:{n}|hunch:vote6:{n}|t2:[A-Za-z0-9][A-Za-z0-9._ -]*|human:{n})".format(
    n=r"[A-Za-z0-9][A-Za-z0-9._:@/-]*"))


def check_decided_by(text) -> bool:
    """True when text has one of the five forms of FR-010."""
    return isinstance(text, str) and _DECIDED_BY.fullmatch(text) is not None


def _token(usage, key):
    value = (usage or {}).get(key)
    if value is not None and (type(value) is not int):
        raise ValueError(f"usage.{key} must be an int or None, not {value!r}")
    return value


def record(conn, *, mail_id, session_id, stage, check_name, mode, decided_by, label=None, failure=None,
           attempt=1, confidence=None, band=None, action="none", detail=None, impl=None, usage=None) -> int:
    """Insert one screen_decisions row, write one gate-log line, return the row id.

    The caller owns the transaction and commits. detail holds a pattern name, a hash or numbers, never a mail body.
    Raises ValueError for a bad value; nothing is written then.
    The gate-log line is written at insert time, not at commit, so commit right after record().
    """
    if (label is None) == (failure is None):
        raise ValueError("a screening record needs exactly one of label and failure")
    for name, value, allowed in (("stage", stage, STAGES), ("check_name", check_name, CHECK_NAMES),
                                 ("mode", mode, MODES), ("action", action, ACTIONS)):
        if value not in allowed:
            raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
    if band is not None and band not in BANDS:
        raise ValueError(f"band {band!r} is not one of {', '.join(BANDS)}")
    if mode == "shadow" and action != "none":
        raise ValueError(f"action must be none in shadow mode, not {action!r}")
    if not check_decided_by(decided_by):
        raise ValueError(f"decided_by {decided_by!r} has no form of FR-010")
    if type(attempt) is not int or attempt < 1:
        raise ValueError(f"attempt must be an int of 1 or more, not {attempt!r}")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError(f"confidence must be a number from 0 to 1 or None, not {confidence!r}")
    if label is not None and (not isinstance(label, str) or not label):
        raise ValueError("label must be non-empty text")
    if detail is not None and (not isinstance(detail, str) or len(detail) > 500):
        raise ValueError("detail must be text of at most 500 characters")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("usage must be a dict or None")
    prompt_tokens, reasoning_tokens = _token(usage, "prompt_tokens"), _token(usage, "reasoning_tokens")
    cur = conn.execute(
        "INSERT INTO screen_decisions(mail_id, session_id, stage, check_name, attempt, label, confidence, band,"
        " mode, action, decided_by, backend_impl, failure, detail, prompt_tokens, reasoning_tokens, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mail_id, session_id, stage, check_name, attempt, label, confidence, band, mode, action, decided_by,
         impl, failure, detail, prompt_tokens, reasoning_tokens, rookery.now()))
    turn_block.log_event(None, "gate", path="screener", session=session_id, msg_id=mail_id, stage=stage,
                         check=check_name, label=label, confidence=confidence, mode=mode, action=action,
                         decided_by=decided_by, failure=failure)
    return cur.lastrowid
