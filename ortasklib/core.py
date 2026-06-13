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
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PROBE_NAMES = ["todo.org", "tasks.org"]
NUMERIC_ID_RE = re.compile(r"^t\d{4}(?:\.\d+)*$")
WEEK_ID_RE = re.compile(r"^tw(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
BARE_WEEK_ID_RE = re.compile(r"^(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
WEEK_ID_PARTS_RE = re.compile(
    r"^tw(?P<year>\d{2}|\d{4})[Ww](?P<week>\d{2})(?P<suffix>(?:\.\d+)*)$"
)
TASK_ID_PATTERN = r"t(?:\d{4}|w(?:\d{2}|\d{4})[Ww]\d{2})(?:\.\d+)*"

HEADING_RE = re.compile(
    r"^(?P<stars>\*+)\s+"
    r"(?P<state>TODO|DONE)\s+"
    r"(?:\[#(?P<priority>[A-C])\]\s+)?"
    rf"(?P<id>{TASK_ID_PATTERN})\s+"
    r"(?P<text>.*?)(?:\s+:(?P<tags>[\w:]+):)?\s*$"
)

# Matches any org heading under * Tasks (with or without a TODO keyword / ID)
BARE_HEADING_RE = re.compile(r"^(?P<stars>\*{2,})\s+(?P<rest>.+)$")

TASKS_HEADING_RE = re.compile(r"^\*\s+Tasks\s*$")


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


# ---------------------------------------------------------------------------
# File discovery (single directory)
# ---------------------------------------------------------------------------

def resolve_org_file() -> Path | None:
    """Find the default org file in the current directory using the probe order.

    1. ORTASK_FILE env var
    2. Probe for TODO.org and TODO*.org files
    3. Probe for compatibility names: todo.org, tasks.org
    4. Pick first .org file alphabetically (warn if multiple)
    """
    env = os.environ.get("ORTASK_FILE")
    if env:
        return Path(env)

    todo_files = sorted(
        Path(".").glob("TODO*.org"),
        key=lambda p: (p.name != "TODO.org", p.name.lower()),
    )
    if todo_files:
        if len(todo_files) > 1:
            print(f"warning: multiple TODO*.org files found, using {todo_files[0].name}"
                  f" (override with --file or ORTASK_FILE)", file=sys.stderr)
        return todo_files[0]

    for name in PROBE_NAMES:
        p = Path(name)
        if p.exists():
            return p

    org_files = sorted(Path(".").glob("*.org"))
    if len(org_files) == 1:
        return org_files[0]
    if len(org_files) > 1:
        print(f"warning: multiple .org files found, using {org_files[0].name}"
              f" (override with --file or ORTASK_FILE)", file=sys.stderr)
        return org_files[0]

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


def parse_org(text: str) -> list[TodoItem]:
    """Parse the * Tasks subtree and return a list of TodoItems."""
    lines = text.splitlines()
    start, end = find_tasks_range(lines)
    if start < 0:
        return []

    items: list[TodoItem] = []
    for i in range(start + 1, end):
        m = HEADING_RE.match(lines[i])
        if m:
            items.append(TodoItem(
                level=len(m.group("stars")),
                state=m.group("state"),
                id=m.group("id"),
                text=m.group("text"),
                priority=m.group("priority"),
                tags=m.group("tags"),
                line_num=i,
            ))
        elif items:
            # Non-heading lines belong to the most recent task's body
            if not BARE_HEADING_RE.match(lines[i]):
                items[-1].body_lines.append(lines[i])

    return items


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
    result = items
    if state == "todo":
        result = [t for t in result if t.state == "TODO"]
    elif state == "done":
        result = [t for t in result if t.state == "DONE"]
    if root_only:
        result = [t for t in result if t.level == 2]
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
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_lines(path: Path, lines: list[str]) -> None:
    atomic_write(path, "\n".join(lines) + "\n" if lines else "")
