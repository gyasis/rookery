"""cli_init.py -- helpers for `rookery init` (D13): managed context section, init lock.

T030 upsert_section. T031 (merge_hooks) and T032 (main) extend this module.
Standard library only.
"""
import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

import turn_block

START_MARKER = "<!-- ROOKERY START -->"
END_MARKER = "<!-- ROOKERY END -->"


_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")   # same rule as turn_block
_START_LINE = re.compile(re.escape(START_MARKER) + r"[ \t]*\r?")
_END_LINE = re.compile(re.escape(END_MARKER) + r"[ \t]*\r?")

_held = {}   # absolute root -> [depth, open file]; one entry per root held by this process


class InitError(ValueError):
    """A problem init cannot fix: bad root, unreadable file, malformed markers."""


def _oserr(path, exc):
    return InitError(f"{path}: {exc.strerror or exc.__class__.__name__}")


@contextlib.contextmanager
def init_lock(root):
    """Exclusive flock on <root>/locks/init.lock (D13). The lock lives under the
    Rookery root, never in the host project (FR-023). Re-entrant within a process:
    a nested init_lock() on the same root returns at once; the flock is released
    when the outermost block exits. An empty or None root raises InitError."""
    if root is None or not str(root).strip():
        raise InitError("Rookery root is empty; set ROOKERY_ROOT")
    root = os.path.abspath(str(root))
    held = _held.get(root)
    if held is not None:
        held[0] += 1
        try:
            yield
        finally:
            held[0] -= 1
        return
    d = os.path.join(root, "locks")
    try:
        os.makedirs(d, exist_ok=True)
        fh = open(os.path.join(d, "init.lock"), "a")
    except OSError as exc:
        raise _oserr(d, exc)
    with fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _held[root] = [1, fh]
        try:
            yield
        finally:
            del _held[root]
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def write_text_atomic(path, content, mode=None):
    """Write text via a temp file in the same folder + fsync + os.replace (the
    rule of turn_block.write_json_atomic). Newlines are written as given; text is
    UTF-8 with surrogateescape, so bytes read the same way round-trip exactly.
    A symlink at path is followed: the TARGET is replaced and the link stays
    (a dangling link creates its target). Limits of os.replace: a hard link to the
    file is broken (the other names keep the old content) and a read-only file is
    replaced anyway when its folder is writable.
    mode: permission bits for the result; default keeps the existing file's
    mode, or 0o666 minus umask for a new file. Raises InitError on OSError."""
    path = os.path.realpath(os.path.abspath(path))
    d = os.path.dirname(path)
    tmp = None
    try:
        os.makedirs(d, exist_ok=True)
        if mode is None:
            try:
                mode = stat.S_IMODE(os.stat(path).st_mode)
            except FileNotFoundError:
                um = os.umask(0)
                os.umask(um)
                mode = 0o666 & ~um
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=os.path.splitext(path)[1])
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape",
                       newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise _oserr(path, exc)
    except BaseException:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _newline(old):
    """Return CRLF if the file uses CRLF throughout, else LF."""
    if "\r\n" in old and "\n" not in old.replace("\r\n", ""):
        return "\r\n"
    return "\n"


def _section(text, nl):
    body = nl.join(text.rstrip("\n").split("\n"))
    return START_MARKER + nl + body + nl + END_MARKER + nl


def _marker_lines(old):
    """Return ([(begin, end_incl_newline)] for START lines, same for END lines):
    whole-line markers outside fenced code blocks only."""
    starts, ends = [], []
    fence = None   # (char, length) while inside a fenced block
    pos, n = 0, len(old)
    while pos < n:
        nxt = old.find("\n", pos)
        end = n if nxt < 0 else nxt + 1
        line = old[pos:end].rstrip("\n")
        m = _FENCE_LINE.match(line.rstrip("\r"))
        if fence is None:
            if m:
                run, info = m.group(1), m.group(2).strip()
                if not (run[0] == "`" and "`" in info):
                    fence = (run[0], len(run))
            elif _START_LINE.fullmatch(line):
                starts.append((pos, end))
            elif _END_LINE.fullmatch(line):
                ends.append((pos, end))
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not m.group(2).strip():
            fence = None
        pos = end
    return starts, ends


def _upsert(path, text):
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape",
                  newline="") as fh:
            old = fh.read()
    except FileNotFoundError:
        write_text_atomic(path, _section(text, "\n"))
        return "created"
    except OSError as exc:
        raise _oserr(path, exc)

    nl = _newline(old)
    section = _section(text, nl)
    starts, ends = _marker_lines(old)
    if not starts and not ends:
        # Insert: the original bytes stay a prefix. Add only the newline needed to
        # end the last line, then one blank line, then the section.
        # An empty file gets the bare section.
        prefix = old
        if prefix:
            if not prefix.endswith("\n"):
                prefix += nl
            if not re.search(r"\r?\n\r?\n\Z", prefix):
                prefix += nl
        write_text_atomic(path, prefix + section)
        return "inserted"

    if len(starts) != 1 or len(ends) != 1 or ends[0][0] < starts[0][0]:
        raise InitError(
            f"{path}: malformed Rookery markers ({len(starts)} START, {len(ends)} END"
            f"{', END before START' if starts and ends and ends[0][0] < starts[0][0] else ''}); "
            f"fix the file by hand. Rookery wrote nothing.")
    a, e = starts[0][1], ends[0][0]
    new_body = nl.join(text.rstrip("\n").split("\n")) + nl
    if old[a:e] == new_body:
        return "unchanged"
    write_text_atomic(path, old[:a] + new_body + old[e:])
    return "replaced"


def check_section(path):
    """Raise InitError when the file cannot be read or has malformed markers. Writes nothing."""
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            old = fh.read()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _oserr(path, exc)
    starts, ends = _marker_lines(old)
    if (starts or ends) and (len(starts) != 1 or len(ends) != 1 or ends[0][0] < starts[0][0]):
        raise InitError(f"{path}: malformed Rookery markers ({len(starts)} START, {len(ends)} END); "
                        f"fix the file by hand. Rookery wrote nothing.")


def upsert_section(path, text):
    """Create the file, or insert/replace the marked Rookery section (D13).

    Returns "created" | "inserted" | "replaced" | "unchanged". Writes only when
    content changes. Bytes outside the markers are never modified (the file is read
    as UTF-8 with surrogateescape, so non-UTF-8 bytes survive). The section uses the
    file's own newline (CRLF only if the file is CRLF throughout, else LF). A marker
    counts only as a whole line outside a fenced code block; anywhere else it is
    plain text. A symlink is followed and kept; see write_text_atomic for hard-link
    and read-only behaviour. Raises InitError (a ValueError) on malformed markers
    (more than one START or END line, END before START, only one of them) or on any
    OSError, writing nothing. The caller (T032 main) holds init_lock around the
    whole init; this function takes no lock.
    """
    return _upsert(path, text)


# ---- settings.json hook merge (T031) -------------------------------------------------

HOOK_EVENTS = (("SessionStart", "session-start"), ("UserPromptSubmit", "prompt"),
               ("Stop", "stop"))
# permissions.allow rules init merges into settings.json. The list only pre-approves
# these commands; it blocks nothing else. No pane send-keys/send-text: an agent that
# mail tricks must not type into a shell. Agents wake agents with `herdr agent prompt`.
PERMISSION_RULES = (
    "Bash(rookery inbox:*)", "Bash(rookery peek:*)", "Bash(rookery ack:*)",
    "Bash(rookery identity show:*)", "Bash(rookery turn:*)",
    "Bash(herdr pane read:*)", "Bash(herdr agent list:*)",
    "Bash(herdr agent prompt:*)", "Bash(herdr agent start:*)",
    "Bash(herdr agent wait:*)",
)
_CMD_PREFIX = "rookery hook "
_OURS = re.compile(r"(?:\S*/)?rookery hook (session-start|prompt|stop) "
                   r"--project ([A-Za-z0-9._-]{1,64})")


def _is_rookery(handler):
    """True only for a handler this tool wrote: type "command" (or absent) and a
    command that, stripped, is exactly `[path/]rookery hook <sub> --project <name>`."""
    return (isinstance(handler, dict) and handler.get("type", "command") == "command"
            and isinstance(handler.get("command"), str)
            and _OURS.fullmatch(handler["command"].strip()) is not None)


def _detect_indent(text):
    """Indent of the first indented line: "\t" or the count of leading spaces; default 2."""
    for line in text.split("\n"):
        if line[:1] == "\t":
            return "\t"
        stripped = line.lstrip(" ")
        if stripped.strip() and stripped != line:
            return len(line) - len(stripped)
    return 2


def _merge_event(path, hooks, event, cmd):
    """Merge one event into the hooks dict; return "added" | "updated" | "unchanged"."""
    groups = hooks.get(event)
    if groups is None and event not in hooks:
        hooks[event] = [{"hooks": [{"type": "command", "command": cmd}]}]
        return "added"
    if not isinstance(groups, list):
        raise InitError(f"{path}: hooks.{event} is not a list; fix the file by hand. "
                        f"Rookery wrote nothing.")
    for i, g in enumerate(groups):
        if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
            raise InitError(f"{path}: hooks.{event}[{i}] is not an object with a "
                            f"\"hooks\" list; fix the file by hand. Rookery wrote nothing.")
    first = None   # the first rookery handler
    changed = False
    for g in list(groups):
        removed = False
        for h in list(g["hooks"]):
            if not _is_rookery(h):
                continue
            if first is None:
                first = h
                if h["command"] != cmd:
                    h["command"] = cmd
                    changed = True
            else:
                g["hooks"].remove(h)   # duplicate: it would fire the hook twice
                changed = removed = True
        if removed and not g["hooks"]:
            groups.remove(g)
    if first is None:
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
        return "added"
    return "updated" if changed else "unchanged"


def _merge_permissions(path, data):
    """Append the missing PERMISSION_RULES to data["permissions"]["allow"]. Never
    changes other keys or entries. A rule listed in deny or ask is skipped. Returns
    {"added", "unchanged", "skipped"}; raises InitError on a bad shape."""
    perms = data.get("permissions")
    if perms is None and "permissions" not in data:
        perms = {}
    if not isinstance(perms, dict):
        raise InitError(f"{path}: \"permissions\" is not an object; fix the file by "
                        f"hand. Rookery wrote nothing.")
    lists = {}
    for key in ("allow", "deny", "ask"):
        v = perms.get(key, [])
        if not isinstance(v, list):
            raise InitError(f"{path}: permissions.{key} is not a list; fix the file by "
                            f"hand. Rookery wrote nothing.")
        lists[key] = v
    restricted = {r for k in ("deny", "ask") for r in lists[k] if isinstance(r, str)}
    have = {r for r in lists["allow"] if isinstance(r, str)}
    res = {"added": [], "unchanged": [], "skipped": []}
    for rule in PERMISSION_RULES:
        if rule in have:
            res["unchanged"].append(rule)
        elif rule in restricted:
            res["skipped"].append(rule)
        else:
            res["added"].append(rule)
    if res["added"]:
        allow = lists["allow"]
        allow.extend(res["added"])
        perms["allow"] = allow
        data["permissions"] = perms
    return res


def _merge_hooks(path, project, with_permissions=False):
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape",
                  newline="") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = None
    except OSError as exc:
        raise _oserr(path, exc)

    bom = ""
    nl = "\n"
    if old is not None:
        if old.startswith("\ufeff"):
            bom, old = "\ufeff", old[1:]
        if "\r\n" in old:
            nl = "\r\n"
    if old is None or not old.strip():
        data, indent, tail = {}, 2, "\n"
    else:
        def bad(why):
            return InitError(f"settings file {path} is not valid JSON: {why}. "
                             f"Fix the file, then run init again.")

        def pairs(items):
            d = {}
            for k, v in items:
                if k in d:
                    raise bad(f"duplicate key {k!r} appears twice and JSON keeps only one value")
                d[k] = v
            return d

        def const(name):
            raise bad(f"{name} is not allowed in JSON")

        def num(text):
            f = float(text)
            if f in (float("inf"), float("-inf")):
                raise bad(f"number {text} is out of range")
            return f
        try:
            data = json.loads(old, object_pairs_hook=pairs, parse_constant=const,
                              parse_float=num)
        except json.JSONDecodeError as exc:
            raise InitError(f"settings file {path} is not valid JSON: line {exc.lineno} "
                            f"column {exc.colno}: {exc.msg}. Fix the file, then run init "
                            f"again.")
        if not isinstance(data, dict):
            raise InitError(f"{path}: the settings file must hold one JSON object; "
                            f"fix the file by hand. Rookery wrote nothing.")
        indent = _detect_indent(old)
        tail = "\n" if old.endswith("\n") else ""
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InitError(f"{path}: \"hooks\" is not an object; fix the file by hand. "
                        f"Rookery wrote nothing.")
    result = {"added": [], "updated": [], "unchanged": []}
    for event, sub in HOOK_EVENTS:
        cmd = f"{_CMD_PREFIX}{sub} --project {project}"
        result[_merge_event(path, hooks, event, cmd)].append(event)
    changed = bool(result["added"] or result["updated"])
    if with_permissions:
        result["permissions"] = _merge_permissions(path, data)
        changed = changed or bool(result["permissions"]["added"])
    if old is None or changed:
        out = json.dumps(data, indent=indent, ensure_ascii=False) + tail
        write_text_atomic(path, bom + (out.replace("\n", nl) if nl != "\n" else out))
    return result


def merge_hooks(path, project, root=None, with_permissions=False):
    """Merge the three Rookery hooks into a Claude Code settings.json (D13).

    A handler is ours only if its command, stripped, fully matches
    `[path/]rookery hook (session-start|prompt|stop) --project NAME` (type "command"
    or absent); any other command, e.g. one with `&& other`, is the user's and is
    never modified or removed. An ours-handler with another subcommand or project is
    rewritten in place (a project rename).
    Events: SessionStart -> `rookery hook session-start --project P`, UserPromptSubmit
    -> `... prompt ...`, Stop -> `... stop ...`. Returns {"added", "updated",
    "unchanged"}: lists of event names. Per event: no ours-handler -> append a new
    group; the first ours-handler is updated in place (same position) when its command
    differs; later ours-handlers in the same event are removed as duplicates (a group they leave empty is dropped) and the event
    counts as "updated".
    A missing, empty or whitespace-only file is treated as `{}`; a missing file is created (2-space indent, trailing newline), with the .claude/
    folder. An existing file keeps its key order, its indent (first indented line:
    tab or space count; default 2), its trailing-newline state, CRLF line endings
    and a UTF-8 BOM. LIMIT: a changed
    file is re-serialised with json.dumps, so other cosmetic formatting (compact
    arrays, \\u escapes, blank lines) is normalised; an unchanged merge writes nothing,
    so the bytes and mtime stay as they were.
    Raises InitError, writing nothing, for: unsafe project name; invalid JSON (message
    has the path, line, column and the contract sentence), including duplicate keys,
    NaN/Infinity and numbers that overflow to infinity; non-object JSON, `hooks` not an object, an event value not a list, or a group without a
    `hooks` list. With root given, runs under init_lock(root) (re-entrant).
    With with_permissions=True it also merges PERMISSION_RULES into permissions.allow
    (see _merge_permissions) and the result gains a "permissions" key; a non-object
    `permissions` or a non-list allow/deny/ask raises InitError. The rules only
    pre-approve commands; nothing is blocked."""
    if not turn_block.is_safe_name(project):
        raise InitError(f"unsafe project name: {project!r} (use 1-64 of A-Z a-z 0-9 . _ -)")
    if root is None:
        return _merge_hooks(path, project, with_permissions)
    with init_lock(root):
        return _merge_hooks(path, project, with_permissions)


# ---- `rookery init` command (T032) ---------------------------------------------------

HARNESSES = ("claude", "codex", "pi", "omp")
_HERE = os.path.dirname(os.path.abspath(__file__))
_NEXT = ("Open agent sessions do not join. Start a new Rookery session with new panes "
         "and agents: rookery session new --name <name>.")


def _default_project(d):
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        top = r.stdout.strip()
        if r.returncode == 0 and top:
            return os.path.basename(top)
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.basename(d)


def _data_text(name):
    """Text of a data file shipped beside this module (or inside the zipapp)."""
    path = os.path.join(_HERE, name)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        # Inside the zipapp open() cannot reach a path in the archive: use the loader,
        # as rookery._load_schema() does.
        loader = globals().get("__loader__")
        if loader is None or not hasattr(loader, "get_data"):
            raise _oserr(path, exc)
        try:
            return loader.get_data(os.path.join(getattr(loader, "archive", _HERE),
                                                name)).decode("utf-8")
        except OSError as exc2:
            raise _oserr(path, exc2)


def _section_text(root, project):
    return _data_text("rookery_section.md").replace("{root}", root).replace("{project}", project)


# pi and omp load a TypeScript extension that calls the same `rookery hook` commands
# as the Claude Code hooks. Project-level folders: pi `.pi/extensions/`, omp `.omp/extensions/`.
EXTENSION_FILE = "rookery_pi_extension.ts"
EXTENSION_DIRS = {"pi": ".pi", "omp": ".omp"}
_EXTENSION_PLACEHOLDER = "__ROOKERY_PROJECT__"


def _extension_text(project):
    return _data_text(EXTENSION_FILE).replace(_EXTENSION_PLACEHOLDER, project)


def install_extension(path, text):
    """Write the extension file atomically. Returns "created" | "replaced" | "unchanged"."""
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            old = fh.read()
    except FileNotFoundError:
        write_text_atomic(path, text)
        return "created"
    except OSError as exc:
        raise _oserr(path, exc)
    if old == text:
        return "unchanged"
    write_text_atomic(path, text)
    return "replaced"


def main(args):
    """rookery init: upsert the context section, merge Claude hooks. Exit 0 or 2."""
    try:
        d = os.path.abspath(args.dir or os.getcwd())
        if not os.path.isdir(d):
            raise InitError(f"{d}: not a directory")
        project = args.project or _default_project(d)
        if not turn_block.is_safe_name(project):
            raise InitError(f"unsafe project name: {project!r} (use 1-64 of "
                            f"A-Z a-z 0-9 . _ -; pass --project)")
        env_root = os.environ.get("ROOKERY_ROOT")
        if env_root and not os.path.isabs(env_root):
            raise InitError(f"ROOKERY_ROOT must be an absolute path, not {env_root!r}")
        root = os.path.abspath(turn_block.project_root(project))
        if os.path.commonpath([root, d]) == d:   # FR-023: no Rookery file in the host repo
            raise InitError(f"the Rookery root {root} is inside {d}; use a root outside the project")
        claude = args.harness == "claude"
        ctx = os.path.join(d, "CLAUDE.md" if claude else "AGENTS.md")
        sp = os.path.join(d, ".claude", "settings.json")
        with init_lock(root):
            # Check the context file first, then write settings (the write most likely
            # to fail), then the context file: a failure never half-installs Rookery.
            check_section(ctx)
            text = _section_text(root, project)   # load before any write
            ext_path = ext_text = None
            if args.harness in EXTENSION_DIRS:
                ext_path = os.path.join(d, EXTENSION_DIRS[args.harness], "extensions",
                                        "rookery.ts")
                ext_text = _extension_text(project)
            settings = (merge_hooks(sp, project, with_permissions=True)
                        if claude else None)
            try:
                action = upsert_section(ctx, text)
            except InitError as exc:
                if settings and (settings["added"] or settings["updated"]
                                 or settings["permissions"]["added"]):
                    raise InitError(f"Rookery updated {sp} but not {ctx}: {exc}") from exc
                raise
            extension = None
            if ext_path is not None:
                try:
                    extension = {"path": ext_path,
                                 "action": install_extension(ext_path, ext_text)}
                except InitError as exc:
                    raise InitError(f"Rookery updated {ctx} but not {ext_path}: {exc}") from exc
    except InitError as exc:
        print(f"rookery init: {exc}", file=sys.stderr)
        sys.exit(2)
    if settings is not None:
        settings = {"path": sp, **settings}
    if args.json:
        print(json.dumps({"ok": True, "context_file": {"path": ctx, "action": action},
                          "settings": settings, "project": project,
                          **({"extension": extension} if extension else {})}))
        return
    print(f"Project: {project}")
    print(f"{ctx}: {action}")
    if settings is not None:
        print(f"{sp}: added {', '.join(settings['added']) or '-'}; updated "
              f"{', '.join(settings['updated']) or '-'}; unchanged "
              f"{', '.join(settings['unchanged']) or '-'}")
        pm = settings["permissions"]
        line = (f"permissions: added {len(pm['added'])} allow rules" if pm["added"]
                else "permissions: unchanged")
        if pm["skipped"]:
            line += f"; skipped {len(pm['skipped'])} (in deny or ask): {', '.join(pm['skipped'])}"
        print(line)
    if extension is not None:
        print(f"{extension['path']}: {extension['action']}")
    print(_NEXT)


def add_parser(sub):
    p = sub.add_parser("init", help="Write the Rookery section and hooks into a project.")
    p.add_argument("--harness", choices=HARNESSES, default="claude")
    p.add_argument("--project", default=None, help="project name (default: git top-level folder)")
    p.add_argument("--dir", default=None, help="project folder (default: current folder)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=main)
