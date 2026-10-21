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
const ctx = { cwd: process.env.SMOKE_CWD, sessionManager: { getSessionId: () => "sess-1" } };
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
        self.assertEqual(calls[1]["stdin"], {"prompt": "hello", "session_id": "sess-1"})
        self.assertEqual(calls[3]["stdin"], {"last_assistant_message": "done",
                                             "session_id": "sess-1"})
        # session-start text rides on the first turn only, together with the prompt text
        self.assertEqual(out["r1"]["message"]["content"],
                         "[rookery] You are a.\n[rookery] unread 1")
        self.assertEqual(out["r2"]["message"]["content"], "[rookery] unread 1")


if __name__ == "__main__":
    unittest.main()
