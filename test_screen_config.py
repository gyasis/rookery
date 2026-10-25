#!/usr/bin/env python3
"""screener_config: defaults, validation and set_mode (research D21).

    python3 -m unittest test_screen_config -v
"""
import copy
import json
import os
import tempfile
import unittest

import screener_config as SC


class ConfigBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        self.path = os.path.join(self.tmp.name, ".rookery", "config.json")

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def write(self, obj):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(obj, fh)


class LoadTests(ConfigBase):
    def test_old_file_without_screener_gives_defaults(self):
        self.write({"loop_limit": 10})
        cfg = SC.load()
        self.assertEqual(cfg, SC.DEFAULTS)
        self.assertEqual(set(cfg["checks"].values()), {"shadow"})
        self.assertEqual(set(cfg["checks"]), set(SC.CHECKS))
        SC.check(cfg)

    def test_partial_block_keeps_other_defaults(self):
        self.write({"screener": {"checks": {"t1": "live"}, "t2": {"batch": 5}}})
        cfg = SC.load()
        self.assertEqual(cfg["checks"]["t1"], "live")
        self.assertEqual(cfg["checks"]["hunch"], "shadow")
        self.assertEqual(cfg["t2"], {"command": None, "batch": 5})
        self.assertEqual(cfg["model"], "tev1:4b")

    def test_load_writes_nothing(self):
        SC.load()
        self.assertFalse(os.path.exists(self.path))

    def test_packaged_default_matches_code_defaults(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "rookery_config.default.json")) as fh:
            self.assertEqual(json.load(fh)["screener"], SC.DEFAULTS)

    def test_no_forbidden_keys(self):
        text = json.dumps(SC.DEFAULTS)
        for word in ("max_tokens", "max_completion_tokens", "num_predict", "num_ctx", "think"):
            self.assertNotIn(word, text)


class CheckTests(unittest.TestCase):
    def bad(self, mutate, key):
        cfg = copy.deepcopy(SC.DEFAULTS)
        mutate(cfg)
        with self.assertRaises(ValueError) as ctx:
            SC.check(cfg)
        self.assertIn(key, str(ctx.exception))

    def test_defaults_pass(self):
        SC.check(copy.deepcopy(SC.DEFAULTS))

    def test_model_names_must_fit_decided_by(self):   # F5
        for bad in ("my model+x", "", None, 5):
            self.bad(lambda c, b=bad: c.__setitem__("model", b), "screener.model")
            self.bad(lambda c, b=bad: c["hunch"].__setitem__("model", b), "screener.hunch.model")

    def test_good_bands_pass(self):
        cfg = copy.deepcopy(SC.DEFAULTS)
        cfg["bands"]["t1"] = {"accept": 0.9, "review": 0.6}
        SC.check(cfg)

    def test_bad_mode(self):
        self.bad(lambda c: c["checks"].update(t1="on"), "screener.checks.t1")

    def test_unknown_check(self):
        self.bad(lambda c: c["checks"].update(bogus="live"), "screener.checks.bogus")

    def test_bad_bands(self):
        def swap(c): c["bands"]["t1"] = {"accept": 0.5, "review": 0.8}
        self.bad(swap, "screener.bands.t1.review")
        def high(c): c["bands"]["t1"] = {"accept": 1.5, "review": 0.8}
        self.bad(high, "screener.bands.t1.accept")
        def low(c): c["bands"]["t1"] = {"accept": 0.9, "review": -0.1}
        self.bad(low, "screener.bands.t1.review")
        def shape(c): c["bands"]["t1"] = {"accept": 0.9}
        self.bad(shape, "screener.bands.t1")

    def test_bad_t2_command(self):
        self.bad(lambda c: c["t2"].update(command="run it"), "screener.t2.command")
        self.bad(lambda c: c["t2"].update(command=["a", 3]), "screener.t2.command")

    def test_max_age_s_default_and_check(self):
        self.assertEqual(SC.DEFAULTS["max_age_s"], 86400)
        self.bad(lambda c: c.update(max_age_s=0), "screener.max_age_s")
        self.bad(lambda c: c.update(max_age_s=True), "screener.max_age_s")

    def test_lease_must_be_twice_the_timeout(self):
        self.bad(lambda c: c.update(timeout_s=300, lease_s=599), "screener.lease_s")
        cfg = copy.deepcopy(SC.DEFAULTS)
        cfg.update(timeout_s=300, lease_s=600)
        SC.check(cfg)

    def test_non_positive_numbers(self):
        for key in ("timeout_s", "envelope_max_chars", "ask_budget", "duplicate_window_s", "lease_s", "max_age_s"):
            self.bad(lambda c, k=key: c.update({k: 0}), f"screener.{key}")
        self.bad(lambda c: c["t2"].update(batch=-1), "screener.t2.batch")
        self.bad(lambda c: c.update(ask_budget=True), "screener.ask_budget")
        self.bad(lambda c: c.update(lease_s="9"), "screener.lease_s")

    def test_null_or_non_object_blocks_name_the_key(self):
        for key in ("checks", "bands", "t2", "hunch", "review_mode"):
            for bad in (None, [], "x", 3):
                self.bad(lambda c, k=key, b=bad: c.update({k: b}), f"screener.{key}")
        self.bad(lambda c: c["review_mode"].update(agents=None), "screener.review_mode.agents")

    def test_bad_review_mode(self):
        self.bad(lambda c: c["review_mode"].update(default="later"), "screener.review_mode.default")
        self.bad(lambda c: c["review_mode"]["agents"].update(boss="never"),
                 "screener.review_mode.agents.boss")


class SetModeTests(ConfigBase):
    def test_keeps_unrelated_key_and_other_modes(self):
        self.write({"loop_limit": 7, "screener": {"checks": {"injection": "live"},
                                                  "model": "x:1b"}})
        SC.set_mode("t1", "live")
        with open(self.path) as fh:
            got = json.load(fh)
        self.assertEqual(got["loop_limit"], 7)
        self.assertEqual(got["screener"]["model"], "x:1b")
        self.assertEqual(got["screener"]["checks"], {"injection": "live", "t1": "live"})
        self.assertEqual(SC.load()["checks"]["duplicate"], "shadow")
        leftovers = [f for f in os.listdir(os.path.dirname(self.path)) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_old_file_without_screener(self):
        self.write({"loop_limit": 7})
        SC.set_mode("gates", "off")
        with open(self.path) as fh:
            got = json.load(fh)
        self.assertEqual(got["loop_limit"], 7)
        self.assertEqual(got["screener"], {"checks": {"gates": "off"}})

    def test_missing_file_starts_from_packaged_default(self):
        SC.set_mode("t2", "live")
        with open(self.path) as fh:
            got = json.load(fh)
        self.assertIn("loop_limit", got)
        self.assertEqual(got["screener"]["checks"]["t2"], "live")
        self.assertEqual(got["screener"]["checks"]["t1"], "shadow")

    def test_non_object_config_or_screener_raises_and_is_not_replaced(self):
        for content, key in (([], "config"), ({"screener": None}, "screener"), ({"screener": []}, "screener"),
                             ({"screener": "x"}, "screener")):
            self.write(content)
            with self.assertRaises(ValueError) as ctx:
                SC.set_mode("t1", "live")
            self.assertIn(key, str(ctx.exception))
            with open(self.path) as fh:
                self.assertEqual(json.load(fh), content)

    def test_takes_an_exclusive_flock_on_the_sidecar_lock(self):
        import fcntl
        import threading
        self.write({"loop_limit": 1})
        with open(self.path + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            t = threading.Thread(target=SC.set_mode, args=("t1", "live"))
            t.start()
            t.join(0.5)
            self.assertTrue(t.is_alive(), "set_mode did not wait for the lock")
            with open(self.path) as fh:
                self.assertNotIn("screener", json.load(fh))
            fcntl.flock(lock, fcntl.LOCK_UN)
        t.join(5)
        self.assertFalse(t.is_alive())
        with open(self.path) as fh:
            self.assertEqual(json.load(fh)["screener"]["checks"]["t1"], "live")

    def test_bad_name_or_mode(self):
        with self.assertRaises(ValueError) as ctx:
            SC.set_mode("nope", "live")
        self.assertIn("screener.checks.nope", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            SC.set_mode("t1", "maybe")
        self.assertIn("screener.checks.t1", str(ctx.exception))
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
