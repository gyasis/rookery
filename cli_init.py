"""cli_init.py -- helpers for `rookery init` (D13): managed context section, init lock.

T030 upsert_section. T031 (merge_hooks) and T032 (main) extend this module.
Standard library only.
"""
import contextlib
import fcntl
import os
import re
import stat
import tempfile

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
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".md")
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
            f"fix the file by hand, nothing was written")
    a, e = starts[0][1], ends[0][0]
    new_body = nl.join(text.rstrip("\n").split("\n")) + nl
    if old[a:e] == new_body:
        return "unchanged"
    write_text_atomic(path, old[:a] + new_body + old[e:])
    return "replaced"


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
