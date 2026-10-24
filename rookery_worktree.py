"""rookery_worktree — one git worktree per writer (specs/003-rookery-up, research D12, FR-020..FR-021a).

Path <root>/wt/<session>/<name>/<repo basename>, branch agent/<session>/<name>/<task_id>.
The `worktrees` registry row is the only proof Rookery owns a path. No function here deletes a row or a branch.
"""

import fnmatch
import os
import subprocess
import sys
from datetime import datetime, timezone


class Refusal(Exception):
    """The session cannot be used. The message is the text for the user."""


def path_for(root, session_name, agent_name, repo):
    return os.path.join(os.path.abspath(root), "wt", session_name, agent_name,
                        os.path.basename(os.path.normpath(repo)))


def branch_for(session_name, agent_name, task_id):
    return f"agent/{session_name}/{agent_name}/{task_id}"


_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
          "GIT_NAMESPACE", "GIT_PREFIX")


def git_env():
    """The environment for every git call: a git hook's inherited variables must not retarget git."""
    return {k: v for k, v in os.environ.items() if k not in _SCRUB}


def _owned(conn, path):
    return conn.execute("SELECT 1 FROM worktrees WHERE path=?", (path,)).fetchone() is not None


def check_session_free(conn, root, session_name):
    """Refuse when <root>/wt/<session> exists (a link counts) and no registry row has a path under it."""
    folder = os.path.join(os.path.abspath(root), "wt", session_name)
    if not os.path.lexists(folder):
        return
    prefix = folder + os.sep
    for (p,) in conn.execute("SELECT path FROM worktrees"):
        if p.startswith(prefix):
            return
    raise Refusal(f"The folder {folder} exists and Rookery did not make it. Choose a new session name.")


def check_agent_free(conn, root, session_name, agent_name, repo):
    """Refuse an agent path that exists and has no registry row."""
    path = path_for(root, session_name, agent_name, repo)
    if os.path.lexists(path) and not _owned(conn, path):
        raise Refusal(f"The folder {path} exists and Rookery did not make it. "
                      "Move it, or rename the agent.")


def _same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def register(conn, root, session_name, session_id, agent_name, agent_id, repo, task_id, now):
    """Inside the up transaction (no commit). Return the row dict, with 'notes' and maybe 'refuse'.
    A 'failed' row goes back to 'pending'; a 'finished' row starts again with the team's branch."""
    path = path_for(root, session_name, agent_name, repo)
    branch = branch_for(session_name, agent_name, task_id)
    out = {"path": path, "repo": repo, "branch": branch, "name": agent_name, "notes": []}
    row = conn.execute("SELECT state, branch, repo FROM worktrees WHERE path=?", (path,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO worktrees(path, session_id, agent_id, repo, branch, state, created_at) "
                     "VALUES(?,?,?,?,?,'pending',?)", (path, session_id, agent_id, repo, branch, now))
        return out
    if not _same(row[2], repo):
        out["refuse"] = f"the worktree belongs to {row[2]}"
        return out
    if row[0] == "finished":
        conn.execute("UPDATE worktrees SET state='pending', branch=?, finished_at=NULL WHERE path=?",
                     (branch, path))
        out["notes"].append(f"{agent_name}: starts a new worktree; the old branch stays")
        return out
    if row[1] != branch:
        out["branch"] = row[1]
        out["notes"].append(f"{agent_name}: task_id changed; the worktree stays on {row[1]}. "
                            "Run rookery finish first.")
    if row[0] == "failed":
        conn.execute("UPDATE worktrees SET state='pending' WHERE path=?", (path,))
    return out


def _git(*args, raw=False, timeout=60):
    """Run git with the scrubbed env. Output is stripped unless raw (porcelain needs its leading space)."""
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout, env=git_env())
    if raw:
        return r.returncode, r.stdout, r.stderr.strip()
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _err(stderr, stdout):
    lines = [x for x in (stderr or stdout).splitlines() if not x.startswith("Preparing worktree")]
    return "\n".join(lines).strip()


def _listed(repo, path):
    """Return (True, branch or None) when `git worktree list` of repo has path, else (False, None)."""
    code, out, _ = _git("-C", repo, "worktree", "list", "--porcelain")
    if code != 0:
        return False, None
    for block in out.split("\n\n"):
        entry = dict(l.split(" ", 1) if " " in l else (l, "") for l in block.splitlines())
        if "worktree" in entry and _same(entry["worktree"], path) and os.path.isdir(path):
            ref = entry.get("branch")
            return True, (ref[len("refs/heads/"):] if ref and ref.startswith("refs/heads/") else None)
    return False, None


def _set(conn, path, state):
    conn.execute("UPDATE worktrees SET state=? WHERE path=?", (state, path))
    conn.commit()


def add(conn, row):
    """Make (or reuse) the worktree for a registered row. Return (path or None, git error or None, note or None)."""
    path, repo, branch = row["path"], row["repo"], row["branch"]
    if row.get("refuse"):
        return None, row["refuse"], None
    try:
        found, cur = _listed(repo, path)
        if found:
            _set(conn, path, "active")
            note = None
            if cur != branch:
                note = f"{row['name']}: the worktree is on {cur or 'a detached HEAD'}, not {branch}"
            return path, None, note
        code, _, _ = _git("-C", repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        if code == 0:
            code, so, se = _git("-C", repo, "worktree", "add", path, branch)
        else:
            code, so, se = _git("-C", repo, "worktree", "add", "-b", branch, path, "HEAD")
        text = _err(se, so)
    except (OSError, subprocess.SubprocessError) as e:
        code, text = 1, f"git could not run: {e}"
    if code == 0:
        _set(conn, path, "active")
        return path, None, None
    _set(conn, path, "failed")
    return None, text or f"git worktree add exited {code}", None


def _row_for(conn, session, name):
    import rookery_identity as ri
    sess = ri.get_session(conn, session) if session else None
    if sess is None:
        raise Refusal(f"No session {session!r}. Pass --session with a session name, or set ROOKERY_SESSION."
                      if session else "No session. Pass --session NAME, or set ROOKERY_SESSION.")
    ident = ri.get_identity(conn, name, sess["session_id"])
    row = None
    if ident is not None:
        row = conn.execute("SELECT path, repo, branch FROM worktrees WHERE session_id=? AND agent_id=? "
                           "AND state='active'", (sess["session_id"], ident["agent_id"])).fetchone()
    if row is None:
        raise Refusal(f"{name} has no active worktree in session {sess['name']}. "
                      "Check the name with rookery gc or the team file.")
    return sess, dict(path=row[0], repo=row[1], branch=row[2])


def _changed(path):
    """Changed files of a worktree, as repo-relative names. Reads NUL-separated porcelain, no stripping."""
    code, out, _ = _git("-C", path, "status", "--porcelain", "-z", "-uall", raw=True)
    fields = out.split("\0")
    files, i = [], 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:
            continue
        files.append(rec[3:])
        if rec[0] in "RC" or rec[1] in "RC":
            i += 1          # a rename record is followed by the old path
    return files


def claim_matches(pattern, relpath):
    """True when a claim glob covers a repo-relative path. `*` crosses `/`, so `src/*.py` covers src/a/b.py.
    The guard hook uses this same function."""
    return fnmatch.fnmatchcase(relpath, pattern)


def _claimed(conn, sess, name, files):
    hits = []
    rows = conn.execute("SELECT c.pattern, i.name FROM claims c JOIN identities i ON i.agent_id=c.agent_id "
                        "WHERE c.session_id=? AND c.active=1 AND i.name<>?", (sess["session_id"], name))
    for pattern, owner in rows.fetchall():
        for f in files:
            if claim_matches(pattern, f):
                hits.append({"file": f, "claimed_by": owner, "pattern": pattern})
    return hits


_SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*")
BIG_FILE = 5 * 1024 * 1024


def _risky(path, files):
    found = []
    for f in files:
        base = os.path.basename(f)
        full = os.path.join(path, f)
        if any(fnmatch.fnmatchcase(base, p) for p in _SECRET_NAMES):
            found.append(f)
        elif os.path.isfile(full) and os.path.getsize(full) > BIG_FILE:
            found.append(f)
    return found


def _under_root(path, root):
    base = os.path.join(os.path.abspath(root), "wt") + os.sep
    return os.path.abspath(path).startswith(base)


def finish(conn, root, session, name, message, ask=input, env=os.environ, out=sys.stdout,
           timeout=60, push_timeout=600):
    """Show the change, confirm 3 times (or BROTUNDWASSER=1), commit, push, remove the worktree, mark the row
    finished. Any step that fails stops here with the row still active. Never deletes the branch."""
    try:
        return _finish(conn, root, session, name, message, ask, env, out, timeout, push_timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refusal(f"finish stopped: {type(e).__name__}: {e}. The worktree stays active.")


def _finish(conn, root, session, name, message, ask, env, out, timeout, push_timeout):
    sess, row = _row_for(conn, session, name)
    path, repo, branch = row["path"], row["repo"], row["branch"]
    if root and not _under_root(path, root):
        raise Refusal(f"The worktree {path} is not under this Rookery root.")

    def say(text):
        print(text, file=out)
    files = _changed(path)
    claimed = _claimed(conn, sess, name, files)
    say(f"Worktree {path} on {branch}")
    say(_git("-C", path, "-c", "core.quotepath=off", "status", "--short", raw=True)[1].rstrip("\n")
        or "(no changes)")
    say(_git("-C", path, "diff", "--stat", "HEAD")[1])
    for c in claimed:
        say(f"{c['file']} claimed by {c['claimed_by']} ({c['pattern']})")
    risky = _risky(path, files)
    if risky:
        say("Check these files before you commit: " + ", ".join(risky))
    if env.get("BROTUNDWASSER") != "1":
        for n in (1, 2, 3):
            out.write(f"Type yes to continue ({n} of 3): ")
            out.flush()
            try:
                answer = ask("")
            except EOFError:
                answer = ""
            if answer.strip() != "yes":
                raise Refusal("Stopped. Nothing changed.")
    result = {"name": name, "path": path, "branch": branch, "committed": False, "pushed": False,
              "claimed": claimed, "notes": []}

    def step(label, *args, limit=timeout):
        try:
            code, so, se = _git(*args, timeout=limit)
        except (OSError, subprocess.SubprocessError) as e:
            raise Refusal(f"finish stopped at {label}: {type(e).__name__}: {e}. The worktree stays active.")
        if code != 0:
            raise Refusal(f"finish stopped at {label}: {se or so}. The worktree stays active.")
        return so
    if _git("-C", path, "status", "--porcelain")[1]:
        step("add", "-C", path, "add", "-A")
        step("commit", "-C", path, "commit", "-m", message)
        result["committed"] = True
    else:
        say("nothing to commit")
    remotes = _git("-C", repo, "remote")[1].split()
    if "origin" in remotes:
        step("push", "-C", path, "push", "-u", "origin", branch, limit=push_timeout)
        result["pushed"] = True
        say(f"pushed {branch} to origin")
    else:
        say("no remote; branch kept local")
    step("worktree remove", "-C", repo, "worktree", "remove", path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE worktrees SET state='finished', finished_at=? WHERE path=?", (now, path))
    conn.commit()
    say(f"finished {name}: branch {branch} kept")
    return result


def gc(conn, root):
    """List stale registry rows. Reads only; deletes and writes nothing."""
    now = datetime.now(timezone.utc)
    found = []
    q = ("SELECT w.path, w.state, w.branch, w.created_at, s.name, s.state, i.name FROM worktrees w "
         "LEFT JOIN rookery_sessions s ON s.session_id=w.session_id "
         "LEFT JOIN identities i ON i.agent_id=w.agent_id ORDER BY w.created_at, w.path")
    for path, state, branch, created, sname, sstate, aname in conn.execute(q).fetchall():
        exists = os.path.lexists(path)
        reason = None
        if sstate == "ended":
            reason = "session-ended"
        elif state == "failed":
            reason = "failed"
        elif state == "finished" and exists:
            reason = "finished-path-exists"
        elif state == "active" and not exists:
            reason = "path-missing"
        elif state == "pending":
            try:
                age = (now - datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
                if age.total_seconds() > 3600:
                    reason = "pending-stale"
            except ValueError:
                reason = "pending-stale"
        if reason and (not root or _under_root(path, root)):
            found.append({"path": path, "session": sname, "agent": aname, "branch": branch, "reason": reason})
    return found
