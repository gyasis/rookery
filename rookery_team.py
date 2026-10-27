"""rookery_team — read and check a team file for `rookery up` (FR-004, FR-009, FR-019, FR-021, D4, D17)."""
import json
import os
import subprocess

import rookery_identity

HARNESSES = ("claude", "pi", "omp")
ROLES = ("reader", "writer", "boss")  # a boss reads: no claims, no repo (D14, FR-029)


class TeamError(Exception):
    """errors: a list of {"index": int|None, "name": str|None, "problem": str}."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(e["problem"] for e in errors))


def _safe(value, what):
    try:
        rookery_identity.safe_name(value, what)
        return None
    except ValueError:
        return f"The {what} is not valid. Use 1 to 64 characters from A-Z a-z 0-9 . _ - and not . or .."


class _GitMissing(Exception):
    pass


def _git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True)
    except OSError:
        raise _GitMissing()


def _branch_ok(session, name, task_id):
    return _git("check-ref-format", "--branch", f"agent/{session}/{name}/{task_id}").returncode == 0


def _git_top(repo):
    """Return the realpath of repo when it is a git top folder, else None."""
    r = _git("-C", repo, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        return None
    top = os.path.realpath(r.stdout.strip())
    return top if top == os.path.realpath(repo) else None


def _is_text_list(value):
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _fail(problem):
    return TeamError([{"index": None, "name": None, "problem": problem}])


def _bad_claim(c):
    return (not isinstance(c, str) or not c or c.startswith("/") or "\0" in c
            or ".." in c.split("/"))


def load(path, config=None, session=None, make_name=None) -> dict:
    """Return the team with defaults filled. Raise TeamError with every problem found.
    session (the --session flag) wins over the file's name; with no name from either,
    make_name() is called, and only then (T017)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        raise _fail("The team file is not UTF-8 text.")
    except OSError as e:
        raise _fail(f"Rookery cannot open the team file: {e.strerror}.")
    try:
        team = json.loads(text)
    except json.JSONDecodeError as e:
        raise _fail(f"The team file is not valid JSON. Check line {e.lineno}.")
    except (RecursionError, ValueError):
        raise _fail("The team file is not valid JSON. It nests too deep.")
    if config is None:
        config = rookery_identity.load_config(persist=False)
    errors = []

    def err(index, name, problem):
        errors.append({"index": index, "name": name, "problem": problem})

    if not isinstance(team, dict):
        raise _fail("The team file must hold one JSON object.")
    in_file = team.get("session")
    session = session if session is not None else in_file
    if session is None and make_name is not None:
        session = make_name()
    if session is None:
        err(None, None, "The team file has no session name.")
    elif (p := _safe(session, "session name")):
        err(None, None, p)
    elif session == "legacy" or rookery_identity._ID_LOOKALIKE.fullmatch(session):
        err(None, None, "Rookery keeps this session name for itself, or it looks like a Rookery ID. "
                        "Choose another session name.")
    if "task" in team and not isinstance(team["task"], str):
        err(None, None, "The task must be text.")
    if "acceptance" in team and not _is_text_list(team["acceptance"]):
        err(None, None, "The acceptance must be a list of text.")
    if "ask_budget" in team:
        b = team["ask_budget"]
        if not isinstance(b, int) or isinstance(b, bool) or b < 1:
            err(None, None, "The ask_budget must be a positive whole number.")
    agents = team.get("agents")
    if agents is None:
        err(None, None, "The team file has no agents list.")
        agents = []
    elif not isinstance(agents, list) or not agents:
        err(None, None, "The agents list must have one or more entries.")
        agents = []

    launch = config.get("launch")
    default_binding = config.get("default_binding")
    seen = set()
    out = []
    git_missing = False
    for i, a in enumerate(agents):
        if not isinstance(a, dict):
            err(i, None, "This agent entry must be a JSON object.")
            continue
        name = a.get("name")
        shown = name if isinstance(name, str) else None
        problems = []
        name_ok = False
        if name is None:
            problems.append("The agent has no name.")
        elif (p := _safe(name, "agent name")):
            problems.append(p)
        elif name.casefold() in seen:
            problems.append("Another agent has this name. Each name must be unique.")
        else:
            name_ok = True
        if isinstance(name, str):
            seen.add(name.casefold())
        harness = a.get("harness")
        if harness == "codex":
            problems.append("Codex waits for R19.")
        elif harness not in HARNESSES:
            problems.append("The harness must be claude, pi or omp.")
        role = a.get("role")
        if role not in ROLES:
            problems.append("The role must be reader, writer or boss.")
        repo = None
        if role == "boss" and (a.get("claims") or a.get("repo") is not None or a.get("task_id") is not None):
            problems.append("A boss reads files and holds no claims. Remove claims, repo and task_id.")
        if role == "writer":
            task_id = a.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                problems.append("A writer needs a task_id.")
            elif _safe(task_id, "task_id"):
                problems.append("The task_id must be one safe name. Use 1 to 64 characters from "
                                "A-Z a-z 0-9 . _ - and not . or ..")
            elif name_ok and isinstance(session, str) and not _safe(session, "session name") \
                    and not git_missing:
                try:
                    if not _branch_ok(session, name, task_id):
                        problems.append("The session, name and task_id do not make a valid branch name.")
                except _GitMissing:
                    git_missing = True
            repo = a.get("repo")
            if repo is not None:
                if not isinstance(repo, str) or not repo:
                    problems.append("The repo must be a folder path.")
                elif "\0" in repo:
                    problems.append("The repo path is not valid.")
                else:
                    repo = os.path.join(os.path.dirname(os.path.abspath(path)), repo)
                    try:
                        top = None if git_missing else _git_top(repo)
                    except (_GitMissing, ValueError) as e:
                        top = None
                        if isinstance(e, _GitMissing):
                            git_missing = True
                    if not git_missing:
                        if top is None:
                            problems.append("The repo is not the top folder of a git repository.")
                        else:
                            repo = top
            if git_missing:
                problems.append("Git is not installed.")
                problems = [x for x in problems if not x.startswith(("The session, name", "The repo is not the top"))]
                git_missing = True
        binding = a.get("binding", default_binding)
        if isinstance(launch, dict):
            if binding is None:
                problems.append("The agent has no binding, and the configuration has no default binding.")
            elif not isinstance(binding, str) or binding not in launch:
                problems.append("The binding is not a key of launch in the configuration.")
        elif "binding" in a and _safe(binding, "binding"):
            problems.append("The binding must be a safe name.")
        if "persona" in a and not isinstance(a["persona"], str):
            problems.append("The persona must be text.")
        if "scope" in a:
            sc = a["scope"]
            if not isinstance(sc, dict) or not all(_is_text_list(sc[k]) for k in sc if k in ("does", "does_not")):
                problems.append("The scope must be an object with does and does_not lists of text.")
        if "claims" in a:
            cl = a["claims"]
            if not isinstance(cl, list) or any(_bad_claim(c) for c in cl):
                problems.append("The claims must be a list of relative globs. "
                                "No glob is empty, absolute, or has a .. part.")
        for p in problems:
            err(i, shown, p)
        entry = dict(a)
        entry["binding"] = binding
        entry["repo"] = repo
        out.append(entry)

    if errors:
        raise TeamError(errors)
    result = dict(team)
    result["session"] = session
    if in_file is not None and in_file != session:
        result["session_overridden"] = in_file   # the flag won; `up` prints a note
    result["agents"] = out
    return result
