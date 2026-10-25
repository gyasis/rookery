"""test_gate_scan — static scan: no code path may bypass the mail gate (FR-004, FR-021, SC-002, D4).

Scope: every non-test .py file at the repo root and in plugin_herdr/. Skipped: test_*.py, this file,
gate_matrix.py (test support, like the tests), fixtures/, .claude/, specs/ and hidden dirs.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCAN_DIRS = (ROOT, ROOT / "plugin_herdr")
SKIP_FILES = {"gate_matrix.py", Path(__file__).name}

SEND_OK = {"mail_gate.py", "rookery.py"}
QUARANTINE_OK = {"mail_gate.py", "rookery.py", "mail_screen.py", "cli_screen.py", "screener.py"}
SYSTEM_SEND_OK = {"postmaster.py", "relay.py", "rookery.py", "mail_gate.py", "screener_t0.py"}
RECIPIENT_UPDATE_OK = {"relay.py"}
INSERT_OK = {"rookery.py"}
ESCALATE_OK = {"rookery.py", "screener.py", "cli_screen.py", "mail_screen.py"}
# rookery._decide moves any held mail to any state. Real callers: the gate parks a mail whose worker gates fire
# (mail_gate.py), the screener escalates a failed screening (screener.py).
DECIDE_OK = {"rookery.py", "screener.py", "mail_gate.py"}
IP_OK = {"screener_config.py"}
NO_SCREENER_IMPORT = {"mail_gate.py", "turn_block.py", "cli_hook.py", "screener_t0.py", "mail_screen.py"}
NO_PROMPTCHAIN_IMPORT = {"mail_gate.py", "turn_block.py", "cli_hook.py", "screener_t0.py"}
FORBIDDEN_KEYS = {"max_tokens", "max_completion_tokens", "num_predict", "num_ctx", "think"}

_SP = r"(?:\s|/\*.*?\*/|--[^\n]*\n)+"                        # whitespace and SQL comments
_TBL = r"""(?:(?:\w+|"\w+"|`\w+`|\[\w+\])\s*\.\s*)?(?:inbox|"inbox"|`inbox`|\[inbox\])(?!\w)"""
_INSERT_RE = re.compile(r"(?:insert(?:" + _SP + r"or" + _SP + r"\w+)?|replace)" + _SP + r"into" + _SP + _TBL,
                        re.I | re.S)
_RECIPIENT_RE = re.compile(r"update(?:" + _SP + r"or" + _SP + r"\w+)?" + _SP + _TBL + _SP
                           + r"set\b(?:(?:(?!\bwhere\b)[^;])*?[\s,])?recipient\s*=", re.I | re.S)
_DYN_MODULES = ("rookery", "mail_gate")

# (module, attribute) -> (rule text, allowed files)
_WATCHED = {
    ("rookery", "send"): ("calls rookery.send; send through mail_gate.submit", SEND_OK),
    ("rookery", "quarantine"): ("calls rookery.quarantine; only the gate and mail_screen may", QUARANTINE_OK),
    ("rookery", "escalate"): ("calls rookery.escalate; only rookery, the screener and its CLI may", ESCALATE_OK),
    ("rookery", "_decide"): ("calls rookery._decide; only rookery, the screener and the gate may", DECIDE_OK),
    ("mail_gate", "system_send"): ("calls mail_gate.system_send; only postmaster and relay may", SYSTEM_SEND_OK),
}


def scan_source(path_name, source):
    """Return a list of 'file:line: message' failures for one file. path_name is the repo-relative path."""
    base = path_name.replace("\\", "/").rsplit("/", 1)[-1]
    in_pkg = "/" in path_name.replace("\\", "/")
    # a root-level name in the allow lists only counts for a root-level file
    def allowed(files):
        return (not in_pkg) and base in files

    try:
        tree = ast.parse(source, filename=path_name)
    except SyntaxError as exc:
        return [f"{path_name}:{exc.lineno or 0}: cannot parse the file ({exc.msg})"]

    out = []
    aliases = {}  # local name -> module name ("rookery" / "mail_gate")

    def bad(node, msg):
        out.append(f"{path_name}:{getattr(node, 'lineno', 0)}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("rookery", "mail_gate"):
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module in ("rookery", "mail_gate"):
                for a in node.names:
                    if a.name == "*":
                        names = [attr for (m, attr) in _WATCHED if m == node.module]
                        if any(not allowed(_WATCHED[(node.module, n)][1]) for n in names):
                            bad(node, f"star import from {node.module} can bring in a gate-bypass name")
                    elif (node.module, a.name) in _WATCHED:
                        rule, files = _WATCHED[(node.module, a.name)]
                        if not allowed(files):
                            bad(node, f"imports {node.module}.{a.name}; this file {rule.split(';', 1)[0].replace('calls ', 'may not use ')} ({rule.split(';', 1)[1].strip()})")
            if "herdr" in (node.module or "").lower() and base == "mail_gate.py" and not in_pkg:
                bad(node, "mail_gate.py imports herdr code (FR-021)")
        if isinstance(node, ast.Import) and base == "mail_gate.py" and not in_pkg:
            for a in node.names:
                if "herdr" in a.name.lower():
                    bad(node, "mail_gate.py imports herdr code (FR-021)")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = aliases.get(node.value.id)
            watched = _WATCHED.get((mod, node.attr))
            if watched and not allowed(watched[1]):
                bad(node, f"{watched[0]}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
              and len(node.args) >= 2 and isinstance(node.args[0], ast.Name)
              and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            watched = _WATCHED.get((aliases.get(node.args[0].id), node.args[1].value))
            if watched and not allowed(watched[1]):
                bad(node, f"getattr reaches {watched[0]}")
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and node.args[0].value in _DYN_MODULES and not allowed(SEND_OK)
              and ((isinstance(node.func, ast.Name) and node.func.id == "__import__")
                   or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"))):
            bad(node, f"imports {node.args[0].value} dynamically; only rookery.py and mail_gate.py may")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _INSERT_RE.search(node.value) and not allowed(INSERT_OK):
                bad(node, "inserts into the inbox directly; only rookery.py may")
            if _RECIPIENT_RE.search(node.value) and not allowed(RECIPIENT_UPDATE_OK):
                bad(node, "re-addresses inbox rows; only relay.py may")
    if not in_pkg:
        out += _feature_rules(path_name, base, source, tree)
    return sorted(set(out))


def _imported_roots(node):
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def _top_level_nodes(tree):
    """Every node that runs at import time: the walk does not enter a function or lambda body."""
    stack = [tree]
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(c)


def _feature_rules(path_name, base, source, tree):
    """Screener-chain rules (D23). `base` is a root-level file name."""
    out = []

    def bad(node, msg):
        out.append(f"{path_name}:{getattr(node, 'lineno', 0)}: {msg}")

    is_screener = base.startswith("screener") or base == "cli_screen.py"
    for node in ast.walk(tree):
        roots = _imported_roots(node)
        if base in NO_SCREENER_IMPORT and ({"screener", "screener_backend"} & set(roots)):
            bad(node, f"{base} imports screener or screener_backend")
        if base in NO_PROMPTCHAIN_IMPORT and "promptchain" in roots:
            bad(node, f"{base} imports promptchain")
        if base == "mail_screen.py" and "promptchain" in roots:
            bad(node, "mail_screen.py imports promptchain")
        if is_screener and isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
            if name == "quarantine":
                vals = [a for a in node.args] + [k.value for k in node.keywords]
                if any(isinstance(v, ast.Constant) and v.value == "loop" for v in vals):
                    bad(node, "a screener module passes the reason 'loop' to quarantine (FR-002)")
        # a model-call knob: a keyword, a name, an attribute, or a string that is exactly the key
        key = None
        if isinstance(node, ast.keyword) and node.arg in FORBIDDEN_KEYS:
            key = node.arg
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_KEYS:
            key = node.id
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_KEYS:
            key = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in FORBIDDEN_KEYS:
            key = node.value
        if key:
            bad(node, f"uses the model-call key {key!r}; the screener never sets one (FR-007, FR-034)")

    for node in _top_level_nodes(tree):
        roots = _imported_roots(node)
        if "promptchain" in roots:
            bad(node, "imports promptchain at the top level; import it inside the function that needs it")
        if base == "cli_screen.py" and "screener" in roots:
            bad(node, "cli_screen.py imports screener at the top level; import it inside a function (D23)")

    if base not in IP_OK:
        for m in re.finditer(r"192\.168\.0\.159", source):
            bad(ast.Pass(lineno=source.count("\n", 0, m.start()) + 1),
                "has the model host address; only screener_config.py and the default config may")
    return out


def repo_files():
    for d in SCAN_DIRS:
        for p in sorted(d.glob("*.py")):
            if p.name.startswith("test_") or p.name in SKIP_FILES or p.name.startswith("."):
                continue
            yield p


class ScanSyntheticTests(unittest.TestCase):
    """Prove the scanner catches each bypass shape (it is not vacuous)."""

    def assertCaught(self, name, src, needle):
        found = scan_source(name, src)
        self.assertTrue(any(needle in f for f in found), f"not caught: {found}")
        self.assertTrue(all(f.startswith(f"{name}:") and f.split(":")[1].isdigit() for f in found))

    def test_plain_send(self):
        self.assertCaught("x.py", "import rookery\nrookery.send(c, 'a', 'b', 'hi', session_id='s')\n", "rookery.send")

    def test_alias_send(self):
        self.assertCaught("x.py", "import rookery as R\nR.send(c)\n", "rookery.send")

    def test_from_import_send(self):
        self.assertCaught("x.py", "from rookery import send\nsend(c)\n", "imports rookery.send")

    def test_from_import_send_as(self):
        self.assertCaught("x.py", "from rookery import send as s\ns(c)\n", "imports rookery.send")

    def test_star_import(self):
        self.assertCaught("x.py", "from rookery import *\n", "star import")

    def test_getattr_send(self):
        self.assertCaught("x.py", "import rookery as R\nf = getattr(R, 'send')\n", "getattr")

    def test_insert_text(self):
        self.assertCaught("x.py", "q = 'INSERT   INTO\\n inbox(a) VALUES (1)'\n", "inserts into the inbox")
        self.assertCaught("x.py", "q = ('insert into '\n     'inbox(a)')\n", "inserts into the inbox")

    def test_insert_variants(self):
        for q in ("INSERT OR IGNORE INTO inbox(a) VALUES (1)", "insert or replace into inbox(a)",
                  "REPLACE INTO inbox(a)", 'INSERT INTO "inbox"(a)', "INSERT INTO `inbox`(a)",
                  "INSERT INTO [inbox](a)", "INSERT INTO main.inbox(a)", 'INSERT INTO "main"."inbox"(a)',
                  "INSERT /* c */ INTO inbox(a)", "INSERT -- c\n INTO inbox(a)"):
            self.assertCaught("x.py", f"q = {q!r}\n", "inserts into the inbox")

    def test_recipient_update_variants(self):
        for q in ("UPDATE inbox SET status=1, recipient=? WHERE id=1", 'UPDATE "inbox" SET recipient=?',
                  "UPDATE main.inbox SET status=1,recipient=?", "UPDATE /* c */ inbox SET recipient=?",
                  "UPDATE OR IGNORE inbox SET recipient=?"):
            self.assertCaught("x.py", f"q = {q!r}\n", "re-addresses")

    def test_dynamic_import(self):
        for mod in ("rookery", "mail_gate"):
            self.assertCaught("x.py", f"import importlib\nm = importlib.import_module({mod!r})\n", "dynamically")
            self.assertCaught("x.py", f"m = __import__({mod!r})\n", "dynamically")
        self.assertEqual(scan_source("x.py", "import importlib\nimportlib.import_module('json')\n"), [])
        self.assertEqual(scan_source("rookery.py", "m = __import__('mail_gate')\n"), [])

    def test_quarantine_call(self):
        self.assertCaught("x.py", "import rookery\nrookery.quarantine(c, 1, 'r', 'me')\n", "rookery.quarantine")
        self.assertCaught("x.py", "from rookery import quarantine as q\n", "imports rookery.quarantine")

    def test_system_send_wrong_file(self):
        self.assertCaught("x.py", "import mail_gate\nmail_gate.system_send(c, 'a', 'b', 'x', session_id='s')\n", "system_send")
        self.assertCaught("x.py", "from mail_gate import system_send as ss\n", "imports mail_gate.system_send")

    def test_recipient_update_outside_relay(self):
        self.assertCaught("x.py", "c.execute('UPDATE inbox SET recipient=? WHERE id=?')\n", "re-addresses")

    def test_herdr_import_in_gate(self):
        self.assertCaught("mail_gate.py", "import plugin_herdr\n", "herdr")
        self.assertCaught("mail_gate.py", "from plugin_herdr.bridge import x\n", "herdr")
        self.assertCaught("mail_gate.py", "import herdr_client\n", "herdr")

    def test_package_file_is_not_root_file(self):
        self.assertCaught("plugin_herdr/relay.py", "import mail_gate\nmail_gate.system_send()\n", "system_send")

    def test_escalate_and_decide_wrong_file(self):
        self.assertCaught("x.py", "import rookery\nrookery.escalate(c, 1, 'me', 'r')\n", "rookery.escalate")
        self.assertCaught("x.py", "import rookery\nrookery._decide(c, 1, 'held', 'escalated', 'v', 'r', 'me')\n", "rookery._decide")
        self.assertCaught("x.py", "from rookery import escalate\n", "imports rookery.escalate")

    def test_screener_loop_quarantine(self):
        for call in ("rookery.quarantine(c, 1, 'loop', 'me')", "rookery.quarantine(c, 1, reason='loop')"):
            self.assertCaught("screener.py", f"import rookery\n{call}\n", "'loop'")
            self.assertCaught("cli_screen.py", f"import rookery\n{call}\n", "'loop'")
        self.assertEqual(scan_source("rookery.py", "quarantine(c, 1, 'loop', 'me')\n"), [])

    def test_gate_files_never_import_screener(self):
        for f in ("mail_gate.py", "turn_block.py", "cli_hook.py", "screener_t0.py", "mail_screen.py"):
            self.assertCaught(f, "import screener\n", "imports screener")
            self.assertCaught(f, "import screener_backend\n", "imports screener")
            self.assertCaught(f, "from screener import x\n", "imports screener")
        for f in ("mail_gate.py", "turn_block.py", "cli_hook.py", "screener_t0.py"):
            self.assertCaught(f, "def f():\n    import promptchain\n", "imports promptchain")

    def test_promptchain_top_level_only(self):
        self.assertCaught("x.py", "import promptchain\n", "top level")
        self.assertCaught("x.py", "try:\n    from promptchain import X\nexcept ImportError:\n    X = None\n", "top level")
        self.assertEqual(scan_source("x.py", "def f():\n    import promptchain\n"), [])

    def test_cli_screen_imports_screener_in_function_only(self):
        self.assertCaught("cli_screen.py", "import screener\n", "top level")
        self.assertEqual(scan_source("cli_screen.py", "def f():\n    import screener\n"), [])

    def test_model_call_keys(self):
        for src in ("f(max_tokens=3)", "d = {'num_predict': 1}", "d = {'num_ctx': 1}", "d = {'think': False}",
                    "x = o.max_completion_tokens"):
            self.assertCaught("x.py", src + "\n", "model-call key")
        self.assertEqual(scan_source("x.py", '"""No think or max_tokens here."""\n# num_ctx\nthinking = 1\n'), [])

    def test_model_host_literal(self):
        self.assertCaught("x.py", "u = 'http://192.168.0.159:8799'\n", "model host")
        self.assertEqual(scan_source("screener_config.py", "u = 'http://192.168.0.159:8799'\n"), [])

    def test_clean_snippet_passes(self):
        src = ("import rookery\nimport mail_gate\nfrom rookery import connect\n"
               "mail_gate.submit(c, 'a', 'b', 'x')\nrookery.inbox(c)\n"
               "c.execute('UPDATE inbox SET status=1')\n"
               "c.execute('UPDATE inbox SET status=1 WHERE recipient=?')\n")
        self.assertEqual(scan_source("x.py", src), [])

    def test_allowed_files_pass(self):
        self.assertEqual(scan_source("mail_gate.py", "import rookery\nrookery.send(c)\nrookery.quarantine(c)\n"), [])
        self.assertEqual(scan_source("relay.py", "import mail_gate\nmail_gate.system_send(c)\n"
                                     "c.execute('UPDATE inbox SET recipient=?')\n"), [])
        self.assertEqual(scan_source("mail_screen.py", "import rookery as R\nR.quarantine(c)\n"), [])
        self.assertEqual(scan_source("rookery.py", "q='INSERT INTO inbox(a)'\n"), [])


class RepoScanTest(unittest.TestCase):
    def test_no_bypass_in_repo(self):
        files = list(repo_files())
        self.assertGreater(len(files), 10, "scan found too few files")
        names = {p.name for p in files}
        self.assertIn("mail_gate.py", names)
        self.assertIn("rookery.py", names)
        found = []
        for p in files:
            found += scan_source(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
        self.assertEqual(found, [], "gate bypass found:\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
