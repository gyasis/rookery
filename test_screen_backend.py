#!/usr/bin/env python3
"""The screener decision backends (T005): contract on both paths, request body, hunch vote, fake, errors, lazy import.

    python3 -m unittest test_screen_backend -v

A local http.server on 127.0.0.1 stands in for /v1/systemone; fixtures/bin/hunch stands in for hunch. No model is called.
The promptchain half of the contract skips (skipTest) with a message when the import fails.
"""
import asyncio
import json
import math
import os
import http.client
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import screener_backend as SB

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = ["deliver", "task-already-done", "wrong-recipient", "unneeded"]
BAD_KEYS = ("think", "max_tokens", "max_completion_tokens", "num_predict", "num_ctx")


class _Handler(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.bodies.append(body)
        state = body["state"]
        if "CASE-500" in state:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        if "CASE-SLOW" in state:
            time.sleep(3)
        label = "not-a-label" if "CASE-BAD" in state else "deliver"
        if "CASE-LIST" in state:
            data = b"[]"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        out = {"model": body["model"], "usage": {"input_tokens": 120, "output_tokens": 3},
               "answers": {"route": {"type": "choice", "choice": label, "confidence": 0.9,
                                     "probabilities": {"deliver": 0.9, "unneeded": 0.1}}}}
        for case, conf in (("CASE-CONF5", 5), ("CASE-CONFNAN", float("nan")), ("CASE-CONFTRUE", True)):
            if case in state:
                out["answers"]["route"]["confidence"] = conf
        if "CASE-NEGPROB" in state:
            out["answers"]["route"] = {"type": "choice", "choice": "deliver", "probabilities": {"deliver": 2, "unneeded": -1}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "hunch.log")
        self.rules = os.path.join(self.tmp.name, "rules.json")
        rules = {"hunch": [
            {"match": "CASE-GOOD", "label": "deliver", "confidence": 0.9},
            {"match": "CASE-ERR", "fail": "error", "label": "deliver"},
            {"match": "CASE-EXIT1", "fail": "exit1", "label": "deliver"},
            {"match": "CASE-BAD", "fail": "bad-answer", "label": "deliver"}]}
        with open(self.rules, "w") as fh:
            json.dump(rules, fh)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ROOKERY_SCREENER", "HUNCH"))}
        env.update(PATH=os.path.join(HERE, "fixtures", "bin") + os.pathsep + os.environ["PATH"],
                   ROOKERY_FAKE_HUNCH=self.rules, ROOKERY_FIXTURE_LOG=self.log)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        _Handler.bodies.clear()

    def cfg(self, timeout=30, **extra):
        return {"base_url": self.url, "model": "tev1:4b", "timeout_s": timeout, "hunch": {"model": "qwen2.5:3b"}, **extra}

    def need_promptchain(self):
        if SB._promptchain() is None:
            self.skipTest("promptchain.utils.decision_step does not import under %s" % sys.executable)

    def impls(self):
        yield "stdlib", {"ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}
        if SB._promptchain() is None:
            yield "promptchain", None
        else:
            yield "promptchain", {}

    def run_both(self, make, state, timeout=30, **extra):
        """Run one fixture on both paths; return {impl: Answer or cause}."""
        out = {}
        for name, env_extra in self.impls():
            if env_extra is None:
                out[name] = None
                continue
            with mock.patch.dict(os.environ, env_extra):
                try:
                    out[name] = make(self.cfg(timeout, **extra)).decide(state, LABELS, "Say where the mail goes.")
                except SB.BackendError as e:
                    out[name] = e.cause
        return out

    def same(self, results):
        got = {k: (v if isinstance(v, str) else (v.label, v.confidence)) for k, v in results.items() if v is not None}
        self.assertEqual(len(set(map(str, got.values()))), 1, got)
        return next(iter(got.values()))

    def test_good_answer_systemone(self):
        r = self.run_both(SB.system_one, "CASE-GOOD")
        self.assertEqual(r["stdlib"].impl, "stdlib")
        self.assertEqual(r["stdlib"].usage, {"prompt_tokens": 120, "reasoning_tokens": None})
        self.need_promptchain()
        self.assertEqual(self.same(r), ("deliver", 0.9))
        self.assertEqual(r["promptchain"].impl, "promptchain")
        self.assertEqual(r["promptchain"].usage, r["stdlib"].usage)

    def test_good_answer_hunch(self):
        r = self.run_both(SB.hunch_vote, "CASE-GOOD")
        self.assertEqual((r["stdlib"].label, r["stdlib"].confidence), ("deliver", 0.9))
        self.need_promptchain()
        self.assertEqual(self.same(r), ("deliver", 0.9))

    def test_http_500(self):
        r = self.run_both(SB.system_one, "CASE-500")
        self.assertEqual(r["stdlib"], "http 500")
        self.need_promptchain()
        self.assertEqual(self.same(r), "http 500")

    def test_timeout(self):
        t0 = time.time()
        r = self.run_both(SB.system_one, "CASE-SLOW", timeout=1)
        self.assertEqual(r["stdlib"], "timeout")
        self.assertLess(time.time() - t0, 10)
        self.need_promptchain()
        self.assertEqual(self.same(r), "timeout")  # a read timeout after the connect: the same cause on both paths

    def test_unreachable(self):
        with mock.patch.dict(os.environ, {"ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}):
            cfg = {**self.cfg(), "base_url": "http://127.0.0.1:1"}
            with self.assertRaises(SB.BackendError) as cm:
                SB.system_one(cfg).decide("CASE-GOOD", LABELS, "x")
        self.assertEqual(cm.exception.cause, "unreachable")

    def test_answer_outside_labels(self):
        a, b = self.run_both(SB.system_one, "CASE-BAD"), self.run_both(SB.hunch_vote, "CASE-BAD")
        self.assertEqual((a["stdlib"], b["stdlib"]), ("bad-answer", "bad-answer"))
        self.need_promptchain()
        self.assertEqual(self.same(a), "bad-answer")
        self.assertEqual(self.same(b), "bad-answer")

    def test_hunch_error_and_exit1(self):
        a, b = self.run_both(SB.hunch_vote, "CASE-ERR"), self.run_both(SB.hunch_vote, "CASE-EXIT1")
        self.assertEqual((a["stdlib"], b["stdlib"]), ("bad-answer", "unreachable"))
        self.need_promptchain()
        self.assertEqual(self.same(a), "bad-answer")
        self.assertEqual(self.same(b), "unreachable")

    def test_hunch_not_on_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent", "ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}):
            with self.assertRaises(SB.BackendError) as cm:
                SB.hunch_vote(self.cfg()).decide("CASE-GOOD", LABELS, "x")
        self.assertEqual(cm.exception.cause, "no-backend")

    def test_body_has_model_state_choice_and_no_limit_keys(self):
        for name, extra in self.impls():
            if extra is None:
                continue
            _Handler.bodies.clear()
            with mock.patch.dict(os.environ, extra):
                SB.system_one(self.cfg()).decide("CASE-GOOD state", LABELS, "Say where the mail goes.")
            body = _Handler.bodies[0]
            self.assertEqual(set(body), {"model", "state", "questions"}, name)
            self.assertEqual((body["model"], body["state"]), ("tev1:4b", "CASE-GOOD state"), name)
            self.assertEqual(list(body["questions"]), ["route"], name)
            q = body["questions"]["route"]
            self.assertEqual(q["type"], "choice")
            self.assertEqual(list(q["criteria"]), LABELS)
            self.assertTrue(q["instructions"])
            self.assertFalse([k for k in BAD_KEYS if k in json.dumps(body)], name)
        self.need_promptchain()

    def test_hunch_vote_is_six(self):
        for name, extra in self.impls():
            if extra is None:
                continue
            open(self.log, "w").close()
            with mock.patch.dict(os.environ, extra):
                SB.hunch_vote(self.cfg()).decide("CASE-GOOD", LABELS, "x")
            with open(self.log) as fh:
                rec = json.loads(fh.readlines()[-1])
            self.assertEqual(rec["HUNCH_VOTE"], "6", name)
            self.assertEqual(rec["HUNCH_MODEL"], "qwen2.5:3b", name)
            self.assertFalse(any(k in " ".join(rec["argv"]) for k in BAD_KEYS), name)
        self.need_promptchain()

    def test_hunch_url_is_passed_when_set(self):
        for name, extra in self.impls():
            if extra is None:
                continue
            for url, want in (("http://127.0.0.1:9/llm/x", "http://127.0.0.1:9/llm/x"), (None, None)):
                open(self.log, "w").close()
                with mock.patch.dict(os.environ, extra):
                    SB.hunch_vote(self.cfg(hunch={"model": "qwen2.5:3b", "url": url})).decide("CASE-GOOD", LABELS, "x")
                with open(self.log) as fh:
                    self.assertEqual(json.loads(fh.readlines()[-1])["HUNCH_URL"], want, (name, url))
        self.need_promptchain()

    def test_connect_failure_same_cause_on_both_paths(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now: a refused connect
        r = self.run_both(SB.system_one, "CASE-GOOD", timeout=3, base_url="http://127.0.0.1:%d" % port)
        self.assertEqual(r["stdlib"], "unreachable")
        self.need_promptchain()
        self.assertEqual(self.same(r), "unreachable")

    def test_stdlib_maps_connect_timeout_to_unreachable_and_read_timeout_to_timeout(self):
        import urllib.error
        with mock.patch.dict(os.environ, {"ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}):
            for exc, cause in ((urllib.error.URLError(TimeoutError("timed out")), "unreachable"),
                               (urllib.error.URLError(OSError("no route")), "unreachable"),
                               (TimeoutError("timed out"), "timeout")):
                with mock.patch("urllib.request.urlopen", side_effect=exc):
                    with self.assertRaises(SB.BackendError) as cm:
                        SB.system_one(self.cfg()).decide("x", LABELS, "i")
                self.assertEqual(cm.exception.cause, cause, repr(exc))

    def test_promptchain_path_inside_a_running_event_loop(self):
        self.need_promptchain()

        async def inside():
            return SB.system_one(self.cfg()).decide("CASE-GOOD", LABELS, "i")
        a = asyncio.run(inside())
        self.assertEqual((a.label, a.impl), ("deliver", "promptchain"))

    def test_odd_replies_are_bad_answer_on_both_paths(self):
        for state in ("CASE-LIST", "CASE-CONF5", "CASE-CONFNAN", "CASE-CONFTRUE", "CASE-NEGPROB"):
            r = self.run_both(SB.system_one, state)
            self.assertEqual(r["stdlib"], "bad-answer", state)
            if r["promptchain"] is not None:
                self.assertEqual(r["promptchain"], "bad-answer", state)
        self.need_promptchain()


class FakeBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "fake.json")
        with open(self.path, "w") as fh:
            json.dump({"t1": [{"match": "A", "label": "unneeded", "confidence": 0.8}, {"match": "T", "fail": "timeout"}],
                       "hunch": [{"match": "A", "label": "deliver"}]}, fh)
        p = mock.patch.dict(os.environ, {"ROOKERY_SCREENER_FAKE": self.path})
        p.start()
        self.addCleanup(p.stop)

    def test_no_socket_no_subprocess_and_calls_counted(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("socket")), \
                mock.patch("subprocess.run", side_effect=AssertionError("subprocess")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess")):
            a = SB.system_one({}).decide("xAx", LABELS, "i")
            b = SB.hunch_vote({}).decide("xAx", LABELS, "i")
        self.assertEqual((a.label, a.confidence, a.impl), ("unneeded", 0.8, "fake"))
        self.assertEqual((b.label, b.confidence), ("deliver", 1.0))
        with open(self.path + ".calls") as fh:
            self.assertEqual(len(fh.readlines()), 2)

    def test_fail_rule_and_no_match_raise(self):
        with self.assertRaises(SB.BackendError) as cm:
            SB.system_one({}).decide("T", LABELS, "i")
        self.assertEqual(cm.exception.cause, "timeout")
        with self.assertRaises(SB.BackendError) as cm:
            SB.system_one({}).decide("zzz", LABELS, "i")
        self.assertEqual(cm.exception.cause, "bad-answer")

    def test_missing_or_corrupt_book_or_unwritable_calls_is_no_backend(self):
        def cause(path=None):
            with self.assertRaises(SB.BackendError) as cm:
                SB.fake(path or self.path).decide("A", LABELS, "i")
            return cm.exception.cause
        self.assertEqual(cause(os.path.join(self.tmp.name, "missing.json")), "no-backend")
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(cause(), "no-backend")
        with open(self.path, "w") as fh:
            json.dump([], fh)  # a book that is not an object
        self.assertEqual(cause(), "no-backend")
        os.remove(self.path + ".calls")
        os.makedirs(self.path + ".calls")  # a folder where the calls log goes: not writable
        self.assertEqual(cause(), "no-backend")

    def test_fake_bad_confidence_is_bad_answer(self):
        for conf in ("x", 7, True, [1]):
            with open(self.path, "w") as fh:
                json.dump({"t1": [{"match": "A", "label": "unneeded", "confidence": conf}]}, fh)
            with self.assertRaises(SB.BackendError) as cm:
                SB.fake(self.path).decide("A", LABELS, "i")
            self.assertEqual(cm.exception.cause, "bad-answer", conf)

    def test_fake_function_directly(self):
        self.assertEqual(SB.fake(self.path).decide("A", LABELS, "i").label, "unneeded")


class Answers(unittest.TestCase):
    """Reply checks and the rule that decide() raises nothing but BackendError."""

    def parse(self, ans, labels=("a", "b")):
        return SB._answer({"answers": {"route": ans}}, "route", list(labels), "x")

    def bad(self, fn):
        with self.assertRaises(SB.BackendError) as cm:
            fn()
        self.assertEqual(cm.exception.cause, "bad-answer")

    def test_confidence_must_be_a_real_number_from_0_to_1(self):
        for conf in (True, float("nan"), float("inf"), -0.1, 1.5, 5, "0.9"):
            self.bad(lambda c=conf: self.parse({"choice": "a", "confidence": c}))
        self.assertEqual(self.parse({"choice": "a", "confidence": 0}).confidence, 0.0)
        self.assertEqual(self.parse({"choice": "a", "confidence": 1}).confidence, 1.0)

    def test_probabilities_must_be_a_dict_of_numbers_from_0_to_1(self):
        for probs in ([0.5, 0.5], {"a": -1, "b": 2}, {"a": float("nan"), "b": 0.5}, {"a": True, "b": 0.5}, {"a": 1}):
            self.bad(lambda p=probs: self.parse({"choice": "a", "probabilities": p}))
        got = self.parse({"choice": "a", "probabilities": {"a": 1.0, "b": 0.0}})
        self.assertTrue(0 <= got.confidence <= 1 and math.isfinite(got.confidence))

    def test_reply_shapes(self):
        for reply in ([], None, {"answers": []}, {"answers": {"route": []}}):
            self.bad(lambda r=reply: SB._answer(r, "route", ["a"], "x"))

    def test_decide_wraps_every_exception(self):
        class Boom(SB._Backend):
            def _decide(self, *a):
                raise http.client.IncompleteRead(b"x")
        self.bad(lambda: Boom().decide("s", LABELS, "i"))
        with mock.patch.dict(os.environ, {"ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}):
            for exc in (http.client.RemoteDisconnected("gone"), http.client.IncompleteRead(b"x")):
                with mock.patch("urllib.request.urlopen", side_effect=exc):
                    self.bad(lambda: SB.system_one({}).decide("s", LABELS, "i"))
        with mock.patch.dict(os.environ, {"ROOKERY_SCREENER_NO_PROMPTCHAIN": "1"}), \
                mock.patch("shutil.which", return_value="/x/hunch"):
            self.bad(lambda: SB.hunch_vote({}).decide("\ud800", LABELS, "i"))  # a lone surrogate does not encode


class LazyImport(unittest.TestCase):
    def test_import_does_not_load_promptchain(self):
        code = "import sys, screener_backend; print('promptchain' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


if __name__ == "__main__":
    unittest.main()
