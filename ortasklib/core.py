"""Core Org parsing, models, ID helpers, discovery, and atomic writes.

This module is shared by both the local task tool (``ortask.py`` via
``ortasklib.tasks``) and the global project tools (``orgmgr.py``/``projtui.py``
via ``ortasklib.manager``). It contains no CLI parsing, no ``sys.exit`` calls,
and no command names — just reusable building blocks that operate on in-memory
strings and individual files.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PRIMARY_PROBE_NAMES = ["tasks.org", "task.org"]
COMPAT_PROBE_NAMES = ["todo.org"]
NUMERIC_ID_RE = re.compile(r"^t\d{4}(?:\.\d+)*$")
WEEK_ID_RE = re.compile(r"^tw(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
BARE_WEEK_ID_RE = re.compile(r"^(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
WEEK_ID_PARTS_RE = re.compile(
    r"^tw(?P<year>\d{2}|\d{4})[Ww](?P<week>\d{2})(?P<suffix>(?:\.\d+)*)$"
)
TASK_ID_PATTERN = r"t(?:\d{4}|w(?:\d{2}|\d{4})[Ww]\d{2})(?:\.\d+)*"
TASK_STATES = ("TODO", "DONE", "SUPERSEDED")
TERMINAL_STATES = frozenset({"DONE", "SUPERSEDED"})
TASK_STATE_PATTERN = "|".join(TASK_STATES)

HEADING_RE = re.compile(
    r"^(?P<stars>\*+)\s+"
    rf"(?P<state>{TASK_STATE_PATTERN})\s+"
    r"(?:\[#(?P<priority>[A-C])\]\s+)?"
    rf"(?P<id>{TASK_ID_PATTERN})\s+"
    r"(?P<text>.*?)(?:\s+:(?P<tags>[\w:]+):)?\s*$"
)

# Matches any org heading under * Tasks (with or without a TODO keyword / ID)
BARE_HEADING_RE = re.compile(r"^(?P<stars>\*{2,})\s+(?P<rest>.+)$")
ORG_HEADING_RE = re.compile(r"^\*+\s+")

TASKS_HEADING_RE = re.compile(r"^\*\s+Tasks\s*$")
TEMPLATE_HEADING_RE = re.compile(r"^\*\s+Template\s*$")


@dataclass
class TodoItem:
    level: int
    state: str
    id: str
    text: str
    priority: str | None = None
    tags: str | None = None
    line_num: int = 0
    body_lines: list[str] = field(default_factory=list)


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


def _walk_up(start: Path) -> list[Path]:
    directory = start.resolve()
    return [directory, *directory.parents]


def resolve_org_file() -> Path | None:
    """Find the default org file from the current directory.

    1. ORTASK_FILE env var
    2. Walk upward for tasks.org, task.org, exactly one *.task.org, and
       compatibility names
    3. Use exactly one generic *.org file in the original cwd
    """
    env = os.environ.get("ORTASK_FILE")
    if env:
        return Path(env)

    cwd = Path.cwd()
    for directory in _walk_up(cwd):
        found = _preferred_task_file_in(directory)
        if found is not None:
            return _relative_to_cwd(found)

    org_files = sorted(p for p in cwd.glob("*.org") if p.is_file())
    if len(org_files) == 1:
        return Path(org_files[0].name)
    if len(org_files) > 1:
        _ambiguous(cwd, "*.org", org_files)

    return None


def discover_org_file(directory: Path) -> Path | None:
    """Probe one directory for its task file (no env var, no parent walk).

    Used by ``orgmgr.py projadd`` to register an arbitrary project directory
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
# Parser
# ---------------------------------------------------------------------------

def find_tasks_range(lines: list[str]) -> tuple[int, int]:
    """Return (start, end) line indices for the * Tasks subtree.

    start is the index of the ``* Tasks`` heading itself.
    end is the index of the next top-level heading, or len(lines).
    """
    start = None
    for i, line in enumerate(lines):
        if TASKS_HEADING_RE.match(line):
            start = i
            break
    if start is None:
        return -1, -1
    for i in range(start + 1, len(lines)):
        if re.match(r"^\*\s+", lines[i]) and not TASKS_HEADING_RE.match(lines[i]):
            return start, i
    return start, len(lines)


def find_template_range(lines: list[str]) -> tuple[int, int]:
    """Return (start, end) line indices for the first ``* Template`` subtree.

    ``start`` is the index of the ``* Template`` heading itself; ``end`` is the
    index of the next top-level heading, or ``len(lines)``. Returns ``(-1, -1)``
    when there is no ``* Template`` heading. Mirrors :func:`find_tasks_range`
    so template application can copy raw template lines without parsing them
    into ``TodoItem`` records.
    """
    start = None
    for i, line in enumerate(lines):
        if TEMPLATE_HEADING_RE.match(line):
            start = i
            break
    if start is None:
        return -1, -1
    for i in range(start + 1, len(lines)):
        if re.match(r"^\*\s+", lines[i]) and not TEMPLATE_HEADING_RE.match(lines[i]):
            return start, i
    return start, len(lines)


def count_template_sections(lines: list[str]) -> int:
    """Count top-level ``* Template`` headings (used to reject 0 or >1)."""
    return sum(1 for line in lines if TEMPLATE_HEADING_RE.match(line))


def _parse_task_headings(lines: list[str], start: int, end: int) -> list[TodoItem]:
    items: list[TodoItem] = []
    active_item: TodoItem | None = None
    for i in range(start, end):
        m = HEADING_RE.match(lines[i])
        if m:
            active_item = TodoItem(
                level=len(m.group("stars")),
                state=m.group("state"),
                id=m.group("id"),
                text=m.group("text"),
                priority=m.group("priority"),
                tags=m.group("tags"),
                line_num=i,
            )
            items.append(active_item)
        elif ORG_HEADING_RE.match(lines[i]):
            active_item = None
        elif active_item is not None:
            # Non-heading lines belong to the most recent task's body
            active_item.body_lines.append(lines[i])

    return items


def parse_org(text: str) -> list[TodoItem]:
    """Parse ortask-compatible task headings.

    If a top-level ``* Tasks`` section exists, parsing is scoped to that subtree.
    Otherwise, parse valid task headings from the whole file. This
    lets ordinary Org files participate without requiring a dedicated section.
    """
    lines = text.splitlines()
    start, end = find_tasks_range(lines)
    if start >= 0:
        return _parse_task_headings(lines, start + 1, end)
    return _parse_task_headings(lines, 0, len(lines))


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


def lines_to_text(lines: list[str]) -> str:
    """Join task lines into file text with a single trailing newline."""
    return "\n".join(lines) + "\n" if lines else ""


def write_lines(path: Path, lines: list[str]) -> None:
    atomic_write(path, lines_to_text(lines))
