"""test_team_file — rookery_team.load validates a team file before `rookery up` changes anything (FR-004, FR-009, FR-019, FR-021, D4, D17)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import rookery_team
from rookery_team import TeamError

CONFIG = {"launch": {"tmux-pane": {"steps": []}}, "default_binding": "tmux-pane"}
EXAMPLE = {
    "session": "auth-fix",
    "task": "Fix the token refresh race.",
    "acceptance": ["test_refresh passes 100 times", "no change to the public API"],
    "agents": [
        {"name": "scout", "harness": "claude", "role": "reader",
         "persona": "You read code and report. You do not edit.",
         "scope": {"does": ["read the auth module"], "does_not": ["edit files", "run the server"]}},
        {"name": "fixer", "harness": "claude", "role": "writer", "task_id": "T1",
         "binding": "tmux-pane", "claims": ["auth/*.py"]},
    ],
}


def agent(**kw):
    base = {"name": "a", "harness": "claude", "role": "reader"}
    base.update(kw)
    return base


class TeamFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, data, raw=False):
        path = os.path.join(self.tmp, "team.json")
        with open(path, "w") as fh:
            fh.write(data if raw else json.dumps(data))
        return path

    def errors(self, data, config=CONFIG, raw=False):
        with self.assertRaises(TeamError) as cm:
            rookery_team.load(self.write(data, raw), config)
        errs = cm.exception.errors
        for e in errs:
            self.assertEqual(set(e), {"index", "name", "problem"})
        return errs

    def one(self, data, name, index, **kw):
        errs = self.errors(data, **kw)
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0]["name"], name)
        self.assertEqual(errs[0]["index"], index)
        return errs[0]["problem"]

    def team(self, *agents, **kw):
        t = {"session": "s1", "agents": list(agents)}
        t.update(kw)
        return t

    def test_valid_example_loads(self):
        team = rookery_team.load(self.write(EXAMPLE), CONFIG)
        self.assertEqual(team["session"], "auth-fix")
        scout, fixer = team["agents"]
        self.assertEqual(scout["binding"], "tmux-pane")      # default_binding
        self.assertIsNone(scout["repo"])
        self.assertIsNone(fixer["repo"])
        self.assertEqual(fixer["binding"], "tmux-pane")
        self.assertEqual(fixer["task_id"], "T1")
        self.assertEqual(fixer["claims"], ["auth/*.py"])

    def test_writer_repo_default_is_none(self):
        team = rookery_team.load(self.write(self.team(agent(role="writer", task_id="T1"))), CONFIG)
        self.assertIsNone(team["agents"][0]["repo"])

    def test_no_session(self):
        self.one({"agents": [agent()]}, None, None)

    def test_reserved_session_names(self):
        for bad in ("legacy", "rs-0123abcd", "ag-0123456789"):
            self.one({"session": bad, "agents": [agent()]}, None, None)

    def test_no_agents(self):
        self.one({"session": "s1"}, None, None)

    def test_empty_agents(self):
        self.one({"session": "s1", "agents": []}, None, None)

    def test_duplicate_name(self):
        self.one(self.team(agent(), agent()), "a", 1)

    def test_bad_safe_name(self):
        self.one(self.team(agent(name="a b")), "a b", 0)

    def test_harness_codex(self):
        self.assertIn("Codex waits for R19", self.one(self.team(agent(harness="codex")), "a", 0))

    def test_harness_x(self):
        self.one(self.team(agent(harness="x")), "a", 0)

    def test_role_x(self):
        self.one(self.team(agent(role="x")), "a", 0)

    def test_writer_no_task_id(self):
        self.one(self.team(agent(role="writer")), "a", 0)

    def test_writer_double_dot(self):
        self.one(self.team(agent(name="a..b", role="writer", task_id="T1")), "a..b", 0)

    def test_writer_lock_suffix(self):
        self.one(self.team(agent(name="x.lock", role="writer", task_id="T1")), "x.lock", 0)

    def test_reader_lock_suffix_ok(self):
        team = rookery_team.load(self.write(self.team(agent(name="x.lock"))), CONFIG)
        self.assertEqual(team["agents"][0]["name"], "x.lock")

    def test_unknown_binding(self):
        self.one(self.team(agent(binding="nope")), "a", 0)

    def test_three_bad_entries(self):
        errs = self.errors(self.team(agent(name="a", harness="x"), agent(name="b", role="x"),
                                     agent(name="c b")))
        self.assertEqual([e["index"] for e in errs], [0, 1, 2])

    def test_bad_json_names_line(self):
        errs = self.errors('{\n"session": "s1",\n"agents": [,\n]}', raw=True)
        self.assertEqual(len(errs), 1)
        self.assertIn("line 3", errs[0]["problem"])

    def test_no_launch_key_accepts_safe_binding(self):
        t = self.team(agent(binding="whatever"))
        self.assertEqual(rookery_team.load(self.write(t), {})["agents"][0]["binding"], "whatever")

    def _git_dir(self, name="repo"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        subprocess.run(["git", "init", "-q", d], check=True)
        return d

    def test_writer_repo_not_git(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d)
        self.one(self.team(agent(role="writer", task_id="T1", repo=d)), "a", 0)

    def test_writer_repo_subfolder(self):
        sub = os.path.join(self._git_dir(), "sub")
        os.makedirs(sub)
        self.one(self.team(agent(role="writer", task_id="T1", repo=sub)), "a", 0)

    def test_writer_repo_git_top_ok(self):
        d = self._git_dir()
        team = rookery_team.load(self.write(self.team(agent(role="writer", task_id="T1", repo=d))), CONFIG)
        self.assertEqual(team["agents"][0]["repo"], os.path.realpath(d))

    def test_writer_repo_relative_uses_team_file_folder(self):
        self._git_dir("rel")
        team = rookery_team.load(self.write(self.team(agent(role="writer", task_id="T1", repo="rel"))), CONFIG)
        self.assertEqual(team["agents"][0]["repo"], os.path.realpath(os.path.join(self.tmp, "rel")))

    def test_binding_not_a_string(self):
        self.one(self.team(agent(binding=["tmux-pane"])), "a", 0)
        self.one(self.team(agent(binding={"x": 1})), "a", 0)
        self.one(self.team(agent(binding=["x"])), "a", 0, config={})

    def test_non_utf8_file(self):
        path = os.path.join(self.tmp, "team.json")
        with open(path, "wb") as fh:
            fh.write(b'{"session": "\xff\xfe"}')
        with self.assertRaises(TeamError) as cm:
            rookery_team.load(path, CONFIG)
        self.assertEqual(len(cm.exception.errors), 1)

    def test_deeply_nested_json(self):
        errs = self.errors("[" * 200000 + "]" * 200000, raw=True)
        self.assertEqual(len(errs), 1)

    def test_git_missing(self):
        t = self.team(agent(role="writer", task_id="T1"))
        path = self.write(t)
        saved = os.environ["PATH"]
        os.environ["PATH"] = self.tmp
        try:
            with self.assertRaises(TeamError) as cm:
                rookery_team.load(path, CONFIG)
        finally:
            os.environ["PATH"] = saved
        errs = cm.exception.errors
        self.assertEqual([e["problem"] for e in errs], ["Git is not installed."])

    def test_git_missing_with_repo(self):
        d = self._git_dir()
        path = self.write(self.team(agent(role="writer", task_id="T1", repo=d)))
        saved = os.environ["PATH"]
        os.environ["PATH"] = self.tmp
        try:
            with self.assertRaises(TeamError) as cm:
                rookery_team.load(path, CONFIG)
        finally:
            os.environ["PATH"] = saved
        self.assertEqual([e["problem"] for e in cm.exception.errors], ["Git is not installed."])

    def test_repo_with_nul(self):
        self.one(self.team(agent(role="writer", task_id="T1", repo="a\0b")), "a", 0)

    def test_names_differ_only_by_case(self):
        self.one(self.team(agent(name="Bob"), agent(name="bob")), "bob", 1)

    def test_optional_field_types(self):
        for kw in ({"persona": 3}, {"scope": []}, {"scope": {"does": "x"}},
                   {"scope": {"does_not": [1]}}, {"claims": "a"}, {"claims": [""]},
                   {"claims": [3]}, {"claims": ["/etc/*"]}, {"claims": ["../x"]},
                   {"claims": ["a/../x"]}):
            with self.subTest(kw=kw):
                self.one(self.team(agent(**kw)), "a", 0)
        self.one(self.team(agent(), task=5), None, None)
        self.one(self.team(agent(), acceptance="x"), None, None)
        self.one(self.team(agent(), acceptance=[1]), None, None)

    def test_valid_optional_fields(self):
        t = self.team(agent(persona="p", scope={"does": ["x"], "does_not": ["y"]}, claims=["a/*.py"]),
                      task="t", acceptance=["ok"])
        rookery_team.load(self.write(t), CONFIG)

    def test_effective_binding_must_be_in_launch(self):
        cfg = {"launch": {"tmux-pane": {}}, "default_binding": "gone"}
        self.one(self.team(agent()), "a", 0, config=cfg)
        self.one(self.team(agent()), "a", 0, config={"launch": {"tmux-pane": {}}})

    def test_task_id_is_one_safe_branch_part(self):
        self.one(self.team(agent(role="writer", task_id="a/b")), "a", 0)
        self.one(self.team(agent(role="writer", task_id="a b")), "a", 0)
        self.one(self.team(agent(role="writer", task_id=5)), "a", 0)

    def test_reader_repo_is_dropped(self):
        team = rookery_team.load(self.write(self.team(agent(repo="/nowhere"))), CONFIG)
        self.assertIsNone(team["agents"][0]["repo"])

    def test_top_level_not_dict(self):
        self.assertEqual(len(self.errors("[1]", raw=True)), 1)

    def test_agents_not_list(self):
        self.one({"session": "s1", "agents": "x"}, None, None)
        self.one({"session": "s1", "agents": {"a": 1}}, None, None)

    def test_agent_entry_not_dict(self):
        self.one({"session": "s1", "agents": ["x"]}, None, 0)

    def test_non_string_names(self):
        for n in (5, ["a"], {"a": 1}, None):
            with self.subTest(n=n):
                self.one(self.team(agent(name=n)), None, 0)
        self.one({"session": 5, "agents": [agent()]}, None, None)

    @unittest.skipUnless(shutil.which("ste-lint"), "ste-lint is not on PATH")
    def test_problem_texts_pass_ste_lint(self):
        w = {"role": "writer"}
        cases = [
            {"agents": [agent()]}, {"session": "s1"}, {"session": "s1", "agents": []},
            {"session": 5, "agents": "x"}, {"session": "s1", "agents": ["x"]},
            self.team(agent(), agent()), self.team(agent(name="Bob"), agent(name="bob")),
            self.team(agent(name="a b")), self.team(agent(name=5)),
            self.team(agent(harness="codex")), self.team(agent(harness="x")),
            self.team(agent(role="x")), self.team(agent(**w)),
            self.team(agent(task_id="a/b", **w)), self.team(agent(name="a..b", task_id="T1", **w)),
            self.team(agent(binding="nope")), self.team(agent(binding=[1])),
            self.team(agent(**w, task_id="T1", repo="a\0b")),
            self.team(agent(**w, task_id="T1", repo=self.tmp)),
            self.team(agent(**w, task_id="T1", repo="")),
            self.team(agent(persona=3), agent(name="b", scope=[]),
                      agent(name="c", scope={"does": "x"}), agent(name="d", scope={"does_not": [1]}),
                      agent(name="e", claims="a"), agent(name="f", claims=[3]),
                      agent(name="g", claims=[""]), agent(name="h", claims=["/x"]),
                      agent(name="i", claims=["../x"])),
            self.team(agent(), task=5, acceptance=[1]), self.team(agent(), acceptance="x"),
        ]
        texts = set()
        for data in cases:
            with self.assertRaises(TeamError) as cm:
                rookery_team.load(self.write(data), CONFIG)
            texts.update(e["problem"] for e in cm.exception.errors)
        with self.assertRaises(TeamError) as cm:
            rookery_team.load(self.write(self.team(agent()), ), {"launch": {"x": {}}})
        texts.update(e["problem"] for e in cm.exception.errors)
        for raw in ("[1]", '{\n"a": [,\n]}', "[" * 200000):
            with self.assertRaises(TeamError) as cm:
                rookery_team.load(self.write(raw, raw=True), CONFIG)
            texts.update(e["problem"] for e in cm.exception.errors)
        with self.assertRaises(TeamError) as cm:
            rookery_team.load(os.path.join(self.tmp, "missing.json"), CONFIG)
        texts.update(e["problem"] for e in cm.exception.errors)
        path = self.write(self.team(agent(**w, task_id="T1")))
        saved = os.environ["PATH"]
        os.environ["PATH"] = self.tmp
        try:
            with self.assertRaises(TeamError) as cm:
                rookery_team.load(path, CONFIG)
        finally:
            os.environ["PATH"] = saved
        texts.update(e["problem"] for e in cm.exception.errors)
        self.assertGreater(len(texts), 20)
        out = os.path.join(self.tmp, "problems.txt")
        with open(out, "w") as fh:
            fh.write("\n\n".join(sorted(texts)) + "\n")
        root = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run(["ste-lint", "--max-score", "2.0", "--glossary",
                            os.path.join(root, "ste-glossary.txt"), out],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
