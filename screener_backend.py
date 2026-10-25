#!/usr/bin/env python3
"""The decision backends of the screener (D6, D7). One interface: backend.decide(state, labels, instructions) -> Answer.

PromptChain's decision_step does the work when it imports; a standard-library copy does it when it does not. The
import happens inside system_one() and hunch_vote(), never at module import (F20: it takes 6 s). Any failure raises
BackendError(cause). No think, max_tokens, num_predict or num_ctx key is ever sent (FR-007, FR-034).

Causes: timeout | http <code> | bad-answer | no-backend | unreachable.
ROOKERY_SCREENER_FAKE=<file> replaces both backends with fake(); ROOKERY_SCREENER_NO_PROMPTCHAIN=1 forces the copy."""
import asyncio
import concurrent.futures
import http.client
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import NamedTuple

DEFAULT_BASE_URL = "http://192.168.0.159:8799/llm/ollama"
DEFAULT_MODEL = "tev1:4b"
DEFAULT_TIMEOUT_S = 300.0
HUNCH_VOTES = 6


class Answer(NamedTuple):
    label: str
    confidence: float
    usage: dict
    impl: str


class BackendError(Exception):
    def __init__(self, cause):
        super().__init__(cause)
        self.cause = cause


def _promptchain():
    """The decision_step module, or None when PromptChain is missing or switched off."""
    if os.environ.get("ROOKERY_SCREENER_NO_PROMPTCHAIN"):
        return None
    try:
        return importlib.import_module("promptchain.utils.decision_step")
    except Exception:
        return None


def _questions(question, labels, instructions):
    return {question: {"type": "choice", "instructions": instructions, "criteria": {lb: lb for lb in labels}}}


def _number(x, lo, hi):
    """x as a float when it is a real number (not a bool, not NaN or inf) from lo to hi; else bad-answer."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not lo <= x <= hi:
        raise BackendError("bad-answer")
    return float(x)


def _confidence(ans):
    conf = ans.get("confidence")
    if conf is not None:
        return _number(conf, 0, 1)
    probs = ans.get("probabilities")
    if not isinstance(probs, dict) or len(probs) < 2:
        raise BackendError("bad-answer")
    probs = [_number(p, 0, 1) for p in probs.values()]
    h = -sum(p * math.log(p) for p in probs if p > 0)
    return round(max(0.0, min(1.0, 1 - h / math.log(len(probs)))), 4)


def _answer(reply, question, labels, impl):
    """Validate one reply the way decision_step.normalize_answer does: an answer outside the labels is an error."""
    answers = reply.get("answers") if isinstance(reply, dict) else None
    ans = answers.get(question) if isinstance(answers, dict) else None
    if not isinstance(ans, dict) or "error" in ans or ans.get("choice") not in labels:
        raise BackendError("bad-answer")
    u = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
    usage = {"prompt_tokens": u.get("prompt_tokens", u.get("input_tokens")), "reasoning_tokens": u.get("reasoning_tokens")}
    return Answer(ans["choice"], _confidence(ans), usage, impl)


def _cause_from_decision_error(msg):
    """Map a PromptChain DecisionError message to a cause (the message is all the library gives)."""
    if " exited " in msg:  # a hunch exit code: its stderr may quote an HTTP status, so test this first
        return "unreachable"
    m = re.search(r"HTTP (\d+)", msg)
    if m:
        return "http " + m.group(1)
    if "TimeoutError" in msg or "timed out" in msg:
        return "timeout"
    if "not on PATH" in msg:
        return "no-backend"
    if "no answer from" in msg:
        return "unreachable"
    return "bad-answer"


def _run(coro):
    """Run a coroutine to its result. Inside a running event loop, asyncio.run raises, so use a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class _Backend:
    """decide() never raises anything but BackendError; each subclass writes _decide()."""

    def decide(self, state, labels, instructions, question="route"):
        try:
            return self._decide(state, labels, instructions, question)
        except BackendError:
            raise
        except Exception as e:
            raise BackendError("bad-answer") from e


class _SystemOne(_Backend):
    def __init__(self, cfg):
        self.model = cfg.get("model") or DEFAULT_MODEL
        self.base = (cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)

    def _decide(self, state, labels, instructions, question):
        qs = _questions(question, labels, instructions)
        pc = _promptchain()
        if pc is not None:
            return self._via_promptchain(pc, state, qs, question, labels)
        body = json.dumps({"model": self.model, "state": state, "questions": qs}).encode()
        req = urllib.request.Request(self.base + "/v1/systemone", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                reply = json.load(r)
        except urllib.error.HTTPError as e:
            raise BackendError("http %d" % e.code) from e
        except urllib.error.URLError as e:  # every connect failure, a connect timeout too (as PromptChain's message says)
            raise BackendError("unreachable") from e
        except TimeoutError as e:  # a read timeout after the connect
            raise BackendError("timeout") from e
        except (http.client.HTTPException, OSError, ValueError) as e:
            raise BackendError("bad-answer") from e
        return _answer(reply, question, labels, "stdlib")

    def _via_promptchain(self, pc, state, qs, question, labels):
        backend = pc.SystemOneBackend(model=self.model, base_url=self.base, timeout=self.timeout)
        try:
            reply = _run(backend.decide(state, qs))
            pc.normalize_answer(question, qs[question], (reply.get("answers") or {}).get(question))
        except pc.DecisionError as e:
            raise BackendError(_cause_from_decision_error(str(e))) from e
        return _answer(reply, question, labels, "promptchain")


class _Hunch(_Backend):
    def __init__(self, cfg):
        h = cfg.get("hunch") or {}
        self.model = h.get("model") or "qwen2.5:3b"
        self.url = h.get("url")  # None: hunch keeps its own default (HUNCH_URL)
        self.timeout = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)

    def _decide(self, state, labels, instructions, question):
        qs = _questions(question, labels, instructions)
        pc = _promptchain()
        if pc is not None:
            return self._via_promptchain(pc, state, qs, question, labels)
        if shutil.which("hunch") is None:
            raise BackendError("no-backend")
        argv = ["hunch", "-", "-q", json.dumps(qs), "--raw", "--timeout", str(self.timeout)]
        env = {**os.environ, "HUNCH_MODEL": self.model, "HUNCH_VOTE": str(HUNCH_VOTES)}
        if self.url:
            env["HUNCH_URL"] = self.url
        try:
            p = subprocess.run(argv, input=state.encode(), capture_output=True, env=env, timeout=self.timeout + 30)
        except subprocess.TimeoutExpired as e:
            raise BackendError("timeout") from e
        except OSError as e:
            raise BackendError("no-backend") from e
        if p.returncode != 0:
            raise BackendError("unreachable")
        try:
            reply = json.loads(p.stdout)
        except ValueError as e:
            raise BackendError("bad-answer") from e
        return _answer(reply, question, labels, "stdlib")

    def _via_promptchain(self, pc, state, qs, question, labels):
        backend = pc.hunch_backend(self.model, vote=HUNCH_VOTES, timeout=self.timeout)
        if self.url:
            backend.extra_env["HUNCH_URL"] = self.url  # CliBackend merges extra_env into the child's environment
        try:
            reply = _run(backend.decide(state, qs))
            pc.normalize_answer(question, qs[question], (reply.get("answers") or {}).get(question))
        except pc.DecisionError as e:
            raise BackendError(_cause_from_decision_error(str(e))) from e
        return _answer(reply, question, labels, "promptchain")


class _Fake(_Backend):
    def __init__(self, path, kind):
        self.path, self.kind = path, kind

    def _decide(self, state, labels, instructions, question):
        try:
            with open(self.path + ".calls", "a") as fh:
                fh.write(json.dumps({"kind": self.kind, "question": question}) + "\n")
            with open(self.path) as fh:
                book = json.load(fh)
            rules = book.get(self.kind, [])
        except (OSError, ValueError, AttributeError) as e:
            raise BackendError("no-backend") from e
        rule = next((r for r in rules if r.get("match", "") in state), None)
        if rule is None:
            raise BackendError("bad-answer")
        if rule.get("fail"):
            raise BackendError(rule["fail"])
        if rule.get("label") not in labels:
            raise BackendError("bad-answer")
        conf = _confidence({"confidence": rule.get("confidence", 1.0)})
        return Answer(rule["label"], conf, {"prompt_tokens": None, "reasoning_tokens": None}, "fake")


def fake(path, kind="t1"):
    return _Fake(path, kind)


def system_one(cfg):
    path = os.environ.get("ROOKERY_SCREENER_FAKE")
    return fake(path, "t1") if path else _SystemOne(cfg or {})


def hunch_vote(cfg):
    path = os.environ.get("ROOKERY_SCREENER_FAKE")
    return fake(path, "hunch") if path else _Hunch(cfg or {})
