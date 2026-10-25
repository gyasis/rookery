"""T008: `rookery up` writes session_context and agent_scope; ask_budget and boss load (D8, D17)."""
import json
import os
import sqlite3
import tempfile
import unittest

import rookery_team
import test_up_build as tb

FIXTURE = os.path.join(tb.HERE, "fixtures", "screener", "team.json")
CONFIG = {"launch": None, "default_binding": None}


def team_path(spec):
    path = os.path.join(tempfile.mkdtemp(), "t.json")
    with open(path, "w") as fh:
        json.dump(spec, fh)
    return path


def fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)


class SessionContext(tb.UpBuild):
    # inherit the fixture; run only the new cases
    def run(self, result=None):
        if self._testMethodName.startswith("test_ctx_"):
            return super().run(result)
        return None

    def rows(self):
        c = sqlite3.connect(self.db)
        try:
            ctx = c.execute("SELECT task, acceptance, ask_budget FROM session_context").fetchall()
            sc = c.execute("SELECT i.name, s.does, s.does_not FROM agent_scope s "
                           "JOIN identities i ON i.agent_id=s.agent_id ORDER BY i.name").fetchall()
            return ctx, sc
        finally:
            c.close()

    def test_ctx_rows_written(self):
        self.up(FIXTURE, "--no-launch")
        ctx, sc = self.rows()
        self.assertEqual(len(ctx), 1)
        task, acc, budget = ctx[0]
        self.assertEqual(task, "Fix the token refresh race in the auth module.")
        self.assertEqual(json.loads(acc), ["test_refresh passes 100 times", "no change to the public API"])
        self.assertEqual(budget, 3)
        self.assertEqual([r[0] for r in sc], ["boss", "counsel", "researcher", "swe"])
        swe = [r for r in sc if r[0] == "swe"][0]
        self.assertEqual(json.loads(swe[1]), ["edit the auth module", "run the unit tests"])
        self.assertEqual(json.loads(swe[2]), ["change the public API", "review plans"])

    def test_ctx_second_run_same_rows(self):
        self.up(FIXTURE, "--no-launch")
        first = self.rows()
        self.up(FIXTURE, "--no-launch")
        self.assertEqual(self.rows(), first)

    def test_ctx_rerun_replaces(self):
        spec = fixture()
        self.write_team(spec)
        self.up(self.team, "--no-launch")
        spec["ask_budget"] = 5
        spec["agents"][0]["scope"]["does"] = ["read only"]
        self.write_team(spec)
        self.up(self.team, "--no-launch")
        ctx, sc = self.rows()
        self.assertEqual((len(ctx), ctx[0][2], len(sc)), (1, 5, 4))
        self.assertEqual(json.loads([r for r in sc if r[0] == "researcher"][0][1]), ["read only"])

    def test_ctx_removed_agent_loses_scope_row(self):
        spec = fixture()
        self.write_team(spec)
        self.up(self.team, "--no-launch")
        spec["agents"] = [a for a in spec["agents"] if a["name"] != "counsel"]
        self.write_team(spec)
        self.up(self.team, "--no-launch")
        self.assertEqual([r[0] for r in self.rows()[1]], ["boss", "researcher", "swe"])

    def test_ctx_boss_runs_and_holds_no_claims(self):
        self.up(FIXTURE, "--no-launch")
        c = sqlite3.connect(self.db)
        try:
            n = c.execute("SELECT COUNT(*) FROM claims c JOIN identities i ON i.agent_id=c.agent_id "
                          "WHERE i.name='boss'").fetchone()[0]
            self.assertEqual(n, 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM identities WHERE name='boss'").fetchone()[0], 1)
        finally:
            c.close()

    def test_ctx_no_scope_gives_empty_lists(self):
        self.up(self.team, "--no-launch")
        ctx, sc = self.rows()
        self.assertEqual((ctx[0][1], ctx[0][2]), ("[]", None))
        self.assertEqual({(r[1], r[2]) for r in sc}, {("[]", "[]")})


class TeamKeys(unittest.TestCase):
    def fails(self, spec, text):
        with self.assertRaises(rookery_team.TeamError) as cm:
            rookery_team.load(team_path(spec), CONFIG)
        self.assertIn(text, str(cm.exception))

    def test_ctx_fixture_loads_with_boss(self):
        team = rookery_team.load(FIXTURE, CONFIG)
        self.assertEqual(team["ask_budget"], 3)
        self.assertEqual(team["agents"][-1]["role"], "boss")

    def test_ctx_bad_ask_budget(self):
        for bad in (0, "x", -1, True, 1.5):
            spec = fixture()
            spec["ask_budget"] = bad
            self.fails(spec, "ask_budget")

    def test_ctx_acceptance_and_scope_errors_name_the_key(self):
        spec = fixture()
        spec["acceptance"] = "x"
        self.fails(spec, "acceptance")
        spec = fixture()
        spec["agents"][0]["scope"]["does_not"] = [1]
        self.fails(spec, "does_not")

    def test_ctx_role_text(self):
        spec = fixture()
        spec["agents"][0]["role"] = "cook"
        self.fails(spec, "The role must be reader, writer or boss.")

    def test_ctx_boss_with_claims_is_refused(self):
        for key, val in (("claims", ["a/*.py"]), ("repo", "."), ("task_id", "T1")):
            spec = fixture()
            boss = [a for a in spec["agents"] if a["role"] == "boss"][0]
            boss[key] = val
            self.fails(spec, "A boss reads files and holds no claims.")


if __name__ == "__main__":
    unittest.main()
