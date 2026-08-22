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
ANY_HEADING_RE = re.compile(r"^(?P<stars>\*+)[ \t]+(?P<title>.*?)[ \t]*$")
TRAILING_TAGS_RE = re.compile(r"\s+:(?:[^\s:]+:)+\s*$")


class OrgStructureError(ValueError):
    """Raised when an addressed Org structure is ambiguous."""


@dataclass(frozen=True)
class SourceSpan:
    """A half-open source range with zero-based, end-exclusive line indices."""

    start: int
    end: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DirectoriesSection:
    """One source-backed direct-child ``Directories`` subtree."""

    span: SourceSpan
    entries: tuple[str, ...]


@dataclass(frozen=True)
class ProjectDirectories:
    """The result of looking up one project in a registry index.

    ``project_span`` is ``None`` when the project heading is absent. A present
    project with ``section=None`` has no direct-child ``Directories`` section.
    A present section with no entries is represented by an empty tuple.
    """

    project: str
    project_span: SourceSpan | None
    section: DirectoriesSection | None

    @property
    def project_found(self) -> bool:
        return self.project_span is not None

    @property
    def section_found(self) -> bool:
        return self.section is not None


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


@dataclass(frozen=True)
class _SourceLine:
    text: str
    start: int


@dataclass(frozen=True)
class _Heading:
    line: int
    level: int
    title: str


def _source_lines(text: str) -> list[_SourceLine]:
    lines: list[_SourceLine] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        end = offset + len(raw_line)
        lines.append(_SourceLine(raw_line.rstrip("\r\n"), offset))
        offset = end
    return lines


def _headings(lines: list[_SourceLine]) -> list[_Heading]:
    headings: list[_Heading] = []
    for line_num, line in enumerate(lines):
        match = ANY_HEADING_RE.match(line.text)
        if match:
            headings.append(
                _Heading(line_num, len(match.group("stars")), match.group("title"))
            )
    return headings


def _project_title(title: str) -> str:
    return TRAILING_TAGS_RE.sub("", title).strip()


def _span(
    lines: list[_SourceLine], start_line: int, end_line: int, text_length: int
) -> SourceSpan:
    start = lines[start_line].start
    end = lines[end_line].start if end_line < len(lines) else text_length
    return SourceSpan(start, end, start_line, end_line)


def _heading_lines(headings: list[_Heading]) -> str:
    return ", ".join(str(heading.line + 1) for heading in headings)


def parse_project_directories(text: str, project: str) -> ProjectDirectories:
    """Locate one project's direct-child ``Directories`` section.

    Project headings are top-level, matched case-insensitively after stripping
    trailing Org tags. Only an exact ``** Directories`` child is configuration;
    deeper headings with that title are ignored. Duplicate matching project or
    direct-child section headings raise :class:`OrgStructureError` instead of
    silently choosing one.

    Returned spans are half-open character offsets into ``text``. Project and
    section spans contain their complete subtrees, ending immediately before
    the next same-or-higher-level heading.
    """
    project_name = project.strip()
    if not project_name:
        raise ValueError("project name must not be empty")

    lines = _source_lines(text)
    headings = _headings(lines)
    wanted = project_name.casefold()
    matches = [
        heading
        for heading in headings
        if heading.level == 1 and _project_title(heading.title).casefold() == wanted
    ]
    if len(matches) > 1:
        raise OrgStructureError(
            f"duplicate project heading for {project_name!r} at lines "
            f"{_heading_lines(matches)}"
        )
    if not matches:
        return ProjectDirectories(project_name, None, None)

    project_heading = matches[0]
    project_end = next(
        (
            heading.line
            for heading in headings
            if heading.line > project_heading.line and heading.level == 1
        ),
        len(lines),
    )
    project_span = _span(lines, project_heading.line, project_end, len(text))

    sections = [
        heading
        for heading in headings
        if project_heading.line < heading.line < project_end
        and heading.level == 2
        and heading.title == "Directories"
    ]
    if len(sections) > 1:
        raise OrgStructureError(
            f"duplicate Directories heading for project {project_name!r} at lines "
            f"{_heading_lines(sections)}"
        )
    if not sections:
        return ProjectDirectories(project_name, project_span, None)

    section_heading = sections[0]
    section_end = next(
        (
            heading.line
            for heading in headings
            if section_heading.line < heading.line < project_end
            and heading.level <= 2
        ),
        project_end,
    )
    section_span = _span(lines, section_heading.line, section_end, len(text))
    entries: list[str] = []
    for line in lines[section_heading.line + 1 : section_end]:
        entry = _strip_directory_entry(line.text)
        if entry and not entry.startswith("#"):
            entries.append(entry)

    section = DirectoriesSection(section_span, tuple(entries))
    return ProjectDirectories(project_name, project_span, section)


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
