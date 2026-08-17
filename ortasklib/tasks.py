"""Local task helpers used by ``ortask.py``.

These functions take Org text (or parsed items) and return data or new line
lists; they never read/write files, print, or call ``sys.exit``. ``ortask.py``
owns the CLI translation: file I/O, human-readable output, and exit codes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .core import (
    BARE_HEADING_RE,
    HEADING_RE,
    NUMERIC_ID_RE,
    ORG_HEADING_RE,
    TASK_ID_PATTERN,
    TASK_STATES,
    WEEK_ID_PARTS_RE,
    TodoItem,
    build_org_heading,
    canonical_id,
    count_template_sections,
    find_by_id,
    find_tasks_range,
    find_template_range,
    normalize_id,
    parse_org,
)


class TaskNotFound(Exception):
    """Raised by edit/show helpers when a task ID is absent.

    ``str(exc)`` is the (normalized) ID that could not be found.
    """


class MissingTasksSection(Exception):
    """Raised when an edit needs ``* Tasks`` but section creation is disabled."""


class TemplateError(Exception):
    """Raised by template-application helpers; ``str(exc)`` is user-facing.

    Covers an unknown profile, a missing/duplicate/empty ``* Template`` section,
    unparseable ``--week``/``--date`` input, mismatched week/date, and generated
    IDs that already exist under ``* Tasks``.
    """


class TodoStateError(Exception):
    """Raised when an Org TODO declaration cannot be updated safely."""


class ArchiveError(Exception):
    """Raised when task subtrees cannot be archived safely."""


@dataclass(frozen=True)
class ArchiveResult:
    """The in-memory result of moving task subtrees into an archive."""

    source_text: str
    archive_text: str
    task_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_plain(items: list[TodoItem]) -> str:
    lines = []
    root_level = min((item.level for item in items), default=2)
    for item in items:
        indent = "  " * max(item.level - root_level, 0)
        lines.append(f"{indent}[{item.state}] {item.id} {item.text}")
    return "\n".join(lines)


def format_org(items: list[TodoItem]) -> str:
    return "\n".join(build_org_heading(item) for item in items)


def _item_to_dict(item: TodoItem, children: list[dict]) -> dict:
    d: dict = {"id": item.id, "state": item.state, "title": item.text, "level": item.level}
    if children:
        d["subtasks"] = children
    return d


def format_json(items: list[TodoItem]) -> str:
    """Build nested JSON: top-level tasks contain their subtasks."""
    roots: list[dict] = []
    # Group items: level-2 items are roots, deeper items are children of
    # the most recent ancestor.
    stack: list[tuple[TodoItem, list[dict]]] = []

    for item in items:
        children: list[dict] = []
        node = (item, children)

        # Pop items from the stack that are not ancestors of this item
        while stack and stack[-1][0].level >= item.level:
            finished_item, finished_children = stack.pop()
            d = _item_to_dict(finished_item, finished_children)
            if stack:
                stack[-1][1].append(d)
            else:
                roots.append(d)

        stack.append(node)

    # Flush remaining stack
    while stack:
        finished_item, finished_children = stack.pop()
        d = _item_to_dict(finished_item, finished_children)
        if stack:
            stack[-1][1].append(d)
        else:
            roots.append(d)

    return json.dumps(roots, indent=2)


# ---------------------------------------------------------------------------
# Show expansion
# ---------------------------------------------------------------------------

def show_lines(text: str, task_id: str) -> list[str]:
    """Return the org lines for a task: its heading, body, and descendants.

    Raises ``TaskNotFound`` if ``task_id`` does not resolve.
    """
    items = parse_org(text)
    item = find_by_id(items, task_id)
    if item is None:
        raise TaskNotFound(task_id)

    out = [build_org_heading(item), *item.body_lines]
    prefix = item.id + "."
    for sub in items:
        if sub.id.startswith(prefix):
            out.append(build_org_heading(sub))
            out.extend(sub.body_lines)
    return out


# ---------------------------------------------------------------------------
# ID allocation
# ---------------------------------------------------------------------------

def next_toplevel_id(
    items: list[TodoItem], reserved_ids: set[str] | None = None
) -> str:
    max_num = 0
    task_ids = {item.id for item in items} | (reserved_ids or set())
    for task_id in task_ids:
        # Only consider top-level IDs (no dots)
        if "." not in task_id and NUMERIC_ID_RE.match(task_id):
            num = int(task_id[1:])
            max_num = max(max_num, num)
    return f"t{max_num + 1:04d}"


def next_subtask_id(
    items: list[TodoItem],
    parent_id: str,
    reserved_ids: set[str] | None = None,
) -> str:
    max_sub = 0
    prefix = canonical_id(parent_id) + "."
    task_ids = {item.id for item in items} | (reserved_ids or set())
    for task_id in task_ids:
        canonical_task_id = canonical_id(task_id)
        if canonical_task_id.startswith(prefix):
            suffix = canonical_task_id[len(prefix):]
            # Only direct children (no further dots)
            if "." not in suffix:
                try:
                    max_sub = max(max_sub, int(suffix))
                except ValueError:
                    pass
    return f"{parent_id}.{max_sub + 1}"


# ---------------------------------------------------------------------------
# Line-level edits
# ---------------------------------------------------------------------------

def add_task(
    text: str,
    title: str,
    parent: str | None = None,
    *,
    allow_create_section: bool = False,
    reserved_ids: set[str] | None = None,
) -> tuple[list[str], str]:
    """Insert a new task and return ``(new_lines, new_id)``.

    A top-level task gets the next ``tNNNN`` ID and is appended to the
    ``* Tasks`` subtree. A subtask is inserted after its parent's existing
    descendants with the next dotted child ID. Raises ``TaskNotFound`` if
    ``parent`` is given but does not resolve; raises ``MissingTasksSection`` if
    a top-level add would need to create ``* Tasks`` and section creation is not
    explicitly enabled. ``reserved_ids`` participate in allocation but are not
    valid insertion parents.
    """
    lines = text.splitlines()
    items = parse_org(text)
    start, end = find_tasks_range(lines)

    if parent:
        parent = normalize_id(parent)
        parent_item = find_by_id(items, parent)
        if parent_item is None:
            raise TaskNotFound(parent)
        parent_id = parent_item.id
        new_id = next_subtask_id(items, parent_id, reserved_ids)
        new_level = parent_item.level + 1
        # Insert after the last sibling/descendant of the parent
        insert_at = parent_item.line_num + 1
        prefix = parent_id + "."
        for item in items:
            if item.id.startswith(prefix) or item is parent_item:
                # Move past this item's heading and body
                insert_at = max(insert_at, item.line_num + 1 + len(item.body_lines))
    else:
        if start < 0:
            if not allow_create_section:
                raise MissingTasksSection()
            # Bootstrap a dedicated task file without reserializing prose.
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("* Tasks")
            start = len(lines) - 1
            end = len(lines)
        new_id = next_toplevel_id(items, reserved_ids)
        new_level = 2
        insert_at = end

    heading = "*" * new_level + f" TODO {new_id} {title}"
    lines.insert(insert_at, heading)
    return lines, new_id


def change_state(text: str, task_id: str, target: str) -> list[str] | None:
    """Switch a task's keyword to a supported ``target`` state.

    Returns the new line list, or ``None`` when the task is already in the
    target state (no write needed). Only the matched heading line changes.
    Raises ``TaskNotFound`` if ``task_id`` does not resolve.
    """
    if target not in TASK_STATES:
        raise ValueError(f"unsupported task state: {target}")
    lines = text.splitlines()
    items = parse_org(text)
    item = find_by_id(items, task_id)
    if item is None:
        raise TaskNotFound(task_id)
    if item.state == target:
        return None
    lines[item.line_num] = re.sub(
        rf"^(\*+\s+){re.escape(item.state)}\b",
        rf"\1{target}",
        lines[item.line_num],
        count=1,
    )
    return lines


def change_text(text: str, task_id: str, target: str) -> list[str] | None:
    """Replace only one task heading's text while preserving its structure."""
    if "\n" in target or "\r" in target:
        raise ValueError("task text must be a single line")
    target = target.strip()
    if not target:
        raise ValueError("task text must not be empty")

    lines = text.splitlines()
    item = find_by_id(parse_org(text), task_id)
    if item is None:
        raise TaskNotFound(task_id)
    if item.text == target:
        return None

    line = lines[item.line_num]
    match = HEADING_RE.fullmatch(line)
    if match is None:  # Defensive: parse_org() found this same heading.
        raise ValueError("task heading cannot be rewritten safely")
    start, end = match.span("text")
    updated = line[:start] + target + line[end:]
    updated_match = HEADING_RE.fullmatch(updated)
    preserved_groups = ("stars", "state", "priority", "id", "tags")
    if (
        updated_match is None
        or updated_match.group("text") != target
        or any(
            updated_match.group(name) != match.group(name)
            for name in preserved_groups
        )
    ):
        raise ValueError("task text conflicts with Org heading syntax")

    lines[item.line_num] = updated
    return lines


def change_body(text: str, task_id: str, target: str) -> list[str] | None:
    """Replace a task's own body without touching any following Org heading."""
    if "\r" in target:
        raise ValueError("task body must not contain carriage returns")
    if any(ORG_HEADING_RE.match(line) for line in target.split("\n")):
        raise ValueError(
            "task body cannot create Org headings; use the external editor"
        )

    lines = text.splitlines()
    item = find_by_id(parse_org(text), task_id)
    if item is None:
        raise TaskNotFound(task_id)

    start = item.line_num + 1
    end = next(
        (
            line_num
            for line_num in range(start, len(lines))
            if ORG_HEADING_RE.match(lines[line_num])
        ),
        len(lines),
    )
    if "\n".join(lines[start:end]) == target:
        return None

    replacement = [] if target == "" else target.split("\n")
    return [*lines[:start], *replacement, *lines[end:]]


PRIORITIES = ("A", "B", "C")
PRIORITY_SCALE = (None, "C", "B", "A")


def change_priority(
    text: str, task_id: str, target: str | None
) -> list[str] | None:
    """Set or clear one task's Org priority cookie without reformatting it."""
    if target is not None:
        target = target.upper()
        if target not in PRIORITIES:
            raise ValueError(f"unsupported task priority: {target}")

    lines = text.splitlines()
    item = find_by_id(parse_org(text), task_id)
    if item is None:
        raise TaskNotFound(task_id)
    if item.priority == target:
        return None

    line = lines[item.line_num]
    if item.priority is not None:
        cookie = f"[#{item.priority}]"
        start = line.find(cookie)
        if target is not None:
            lines[item.line_num] = (
                line[:start] + f"[#{target}]" + line[start + len(cookie):]
            )
        else:
            end = start + len(cookie)
            while end < len(line) and line[end] in " \t":
                end += 1
            lines[item.line_num] = line[:start] + line[end:]
        return lines

    match = re.match(
        rf"^(?P<prefix>\*+\s+{re.escape(item.state)})(?P<spacing>\s+)",
        line,
    )
    if match is None:  # pragma: no cover - parse_org already validated the line
        raise ValueError(f"cannot locate task heading for priority edit: {task_id}")
    insert_at = match.end()
    lines[item.line_num] = line[:insert_at] + f"[#{target}] " + line[insert_at:]
    return lines


def shift_priority(priority: str | None, direction: int) -> str | None:
    """Raise (``1``) or lower (``-1``) priority, clamping at A/none."""
    if priority is not None:
        priority = priority.upper()
    if priority not in PRIORITY_SCALE:
        raise ValueError(f"unsupported task priority: {priority}")
    if direction not in {-1, 1}:
        raise ValueError(f"priority direction must be -1 or 1: {direction}")
    index = PRIORITY_SCALE.index(priority)
    shifted = min(max(index + direction, 0), len(PRIORITY_SCALE) - 1)
    return PRIORITY_SCALE[shifted]


TODO_DIRECTIVE_RE = re.compile(r"^#\+TODO:\s*(.*?)\s*$", re.IGNORECASE)


def _restore_final_newline(lines: list[str], original: str) -> str:
    text = "\n".join(lines)
    return text + "\n" if original.endswith("\n") else text


def ensure_terminal_keyword(text: str, state: str = "MOOT") -> str:
    """Return Org text whose TODO declaration includes terminal ``state``."""
    if state not in TASK_STATES or state == "TODO":
        raise ValueError(f"unsupported terminal state: {state}")

    lines = text.splitlines()
    matches = [(i, TODO_DIRECTIVE_RE.match(line)) for i, line in enumerate(lines)]
    matches = [(i, match) for i, match in matches if match]
    if len(matches) > 1:
        raise TodoStateError("multiple #+TODO declarations; update them manually")
    if matches:
        index, match = matches[0]
        assert match is not None
        body = match.group(1)
        if body.count("|") != 1:
            raise TodoStateError("ambiguous #+TODO declaration; expected one '|' separator")
        active, terminal = (part.strip() for part in body.split("|", 1))
        active_words = active.split()
        terminal_words = terminal.split()
        if "TODO" not in active_words or "DONE" not in terminal_words:
            raise TodoStateError("incompatible #+TODO declaration; expected TODO | DONE")
        if state in active_words:
            raise TodoStateError(f"{state} is configured as an active TODO state")
        if state not in terminal_words:
            terminal_words.append(state)
            lines[index] = f"#+TODO: {' '.join(active_words)} | {' '.join(terminal_words)}"
        return _restore_final_newline(lines, text)

    tasks_start, _ = find_tasks_range(lines)
    if tasks_start < 0:
        raise TodoStateError("no '* Tasks' section found for #+TODO declaration")
    lines.insert(tasks_start, f"#+TODO: TODO | DONE {state}")
    return _restore_final_newline(lines, text)


def change_subtree_state(
    text: str,
    task_id: str,
    *,
    source: str = "TODO",
    target: str = "MOOT",
    note: str = "",
) -> tuple[str, int]:
    """Change ``source`` headings in one task subtree and add one parent note."""
    if target not in TASK_STATES:
        raise ValueError(f"unsupported task state: {target}")
    items = parse_org(text)
    root = find_by_id(items, normalize_id(task_id))
    if root is None:
        raise TaskNotFound(task_id)

    lines = text.splitlines()
    subtree = [
        item for item in items
        if item is root or (item.line_num > root.line_num and item.id.startswith(root.id + "."))
    ]
    changed = 0
    for item in subtree:
        if item.state != source:
            continue
        lines[item.line_num] = re.sub(
            rf"^(\*+\s+){re.escape(source)}\b",
            rf"\1{target}",
            lines[item.line_num],
            count=1,
        )
        changed += 1

    if note:
        insert_at = root.line_num + 1 + len(root.body_lines)
        if note not in root.body_lines:
            lines.insert(insert_at, note)
    return _restore_final_newline(lines, text), changed


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

_ANY_HEADING_RE = re.compile(r"^(?P<stars>\*+)\s+(?P<title>.*)$")
_HEADING_TAGS_RE = re.compile(r"(?:\s+|^):(?P<tags>[\w@#%:]+):\s*$")
_PROPERTY_RE = re.compile(r"^\s*:(?P<name>[A-Za-z0-9_]+):\s*(?P<value>.*?)\s*$")
_PLANNING_RE = re.compile(
    r"^\s*(?:(?:CLOSED|DEADLINE|SCHEDULED):\s*[\[<][^]>]+[]>]\s*)+$"
)
_CATEGORY_RE = re.compile(r"^#\+CATEGORY:\s*(.*?)\s*$", re.IGNORECASE)
_FILETAGS_RE = re.compile(r"^#\+FILETAGS:\s*(.*?)\s*$", re.IGNORECASE)
_KEYWORD_RE = re.compile(r"^(?P<keyword>[A-Za-z][A-Za-z0-9_-]*)")
_ARCHIVED_ID_RE = re.compile(
    rf"^\*+\s+(?:(?P<state>[A-Z][A-Z0-9_-]*)\s+)?"
    rf"(?:\[#[A-C]\]\s+)?(?P<id>{TASK_ID_PATTERN})(?:\s+|$)"
)


@dataclass(frozen=True)
class _ArchiveSelection:
    item: TodoItem
    start: int
    end: int
    outline_path: str
    inherited_tags: tuple[str, ...]


def task_ids_in_headings(text: str) -> set[str]:
    """Return stable IDs from headings without assuming a workflow keyword."""
    return {
        match.group("id")
        for line in text.splitlines()
        if (match := _ARCHIVED_ID_RE.match(line))
    }


def _workflow_states_in_headings(text: str) -> set[str]:
    return {
        match.group("state")
        for line in text.splitlines()
        if (match := _ARCHIVED_ID_RE.match(line)) and match.group("state")
    }


def _subtree_end(lines: list[str], start: int, level: int) -> int:
    for index in range(start + 1, len(lines)):
        match = _ANY_HEADING_RE.match(lines[index])
        if match and len(match.group("stars")) <= level:
            return index
    return len(lines)


def _split_heading_title(
    raw: str, workflow_keywords: set[str]
) -> tuple[str, tuple[str, ...]]:
    """Return an Org outline title and local tags without TODO decoration."""
    tags: tuple[str, ...] = ()
    tag_match = _HEADING_TAGS_RE.search(raw)
    if tag_match:
        tags = tuple(tag for tag in tag_match.group("tags").split(":") if tag)
        raw = raw[:tag_match.start()].rstrip()
    words = raw.split()
    if words and words[0] in workflow_keywords:
        words.pop(0)
    if words and re.fullmatch(r"\[#[A-Z]\]", words[0]):
        words.pop(0)
    return " ".join(words), tags


def _file_tags(lines: list[str]) -> tuple[str, ...]:
    tags: list[str] = []
    for line in lines:
        match = _FILETAGS_RE.match(line)
        if not match:
            continue
        for tag in match.group(1).strip().strip(":").split(":"):
            if tag and tag not in tags:
                tags.append(tag)
    return tuple(tags)


def _context_before(
    lines: list[str],
    line_num: int,
    level: int,
    base_tags: tuple[str, ...],
    workflow_keywords: set[str],
) -> tuple[str, tuple[str, ...]]:
    stack: list[tuple[int, str, tuple[str, ...]]] = []
    for line in lines[:line_num]:
        match = _ANY_HEADING_RE.match(line)
        if not match:
            continue
        heading_level = len(match.group("stars"))
        while stack and stack[-1][0] >= heading_level:
            stack.pop()
        title, tags = _split_heading_title(
            match.group("title"), workflow_keywords
        )
        stack.append((heading_level, title, tags))

    while stack and stack[-1][0] >= level:
        stack.pop()

    inherited = list(base_tags)
    for _, _, tags in stack:
        for tag in tags:
            if tag not in inherited:
                inherited.append(tag)
    return "/".join(title for _, title, _ in stack), tuple(inherited)


def _source_category(lines: list[str], source_file: str) -> str:
    for line in lines:
        match = _CATEGORY_RE.match(line)
        if match and match.group(1):
            return match.group(1)
    return Path(source_file).stem


def _abbreviate_home(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home().resolve())
    except ValueError:
        return str(resolved)
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def _archive_properties(
    selection: _ArchiveSelection,
    *,
    source_file: str,
    category: str,
    archived_at: datetime,
) -> list[tuple[str, str]]:
    properties = [
        ("ARCHIVE_TIME", archived_at.strftime("[%Y-%m-%d %a %H:%M]")),
        ("ARCHIVE_FILE", _abbreviate_home(source_file)),
    ]
    if selection.outline_path:
        properties.append(("ARCHIVE_OLPATH", selection.outline_path))
    if category:
        properties.append(("ARCHIVE_CATEGORY", category))
    if selection.item.state:
        properties.append(("ARCHIVE_TODO", selection.item.state))
    if selection.inherited_tags:
        properties.append(("ARCHIVE_ITAGS", " ".join(selection.inherited_tags)))
    return properties


def _add_archive_drawer(
    subtree: list[str], properties: list[tuple[str, str]]
) -> list[str]:
    """Add or update stock ARCHIVE_* properties on the subtree root."""
    child_at = len(subtree)
    root_match = _ANY_HEADING_RE.match(subtree[0])
    if root_match is None:
        raise ArchiveError("selected task does not begin with an Org heading")
    root_level = len(root_match.group("stars"))
    for index in range(1, len(subtree)):
        match = _ANY_HEADING_RE.match(subtree[index])
        if match and len(match.group("stars")) > root_level:
            child_at = index
            break

    drawer_start = None
    drawer_end = None
    candidate = 1
    if (
        candidate < child_at
        and subtree[candidate].strip().upper() != ":PROPERTIES:"
    ):
        while candidate < child_at and _PLANNING_RE.match(subtree[candidate]):
            candidate += 1
    if candidate < child_at and subtree[candidate].strip().upper() == ":PROPERTIES:":
        drawer_start = candidate
        for end in range(candidate + 1, child_at):
            if subtree[end].strip().upper() == ":END:":
                drawer_end = end
                break

    if drawer_start is not None and drawer_end is None:
        raise ArchiveError("unterminated property drawer in task subtree")

    property_lines = [f":{name}: {value}" for name, value in properties]
    if drawer_start is None:
        insert_at = 1
        while insert_at < child_at and _PLANNING_RE.match(subtree[insert_at]):
            insert_at += 1
        return [
            *subtree[:insert_at],
            ":PROPERTIES:",
            *property_lines,
            ":END:",
            *subtree[insert_at:],
        ]

    replacements = {name: value for name, value in properties}
    updated: list[str] = []
    seen: set[str] = set()
    assert drawer_end is not None
    for line in subtree[drawer_start + 1:drawer_end]:
        match = _PROPERTY_RE.match(line)
        name = match.group("name").upper() if match else ""
        if name in replacements:
            updated.append(f":{name}: {replacements[name]}")
            seen.add(name)
        else:
            updated.append(line)
    updated.extend(
        f":{name}: {value}" for name, value in properties if name not in seen
    )
    return [
        *subtree[:drawer_start + 1],
        *updated,
        *subtree[drawer_end:],
    ]


def _normalize_archive_levels(subtree: list[str], root_level: int) -> list[str]:
    shift = root_level - 1
    if shift == 0:
        return subtree
    normalized: list[str] = []
    for line in subtree:
        match = _ANY_HEADING_RE.match(line)
        if not match:
            normalized.append(line)
            continue
        level = len(match.group("stars"))
        normalized.append("*" * (level - shift) + line[level:])
    return normalized


def _todo_tokens(text: str) -> tuple[int | None, list[str], list[str]]:
    matches = [
        (index, match)
        for index, line in enumerate(text.splitlines())
        if (match := TODO_DIRECTIVE_RE.match(line))
    ]
    if len(matches) > 1:
        raise ArchiveError("multiple #+TODO declarations; merge them manually")
    if not matches:
        return None, ["TODO"], ["DONE"]
    index, match = matches[0]
    body = match.group(1)
    if body.count("|") != 1:
        raise ArchiveError("ambiguous #+TODO declaration; expected one '|' separator")
    active, terminal = (part.split() for part in body.split("|", 1))
    if not active or not terminal:
        raise ArchiveError("ambiguous #+TODO declaration; both sides must contain keywords")
    return index, active, terminal


def _keyword(token: str) -> str:
    match = _KEYWORD_RE.match(token)
    if not match:
        raise ArchiveError(f"cannot parse TODO keyword token: {token!r}")
    return match.group("keyword")


def _merged_todo_line(
    source_text: str, archive_text: str, moved_states: set[str]
) -> tuple[int | None, str]:
    _, source_active, source_terminal = _todo_tokens(source_text)
    archive_index, archive_active, archive_terminal = _todo_tokens(archive_text)
    if archive_index is None:
        archive_active = []
        archive_terminal = []

    active_tokens = list(archive_active)
    terminal_tokens = list(archive_terminal)
    active_names = {_keyword(token) for token in active_tokens}
    terminal_names = {_keyword(token) for token in terminal_tokens}
    overlap = active_names & terminal_names
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ArchiveError(f"archive TODO keyword is both active and terminal: {names}")

    for token in source_active:
        name = _keyword(token)
        if name in terminal_names:
            raise ArchiveError(f"TODO keyword {name} changed from terminal to active")
        if name not in active_names:
            active_tokens.append(token)
            active_names.add(name)
    for token in source_terminal:
        name = _keyword(token)
        if name in active_names:
            raise ArchiveError(f"TODO keyword {name} changed from active to terminal")
        if name not in terminal_names:
            terminal_tokens.append(token)
            terminal_names.add(name)
    historical_states = _workflow_states_in_headings(archive_text)
    for state in sorted(historical_states):
        if state in active_names or state in terminal_names:
            continue
        if state == "TODO":
            active_tokens.append(state)
            active_names.add(state)
        else:
            terminal_tokens.append(state)
            terminal_names.add(state)
    for state in sorted(moved_states):
        if state == "TODO":
            if state not in active_names:
                active_tokens.append(state)
                active_names.add(state)
        elif state not in terminal_names:
            if state in active_names:
                raise ArchiveError(f"TODO keyword {state} is configured as active")
            terminal_tokens.append(state)
            terminal_names.add(state)

    return archive_index, f"#+TODO: {' '.join(active_tokens)} | {' '.join(terminal_tokens)}"


def _append_archive(
    archive_text: str,
    blocks: list[list[str]],
    *,
    source_text: str,
    source_file: str,
    moved_states: set[str],
) -> str:
    directive_index, directive = _merged_todo_line(
        source_text, archive_text, moved_states
    )
    if archive_text:
        lines = archive_text.splitlines()
        if directive_index is None:
            insert_at = 1 if lines and "-*- mode: org -*-" in lines[0] else 0
            lines.insert(insert_at, directive)
        else:
            lines[directive_index] = directive
    else:
        lines = [
            "# -*- mode: org -*-",
            directive,
            "",
            f"Archived entries from file {_abbreviate_home(source_file)}",
        ]

    while lines and not lines[-1].strip():
        lines.pop()
    for block in blocks:
        lines.extend(["", *block])
    return "\n".join(lines) + "\n"


def archive_tasks(
    source_text: str,
    archive_text: str,
    *,
    source_file: str,
    task_id: str | None = None,
    archived_at: datetime | None = None,
) -> ArchiveResult:
    """Move selected task subtrees into a stock-shaped ``.org_archive`` file.

    With no ID, every ``DONE`` heading is selected, pruning descendants when a
    selected ancestor already moves them. With an ID, that subtree is selected
    regardless of state. The returned texts are not written by this helper.
    """
    lines = source_text.splitlines()
    items = parse_org(source_text)
    if task_id is not None:
        normalized = normalize_id(task_id)
        item = find_by_id(items, normalized)
        if item is None:
            raise TaskNotFound(normalized)
        candidates = [item]
    else:
        candidates = [item for item in items if item.state == "DONE"]

    if not candidates:
        return ArchiveResult(source_text, archive_text, ())

    _, active_tokens, terminal_tokens = _todo_tokens(source_text)
    workflow_keywords = set(TASK_STATES) | {
        _keyword(token) for token in [*active_tokens, *terminal_tokens]
    }

    base_tags = _file_tags(lines)
    selections: list[_ArchiveSelection] = []
    for item in candidates:
        end = _subtree_end(lines, item.line_num, item.level)
        if any(
            selection.start <= item.line_num < selection.end
            for selection in selections
        ):
            continue
        outline_path, inherited_tags = _context_before(
            lines,
            item.line_num,
            item.level,
            base_tags,
            workflow_keywords,
        )
        selections.append(
            _ArchiveSelection(
                item=item,
                start=item.line_num,
                end=end,
                outline_path=outline_path,
                inherited_tags=inherited_tags,
            )
        )

    now = archived_at or datetime.now().astimezone()
    category = _source_category(lines, source_file)
    blocks: list[list[str]] = []
    for selection in selections:
        subtree = lines[selection.start:selection.end]
        subtree = _add_archive_drawer(
            subtree,
            _archive_properties(
                selection,
                source_file=source_file,
                category=category,
                archived_at=now,
            ),
        )
        blocks.append(_normalize_archive_levels(subtree, selection.item.level))

    source_lines = list(lines)
    for selection in reversed(selections):
        del source_lines[selection.start:selection.end]
    updated_source = _restore_final_newline(source_lines, source_text)
    updated_archive = _append_archive(
        archive_text,
        blocks,
        source_text=source_text,
        source_file=source_file,
        moved_states={selection.item.state for selection in selections},
    )
    return ArchiveResult(
        updated_source,
        updated_archive,
        tuple(selection.item.id for selection in selections),
    )


# Order of the keyword cycle, mirroring Emacs org-mode's TODO fast-cycling.
# ortask only uses two keywords, so the ring simply flips between them.
STATE_RING = ("TODO", "DONE")


def next_state(state: str) -> str:
    """Return the next keyword in the ``TODO``/``DONE`` ring.

    Emacs-style cycling: ``TODO`` -> ``DONE`` -> ``TODO``. An unrecognized
    state cycles to the first ring entry (``TODO``).
    """
    try:
        idx = STATE_RING.index(state)
    except ValueError:
        return STATE_RING[0]
    return STATE_RING[(idx + 1) % len(STATE_RING)]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def find_repair_problems(text: str) -> list[tuple[int, str, str]]:
    """Return list of (line_num, problem_description, suggested_fix_line).

    Problems detected:
    - Duplicate IDs
    - Headings under * Tasks that have TODO/DONE but no valid ID
    - Subtask IDs that don't match their parent's ID prefix
    """
    lines = text.splitlines()
    start, end = find_tasks_range(lines)
    if start < 0:
        return []

    problems: list[tuple[int, str, str]] = []
    seen_ids: dict[str, int] = {}
    parent_stack: list[tuple[int, str]] = []  # (level, id)

    for i in range(start + 1, end):
        m = HEADING_RE.match(lines[i])
        if m:
            tid = m.group("id")
            level = len(m.group("stars"))

            # Check duplicates
            if tid in seen_ids:
                problems.append((i, f"duplicate ID {tid} (first seen line {seen_ids[tid] + 1})", ""))
            else:
                seen_ids[tid] = i

            # Update parent stack
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()

            # Check subtask ID matches parent
            if parent_stack and "." in tid:
                expected_prefix = parent_stack[-1][1] + "."
                if not tid.startswith(expected_prefix):
                    problems.append((
                        i,
                        f"subtask {tid} doesn't match parent {parent_stack[-1][1]}",
                        "",
                    ))

            parent_stack.append((level, tid))
        else:
            # Check for headings under * Tasks that lack an ID
            bm = BARE_HEADING_RE.match(lines[i])
            if bm:
                rest = bm.group("rest").strip()
                if any(rest.startswith(state) for state in TASK_STATES):
                    problems.append((i, f"heading has task keyword but no valid task ID", ""))

    return problems


# ---------------------------------------------------------------------------
# Template application (ortask.py apply)
# ---------------------------------------------------------------------------

def _parse_week_arg(week: str) -> tuple[int, int]:
    """Parse a ``--week`` value into ``(iso_year_4digit, week_number)``.

    Accepts the same forms as week IDs elsewhere — two- or four-digit year,
    upper- or lowercase ``W``, with or without a leading ``tw``. Two-digit years
    are read as ``20YY``. Raises :class:`TemplateError` on anything else.
    """
    match = WEEK_ID_PARTS_RE.match(normalize_id(week.strip()))
    if not match or match.group("suffix"):
        raise TemplateError(
            f"invalid --week {week!r} (expected a week like 2026W26 or 26W26)"
        )
    year_raw = match.group("year")
    year4 = int(year_raw) if len(year_raw) == 4 else 2000 + int(year_raw)
    return year4, int(match.group("week"))


def resolve_week_target(
    week: str | None,
    date_str: str | None,
    today: date | None = None,
) -> tuple[int, int, date]:
    """Resolve ``(iso_year_4digit, week_number, week_start_monday)``.

    - neither given: use ``today`` (default: the system date).
    - ``--week`` only: that week; the label date is its Monday.
    - ``--date`` only: derive the ISO week and Monday from the date.
    - both: the date must fall inside the week, else :class:`TemplateError`.

    All math is ISO-calendar based, so the year is the ISO year.
    """
    iso_year: int | None = None
    iso_week: int | None = None

    if week is not None:
        iso_year, iso_week = _parse_week_arg(week)

    if date_str is not None:
        try:
            target = date.fromisoformat(date_str.strip())
        except ValueError:
            raise TemplateError(f"invalid --date {date_str!r} (expected YYYY-MM-DD)")
        d_year, d_week, _ = target.isocalendar()
        if week is not None and (iso_year, iso_week) != (d_year, d_week):
            raise TemplateError(
                f"--date {date_str} (ISO {d_year}W{d_week:02d}) is not in "
                f"--week {week} (ISO {iso_year}W{iso_week:02d})"
            )
        iso_year, iso_week = d_year, d_week

    if iso_year is None:
        ref = today if today is not None else date.today()
        iso_year, iso_week, _ = ref.isocalendar()

    monday = date.fromisocalendar(iso_year, iso_week, 1)
    return iso_year, iso_week, monday


def _weekly_replacements(
    iso_year: int, week_num: int, monday: date
) -> list[tuple[str, str]]:
    """Ordered (placeholder, value) pairs for the weekly profile.

    Ordered **longest literal first** so a shorter placeholder cannot match
    inside a longer one (``YYWNN`` inside ``twYYWNN``; ``Month Day`` inside
    ``Next Month Day``).
    """
    next_monday = monday + timedelta(days=7)
    year2 = f"{iso_year % 100:02d}"
    week2 = f"{week_num:02d}"
    label = f"{monday.strftime('%B')} {monday.day}"
    next_label = f"{next_monday.strftime('%B')} {next_monday.day}"
    return [
        ("twYYYYWNN", f"tw{iso_year}W{week2}"),
        ("twYYWNN", f"tw{year2}W{week2}"),
        ("YYYYWNN", f"{iso_year}W{week2}"),
        ("YYWNN", f"{year2}W{week2}"),
        ("Next Month Day", next_label),
        ("Month Day", label),
    ]


def _apply_replacements(line: str, repls: list[tuple[str, str]]) -> str:
    for placeholder, value in repls:
        line = line.replace(placeholder, value)
    return line


def apply_template(
    text: str,
    iso_year: int,
    week_num: int,
    monday: date,
    *,
    profile: str = "weekly",
) -> tuple[list[str], list[str], str]:
    """Instantiate the file's single ``* Template`` subtree for one week.

    Returns ``(new_lines, inserted_block, week_id)``. ``inserted_block`` is the
    instantiated Org lines (also what ``--dry-run`` prints); ``new_lines`` is the
    whole file with the block inserted at the end of ``* Tasks``. Raises
    :class:`TemplateError` for an unknown profile, a missing/duplicate/empty
    ``* Template``, or a generated ID already present under ``* Tasks``.
    """
    if profile != "weekly":
        raise TemplateError(f"unknown template profile: {profile!r}")

    lines = text.splitlines()

    count = count_template_sections(lines)
    if count == 0:
        raise TemplateError("no '* Template' section found")
    if count > 1:
        raise TemplateError(f"found {count} '* Template' sections; only one is supported")

    t_start, t_end = find_template_range(lines)
    body = lines[t_start + 1:t_end]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise TemplateError("'* Template' section is empty")

    repls = _weekly_replacements(iso_year, week_num, monday)
    block = [_apply_replacements(line, repls) for line in body]
    week_id = f"tw{iso_year % 100:02d}W{week_num:02d}"

    # Duplicate-ID guard, comparing canonically (tw26W26 == tw2026W26).
    existing = {canonical_id(t.id) for t in parse_org(text)}
    generated = [m.group("id") for m in (HEADING_RE.match(line) for line in block) if m]
    dups = sorted({g for g in generated if canonical_id(g) in existing})
    if dups:
        raise TemplateError(
            f"generated task ID(s) already exist under * Tasks: {', '.join(dups)}"
        )

    start, end = find_tasks_range(lines)
    if start < 0:
        # No * Tasks yet — create one at end of file (mirrors add_task).
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("* Tasks")
        insert_at = len(lines)
    else:
        # End of the * Tasks subtree, backing up over any trailing blank lines
        # so the block stays inside * Tasks and the blank before the next
        # heading is preserved.
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1

    new_lines = lines[:insert_at] + block + lines[insert_at:]
    return new_lines, block, week_id
