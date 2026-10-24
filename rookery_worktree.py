"""rookery_worktree — one git worktree per writer (specs/003-rookery-up, research D12, FR-020..FR-021a).

Path <root>/wt/<session>/<name>/<repo basename>, branch agent/<session>/<name>/<task_id>.
The `worktrees` registry row is the only proof Rookery owns a path. No function here deletes a row or a branch.
"""

import os
import subprocess


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


def _git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, env=git_env())
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
