"""Task-file discovery, ID helpers, and atomic writes.

This module is shared by both the local task tool (``ortask.py`` via
``ortasklib.tasks``) and the project-layer tools (``projmgr.py``/``taskui``
via ``ortasklib.manager``). It contains no CLI parsing, no ``sys.exit`` calls,
and no command names — just reusable building blocks that operate on in-memory
strings and individual files.

Org syntax itself lives in the standalone ``orglib`` package; the names below
that come from :mod:`orglib.syntax` are re-exported here for callers that
predate the split.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

# Org syntax lives in the standalone ``orglib`` package. These names are
# re-exported so that ``core.parse_org``, ``core.TodoItem``, and the heading
# regexes keep resolving for the callers that already use them.
from orglib.syntax import (  # noqa: F401 — re-exported for existing callers
    BARE_HEADING_RE,
    BARE_WEEK_ID_RE,
    DIRECTORIES_HEADING_RE,
    HEADING_RE,
    LIST_BULLET_RE,
    NUMERIC_ID_RE,
    ORG_HEADING_RE,
    TASK_ID_PATTERN,
    TASK_STATE_PATTERN,
    TASK_STATES,
    TASKS_HEADING_RE,
    TEMPLATE_HEADING_RE,
    TERMINAL_STATES,
    TodoItem,
    TOPLEVEL_HEADING_RE,
    WEEK_ID_PARTS_RE,
    WEEK_ID_RE,
    count_template_sections,
    find_tasks_range,
    find_template_range,
    parse_directories,
    parse_org,
)

PRIMARY_PROBE_NAMES = ["tasks.org", "task.org"]
COMPAT_PROBE_NAMES = ["todo.org"]


class OrgFileDiscoveryError(Exception):
    """Raised when task-file discovery finds ambiguous candidates."""


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _relative_to_cwd(path: Path) -> Path:
    return Path(os.path.relpath(path, Path.cwd()))


def _format_candidates(paths: list[Path]) -> str:
    return ", ".join(p.name for p in paths)


def _ambiguous(directory: Path, pattern: str, matches: list[Path]) -> None:
    raise OrgFileDiscoveryError(
        f"ambiguous task files in {directory}: {_format_candidates(matches)} "
        f"match {pattern}; use --file or rename the intended file to tasks.org"
    )


def _preferred_task_file_in(directory: Path) -> Path | None:
    for name in PRIMARY_PROBE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate

    task_files = sorted(p for p in directory.glob("*.task.org") if p.is_file())
    if len(task_files) == 1:
        return task_files[0]
    if len(task_files) > 1:
        _ambiguous(directory, "*.task.org", task_files)

    compat_canonical = directory / "TODO.org"
    if compat_canonical.is_file():
        return compat_canonical

    compat_todo_files = sorted(
        p for p in directory.glob("TODO*.org")
        if p.is_file() and p.name != "TODO.org"
    )
    if len(compat_todo_files) == 1:
        return compat_todo_files[0]
    if len(compat_todo_files) > 1:
        _ambiguous(directory, "TODO*.org", compat_todo_files)

    for name in COMPAT_PROBE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate

    return None


def preferred_task_file_in(directory: Path) -> Path | None:
    """The canonical task file in one directory, if any.

    Stricter than :func:`discover_org_file`: it never falls back to "exactly one
    generic ``*.org``", so callers can use it to ask whether a directory looks
    like a project root.
    """
    return _preferred_task_file_in(directory)


def _walk_up(start: Path) -> list[Path]:
    directory = start.resolve()
    return [directory, *directory.parents]


def resolve_org_file_with_provenance() -> tuple[Path | None, str | None]:
    """Find the default org file from the current directory and its provenance.

    Returns ``(path, provenance_description)``.
    """
    env = os.environ.get("ORTASK_FILE")
    if env:
        return Path(env), f"environment variable ORTASK_FILE={env}"

    cwd = Path.cwd()
    for directory in _walk_up(cwd):
        found = _preferred_task_file_in(directory)
        if found is not None:
            prov = f"probed in {directory}" if directory != cwd else "probed in ."
            return _relative_to_cwd(found), prov

    org_files = sorted(p for p in cwd.glob("*.org") if p.is_file())
    if len(org_files) == 1:
        return Path(org_files[0].name), "generic *.org in ."
    if len(org_files) > 1:
        _ambiguous(cwd, "*.org", org_files)

    return None, None


def resolve_org_file() -> Path | None:
    """Find the default org file from the current directory.

    1. ORTASK_FILE env var
    2. Walk upward for tasks.org, task.org, exactly one *.task.org, and
       compatibility names
    3. Use exactly one generic *.org file in the original cwd
    """
    path, _ = resolve_org_file_with_provenance()
    return path


def discover_org_file(directory: Path) -> Path | None:
    """Probe one directory for its task file (no env var, no parent walk).

    Used by ``projmgr.py add`` to register an arbitrary project directory
    without accidentally selecting a parent project's task file.
    """
    found = _preferred_task_file_in(directory)
    if found is not None:
        return found

    org_files = sorted(p for p in directory.glob("*.org") if p.is_file())
    if len(org_files) == 1:
        return org_files[0]
    if len(org_files) > 1:
        _ambiguous(directory, "*.org", org_files)
    return None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def normalize_id(raw: str) -> str:
    """Expand shorthand numeric IDs while preserving explicit weekly IDs."""
    if BARE_WEEK_ID_RE.match(raw):
        return "tw" + raw
    if WEEK_ID_RE.match(raw):
        return raw
    if raw.startswith("w") and WEEK_ID_RE.match("t" + raw):
        return "t" + raw
    if not raw.startswith("t"):
        raw = "t" + raw
    if WEEK_ID_RE.match(raw):
        return raw
    if NUMERIC_ID_RE.match(raw):
        return raw
    parts = raw[1:].split(".")
    # Zero-pad the top-level number to 4 digits
    parts[0] = parts[0].zfill(4)
    return "t" + ".".join(parts)


def canonical_id(task_id: str) -> str:
    """Return a comparison key for IDs; week IDs normalize year and W/w."""
    match = WEEK_ID_PARTS_RE.match(task_id)
    if match:
        year = match.group("year")[-2:]
        return f"tw{year}w{match.group('week')}{match.group('suffix')}"
    return task_id


def filter_items(
    items: list[TodoItem],
    *,
    state: str = "all",
    root_only: bool = False,
    max_items: int | None = None,
) -> list[TodoItem]:
    root_level = min((t.level for t in items), default=2)
    result = items
    if state == "todo":
        result = [t for t in result if t.state == "TODO"]
    elif state == "done":
        result = [t for t in result if t.state in TERMINAL_STATES]
    if root_only:
        result = [t for t in result if t.level == root_level]
    if max_items is not None:
        result = result[:max_items]
    return result


def find_by_id(items: list[TodoItem], task_id: str) -> TodoItem | None:
    target = canonical_id(task_id)
    for item in items:
        if canonical_id(item.id) == target:
            return item
    return None


def build_org_heading(item: TodoItem) -> str:
    """Render a TodoItem back into a single org heading line."""
    stars = "*" * item.level
    parts = [stars, item.state]
    if item.priority:
        parts.append(f"[#{item.priority}]")
    parts.append(item.id)
    parts.append(item.text)
    line = " ".join(parts)
    if item.tags:
        line += f"  :{item.tags}:"
    return line


# ---------------------------------------------------------------------------
# Writer — atomic file replacement
# ---------------------------------------------------------------------------

def atomic_write(path: Path, content: str) -> None:
    target = path.resolve() if path.is_symlink() else path
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_pair(
    first_path: Path,
    first_content: str,
    second_path: Path,
    second_content: str,
) -> None:
    """Replace two files, restoring the first if the second write fails.

    Callers should put the copy/destination first and the destructive source
    edit second. A process crash can still interrupt two filesystem replaces,
    but ordinary write failures cannot lose or duplicate the moved content.
    """
    first_target = first_path.resolve() if first_path.is_symlink() else first_path
    first_existed = first_target.exists()
    previous_first = (
        first_target.read_text(encoding="utf-8") if first_existed else None
    )

    atomic_write(first_path, first_content)
    try:
        atomic_write(second_path, second_content)
    except BaseException:
        try:
            if first_existed:
                assert previous_first is not None
                atomic_write(first_path, previous_first)
            else:
                first_target.unlink(missing_ok=True)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"second write failed and rollback of {first_path} also failed"
            ) from rollback_error
        raise


def lines_to_text(lines: list[str]) -> str:
    """Join task lines into file text with a single trailing newline."""
    return "\n".join(lines) + "\n" if lines else ""


def write_lines(path: Path, lines: list[str]) -> None:
    atomic_write(path, lines_to_text(lines))


# ---------------------------------------------------------------------------
# External editor
# ---------------------------------------------------------------------------

_EDITOR_LINE_FLAG = {
    "vi": "plus",
    "vim": "plus",
    "nvim": "plus",
    "less": "plus",
    "emacs": "plus",
    "emacsclient": "plus",
    "nano": "plus",
    "pico": "plus",
    "code": "goto",
    "codium": "goto",
}


def editor_argv(path: Path, line: int | None = None) -> list[str] | None:
    """Build the argv for opening ``path`` in the user's editor.

    ``VISUAL`` wins over ``EDITOR``, and the value is split with :mod:`shlex` so
    settings that carry arguments (``EDITOR="emacs -nw"``) work. ``line`` is
    1-based and only added for editors known to accept it. Returns ``None`` when
    neither variable is set to anything useful.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        return None
    parts = shlex.split(editor)
    if not parts:
        return None
    if line is None:
        return parts + [str(path)]
    style = _EDITOR_LINE_FLAG.get(Path(parts[0]).name)
    if style == "plus":
        return parts + [f"+{line}", str(path)]
    if style == "goto":
        return parts + ["--goto", f"{path}:{line}"]
    return parts + [str(path)]
