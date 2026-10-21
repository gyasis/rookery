#!/usr/bin/env python3
"""Tests of rookery_pi_extension.ts. Static checks always run; the smoke test runs when node exists.

    python3 -W ignore -m unittest test_pi_extension -v
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TS = os.path.join(HERE, "rookery_pi_extension.ts")


class TestStatic(unittest.TestCase):
    def setUp(self):
        with open(TS, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_calls_the_three_hooks_with_the_cli_field_names(self):
        for sub in ("session-start", "prompt", "stop"):
            self.assertIn(f'"{sub}"', self.src)
        for field in ("source", "session_id", "cwd", "prompt", "last_assistant_message"):
            self.assertIn(field, self.src)

    def test_uses_documented_events(self):
        for ev in ("session_start", "before_agent_start", "agent_end"):
            self.assertIn(f'pi.on("{ev}"', self.src)

    def test_placeholder_present_once_for_init(self):
        self.assertEqual(self.src.count('"__ROOKERY_PROJECT__"'), 1)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestSmoke(unittest.TestCase):
    """Load the file with a fake pi, fire the events, check what the fake rookery saw."""

    DRIVER = r"""
const mod = await import(process.argv[2]);
const handlers = {};
mod.default({ on: (e, h) => { handlers[e] = h; } });
const ctx = { cwd: process.env.SMOKE_CWD, sessionManager: { getSessionId: () => "sess-1", getSessionFile: () => "/tmp/sess.jsonl" } };
await handlers.session_start({ reason: "startup" }, ctx);
const r1 = await handlers.before_agent_start({ messageType: "user", prompt: "hello" }, ctx);
const r2 = await handlers.before_agent_start({ messageType: "user", prompt: "again" }, ctx);
await handlers.agent_end({ willRetry: true, messages: [] }, ctx);
await handlers.agent_end({ messages: [
  { role: "user", content: "hello" },
  { role: "assistant", content: [{ type: "thinking", thinking: "x" }, { type: "text", text: "done" }] },
] }, ctx);
console.log(JSON.stringify({ r1, r2 }));
"""

    def test_events_invoke_rookery(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "calls.jsonl")
            fake = os.path.join(tmp, "fake-rookery")
            with open(fake, "w") as fh:
                fh.write("#!/usr/bin/env python3\nimport sys, json\n"
                         "inp = sys.stdin.read()\n"
                         f"open({log!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'stdin': json.loads(inp)}}) + '\\n')\n"
                         "if sys.argv[2] == 'session-start': print('[rookery] You are a.')\n"
                         "if sys.argv[2] == 'prompt': print('[rookery] unread 1')\n")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
            driver = os.path.join(tmp, "driver.mjs")
            with open(driver, "w") as fh:
                fh.write(self.DRIVER)
            ext = os.path.join(tmp, "ext.ts")
            shutil.copy(TS, ext)
            env = dict(os.environ, ROOKERY_BIN=fake, ROOKERY_PROJECT="proj", SMOKE_CWD=tmp)
            r = subprocess.run(["node", "--experimental-strip-types", driver, ext], env=env, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
            with open(log) as fh:
                calls = [json.loads(line) for line in fh]
        subs = [c["argv"][1] for c in calls]
        self.assertEqual(subs, ["session-start", "prompt", "prompt", "stop"])
        self.assertTrue(all(c["argv"][2:] == ["--project", "proj"] for c in calls))
        self.assertEqual(calls[0]["stdin"], {"source": "startup", "session_id": "sess-1",
                                             "cwd": calls[0]["stdin"]["cwd"]})
        self.assertEqual({k: v for k, v in calls[1]["stdin"].items() if k != "transcript_path"},
                         {"prompt": "hello", "session_id": "sess-1"})
        self.assertEqual({k: v for k, v in calls[3]["stdin"].items() if k != "transcript_path"},
                         {"last_assistant_message": "done", "session_id": "sess-1"})
        # session-start text rides on the first turn only, together with the prompt text
        self.assertEqual(out["r1"]["message"]["content"],
                         "[rookery] You are a.\n[rookery] unread 1")
        self.assertEqual(out["r2"]["message"]["content"], "[rookery] unread 1")
        # item 7: transcript_path passed to the prompt and stop hooks
        self.assertEqual(calls[1]["stdin"]["transcript_path"], "/tmp/sess.jsonl")
        self.assertEqual(calls[3]["stdin"]["transcript_path"], "/tmp/sess.jsonl")


    SCEN = r"""
const mod = await import(process.argv[2]);
const handlers = {};
mod.default({ on: (e, h) => { handlers[e] = h; } });
const ctx = { cwd: process.env.SMOKE_CWD, sessionManager: { getSessionId: () => "s" } };
const asst = (text, stopReason) => ({ messages: [{ role: "assistant", stopReason, content: [{ type: "text", text }] }] });
const scen = process.env.SCEN;
if (scen === "compact") {
  await handlers.session_start({ reason: "startup" }, ctx);
  await handlers.before_agent_start({ messageType: "user", prompt: "a" }, ctx);
  if (handlers.session_compact) await handlers.session_compact({}, ctx);
  console.log(JSON.stringify(await handlers.before_agent_start({ messageType: "user", prompt: "b" }, ctx)));
} else if (scen === "aborted") {
  await handlers.agent_end(asst("```rookery half", "aborted"), ctx);
} else if (scen === "dedupe") {
  await handlers.before_agent_start({ messageType: "user", prompt: "a" }, ctx);
  await handlers.agent_end(asst("same", "stop"), ctx);
  await handlers.agent_end(asst("same", "stop"), ctx);
  await handlers.agent_end(asst("different", "stop"), ctx);
  await handlers.before_agent_start({ messageType: "user", prompt: "b" }, ctx);
  await handlers.agent_end(asst("same", "stop"), ctx);
} else if (scen === "big") {
  console.log(JSON.stringify(await handlers.before_agent_start({ messageType: "user", prompt: "a" }, ctx)));
}
"""

    def scenario(self, scen, script, project="proj"):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "calls.jsonl")
            fake = os.path.join(tmp, "fake-rookery")
            with open(fake, "w") as fh:
                fh.write("#!/usr/bin/env python3\nimport sys, json, os, time\n"
                         "inp = sys.stdin.read()\n"
                         f"open({log!r}, 'a').write(json.dumps({{'sub': sys.argv[2], 'stdin': json.loads(inp)}}) + '\\n')\n"
                         + script)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
            for name, body in (("ext.ts", None), ("driver.mjs", self.SCEN)):
                if body is None:
                    shutil.copy(TS, os.path.join(tmp, name))
                else:
                    with open(os.path.join(tmp, name), "w") as fh:
                        fh.write(body)
            env = dict(os.environ, ROOKERY_BIN=fake, ROOKERY_PROJECT=project, SMOKE_CWD=tmp, SCEN=scen)
            t0 = time.time()
            r = subprocess.run(["node", "--experimental-strip-types", os.path.join(tmp, "driver.mjs"),
                                os.path.join(tmp, "ext.ts")], env=env, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = []
            if os.path.exists(log):
                with open(log) as fh:
                    calls = [json.loads(line) for line in fh]
            return r.stdout.strip(), calls, time.time() - t0

    def test_session_compact_does_not_rerun_session_start(self):
        out, calls, _ = self.scenario("compact", "if sys.argv[2] == 'session-start': print('SS')\n")
        self.assertEqual([c["sub"] for c in calls].count("session-start"), 1)
        self.assertEqual(out, "undefined")   # session-start text was spent on the first prompt

    def test_aborted_turn_sends_empty_message(self):
        _, calls, _ = self.scenario("aborted", "")
        self.assertEqual(calls[0]["stdin"]["last_assistant_message"], "")

    def test_repeated_agent_end_in_one_turn_sends_once(self):
        _, calls, _ = self.scenario("dedupe", "")
        texts = [c["stdin"]["last_assistant_message"] for c in calls if c["sub"] == "stop"]
        self.assertEqual(texts, ["same", "different", "same"])

    def test_output_capped_at_8kb(self):
        out, _, _ = self.scenario("big", "if sys.argv[2] == 'prompt':\n    sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(3)\n")
        self.assertLessEqual(len(json.loads(out)["message"]["content"]), 8192)

    def test_grandchild_holding_stdout_does_not_cost_the_timeout(self):
        script = ("if sys.argv[2] == 'prompt':\n    print('hi'); sys.stdout.flush()\n"
                  "    import subprocess; subprocess.Popen(['sleep', '4'])\n")
        out, _, secs = self.scenario("big", script)
        self.assertEqual(json.loads(out)["message"]["content"], "hi")
        self.assertLess(secs, 3)

    def test_real_project_named_dunder_is_used(self):
        # the baked name "__init__" must not be mistaken for the placeholder
        with open(TS, encoding="utf-8") as fh:
            src = fh.read().replace("__ROOKERY_PROJECT__", "__init__", 1)
        with tempfile.TemporaryDirectory() as tmp:
            ext = os.path.join(tmp, "ext.ts")
            with open(ext, "w") as fh:
                fh.write(src)
            drv = os.path.join(tmp, "d.mjs")
            with open(drv, "w") as fh:
                fh.write('const m = await import(process.argv[2]); const h = {};'
                         'm.default({on:(e,f)=>{h[e]=f}});'
                         'await h.before_agent_start({messageType:"user",prompt:"p"},{cwd:process.env.SMOKE_CWD});')
            argv = os.path.join(tmp, "argv")
            fake = os.path.join(tmp, "fake")
            with open(fake, "w") as fh:
                fh.write(f"#!/bin/sh\necho \"$@\" > {argv}\ncat >/dev/null\n")
            os.chmod(fake, 0o755)
            env = {k: v for k, v in os.environ.items() if k != "ROOKERY_PROJECT"}
            env.update(ROOKERY_BIN=fake, SMOKE_CWD=tmp)
            subprocess.run(["node", "--experimental-strip-types", drv, ext], env=env,
                           capture_output=True, timeout=60, check=True)
            with open(argv) as fh:
                self.assertIn("--project __init__", fh.read())


if __name__ == "__main__":
    unittest.main()
