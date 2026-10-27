#!/usr/bin/env python3
"""screener_config.enabled and screening_on (feature 005, T001, research R6).

    python3 -m unittest test_screener_enabled -v
"""
import json
import os
import tempfile
import unittest

import rookery_identity as RI
import screener_config as SC
import turn_block as TB

ENV_KEYS = ("ROOKERY_DB", "ROOKERY_ROOT", "HOME")


class EnabledBase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self.tmp.name
        os.environ["ROOKERY_DB"] = os.path.join(self.tmp.name, "rookery.db")
        os.environ["ROOKERY_ROOT"] = os.path.join(self.tmp.name, "root")
        self.conn = TB.open_db()
        self.path = os.path.join(self.tmp.name, ".rookery", "config.json")

    def tearDown(self):
        self.conn.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def write(self, obj):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(obj, fh)


class ScreeningOnTests(EnabledBase):
    def test_default_is_off(self):
        s = RI.new_session(self.conn)
        self.assertIs(SC.DEFAULTS["enabled"], False)
        self.assertFalse(SC.screening_on(self.conn, SC.load(), s["session_id"]))

    def test_no_config_file_is_off(self):
        s = RI.new_session(self.conn)
        self.assertFalse(SC.screening_on(self.conn, SC.load(), s["session_id"]))

    def test_enabled_true_is_on_for_every_session(self):
        self.write({"screener": {"enabled": True}})
        a, b = RI.new_session(self.conn), RI.new_session(self.conn)
        cfg = SC.load()
        self.assertTrue(SC.screening_on(self.conn, cfg, a["session_id"]))
        self.assertTrue(SC.screening_on(self.conn, cfg, b["session_id"]))

    def test_session_binding_is_on_for_that_session_only(self):
        a = RI.new_session(self.conn, binds=[("screener-enabled", "1")])
        b = RI.new_session(self.conn)
        cfg = SC.load()
        self.assertTrue(SC.screening_on(self.conn, cfg, a["session_id"]))
        self.assertFalse(SC.screening_on(self.conn, cfg, b["session_id"]))

    def test_binding_with_other_value_is_off(self):
        a = RI.new_session(self.conn, binds=[("screener-enabled", "0")])
        self.assertFalse(SC.screening_on(self.conn, SC.load(), a["session_id"]))

    def test_bad_config_does_not_raise(self):
        s = RI.new_session(self.conn)
        self.assertFalse(SC.screening_on(self.conn, {"enabled": "yes"}, s["session_id"]))
        self.assertFalse(SC.screening_on(self.conn, None, s["session_id"]))
        # the full config (as the gate and the sweep hold it) works the same as the screener block
        self.assertTrue(SC.screening_on(self.conn, {"screener": {"enabled": True}}, s["session_id"]))
        self.assertFalse(SC.screening_on(self.conn, {"screener": {"enabled": False}}, s["session_id"]))
        self.assertFalse(SC.screening_on(self.conn, {"screener": None}, s["session_id"]))
        # a dict that is neither the full config nor a screener block never turns screening on
        self.assertFalse(SC.screening_on(self.conn, {"other": 1, "enabled": True}, s["session_id"]))


class CheckTests(EnabledBase):
    def test_check_refuses_non_bool_enabled(self):
        for bad in ("yes", 1, None):
            cfg = SC.load()
            cfg["enabled"] = bad
            with self.assertRaises(ValueError) as cm:
                SC.check(cfg)
            self.assertIn("screener.enabled", str(cm.exception))

    def test_check_accepts_bool_enabled(self):
        for good in (True, False):
            cfg = SC.load()
            cfg["enabled"] = good
            SC.check(cfg)


if __name__ == "__main__":
    unittest.main()
