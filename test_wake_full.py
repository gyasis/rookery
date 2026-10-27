#!/usr/bin/env python3
"""The installed zipapp carries feature 005 (T023): `wake`, `sweep` and `check` run from the archive.

    python3 -m unittest test_wake_full -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import bootstrap


class Zipapp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pyz = os.path.join(cls.tmp.name, "rookery.pyz")
        bootstrap.build_pyz(cls.pyz)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_pyz(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ROOKERY_")}
        env.update(HOME=self.tmp.name, ROOKERY_DB=os.path.join(self.tmp.name, "t.db"),
                   ROOKERY_WAKE="off")
        return subprocess.run([sys.executable, self.pyz, *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_archive_holds_the_005_modules(self):
        names = set(zipfile.ZipFile(self.pyz).namelist())
        self.assertLessEqual({"postmaster.py", "rookery_wake.py", "rookery_remote.py"}, names)

    def test_subcommands_run_from_the_archive(self):
        for sub in ("wake", "sweep", "check"):
            with self.subTest(sub=sub):
                p = self.run_pyz(sub, "--help")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("usage:", p.stdout)

    def test_wake_finds_postmaster_inside_the_archive(self):
        # an unknown session exits 3 after importing postmaster lazily: proves the module is reachable
        p = self.run_pyz("wake", "--session", "nope", "--agent", "nobody")
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertNotIn("ModuleNotFoundError", p.stderr)


if __name__ == "__main__":
    unittest.main()
