"""Org syntax: the heading regexes, the task model, and the text parsers.

Moved here from ``ortasklib.core`` so that ``orglib`` depends on nothing beyond
the standard library. ``ortasklib.core`` re-exports every name defined here, so
callers that still say ``core.parse_org`` keep working unchanged.

Everything in this module operates on strings and lists of strings. File
discovery, atomic writes, and editor invocation stayed behind in
``ortasklib.core``, which is the division that lets this package stand alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMERIC_ID_RE = re.compile(r"^t\d{4}(?:\.\d+)*$")
WEEK_ID_RE = re.compile(r"^tw(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
BARE_WEEK_ID_RE = re.compile(r"^(?:\d{2}|\d{4})[Ww]\d{2}(?:\.\d+)*$")
WEEK_ID_PARTS_RE = re.compile(
    r"^tw(?P<year>\d{2}|\d{4})[Ww](?P<week>\d{2})(?P<suffix>(?:\.\d+)*)$"
)
TASK_ID_PATTERN = r"t(?:\d{4}|w(?:\d{2}|\d{4})[Ww]\d{2})(?:\.\d+)*"
TASK_STATES = ("TODO", "DONE", "MOOT", "SUPERSEDED")
TERMINAL_STATES = frozenset({"DONE", "MOOT", "SUPERSEDED"})
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
DIRECTORIES_HEADING_RE = re.compile(r"^\*\s+Directories\s*$")
TOPLEVEL_HEADING_RE = re.compile(r"^\*\s")
LIST_BULLET_RE = re.compile(r"^[-+]\s+")


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


def _strip_directory_entry(line: str) -> str:
    """Strip the optional Org decoration around one ``* Directories`` entry.

    Entries are usually written as subheadings with ``file:`` links
    (``** file:~/src/ortask``), but a bare path, a list bullet, and Org link
    brackets are all accepted so the section stays comfortable to hand-edit.
    """
    entry = line.strip().lstrip("*").strip()
    entry = LIST_BULLET_RE.sub("", entry)
    if entry.startswith("[[") and "]]" in entry:
        entry = entry[2:entry.index("]]")].split("][")[0].strip()
    if entry.startswith("file:"):
        entry = entry[len("file:"):].strip()
    return entry


def parse_directories(text: str) -> list[str] | None:
    """Return the raw entries under a top-level ``* Directories`` heading.

    Returns ``None`` when the text has no such section, which is different from
    an empty list for a section that exists but lists nothing. The section runs
    until the next top-level heading. Blank lines and ``#`` comments are
    skipped; everything else is stripped of Org decoration but left otherwise
    unexpanded, since resolving a path needs a project root this module has no
    opinion about.
    """
    entries: list[str] | None = None
    for line in text.splitlines():
        if DIRECTORIES_HEADING_RE.match(line):
            entries = []
            continue
        if entries is None:
            continue
        if TOPLEVEL_HEADING_RE.match(line):
            break
        entry = _strip_directory_entry(line)
        if not entry or entry.startswith("#"):
            continue
        entries.append(entry)
    return entries


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
