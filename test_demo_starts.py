"""Demo scripts must start the mailroom with a token or --allow-no-auth,
and with --allow-public on a public --host (feature 002, T044, FR-029)."""
import glob
import re
import shlex
import subprocess
import unittest

LOCAL = {"127.0.0.1", "localhost", "::1"}


def _logical_lines(path):
    out, cur = [], ""
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        out.append(cur + line)
        cur = ""
    if cur:
        out.append(cur)
    return out


def _is_start(path, line):
    s = line.strip()
    if "mailroom_server.py" not in s or s.startswith("#"):
        return False
    if re.match(r"(pgrep|pkill|echo)\b", s):
        return path == "gen_cert.sh" and s.startswith("echo")
    # a pgrep/pkill inside a for/subshell is a lookup, not a start
    if re.search(r"\b(pgrep|pkill)\b", s):
        return False
    return True


def _host(line):
    m = re.search(r"--host[ =]+(\S+)", line)
    return m.group(1).strip("\"'") if m else None


class DemoStarts(unittest.TestCase):
    def test_mailroom_starts(self):
        seen = 0
        for path in sorted(glob.glob("*.sh")):
            for line in _logical_lines(path):
                if not _is_start(path, line):
                    continue
                seen += 1
                with self.subTest(script=path, line=line.strip()[:80]):
                    self.assertTrue(
                        "--token" in line or "ROOKERY_TOKEN" in line
                        or "--allow-no-auth" in line,
                        "no token and no --allow-no-auth")
                    host = _host(line)
                    if host is not None and host not in LOCAL:
                        self.assertIn("--allow-public", line)
        self.assertGreater(seen, 5)

    def test_bash_syntax(self):
        for path in sorted(glob.glob("*.sh")):
            with self.subTest(script=path):
                r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
