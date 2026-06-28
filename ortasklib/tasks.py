"""Local task helpers used by ``ortask.py``.

These functions take Org text (or parsed items) and return data or new line
lists; they never read/write files, print, or call ``sys.exit``. ``ortask.py``
owns the CLI translation: file I/O, human-readable output, and exit codes.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from .core import (
    BARE_HEADING_RE,
    HEADING_RE,
    NUMERIC_ID_RE,
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


class TemplateError(Exception):
    """Raised by template-application helpers; ``str(exc)`` is user-facing.

    Covers an unknown profile, a missing/duplicate/empty ``* Template`` section,
    unparseable ``--week``/``--date`` input, mismatched week/date, and generated
    IDs that already exist under ``* Tasks``.
    """


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_plain(items: list[TodoItem]) -> str:
    lines = []
    for item in items:
        indent = "  " * (item.level - 2)
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

def next_toplevel_id(items: list[TodoItem]) -> str:
    max_num = 0
    for item in items:
        # Only consider top-level IDs (no dots)
        if "." not in item.id and NUMERIC_ID_RE.match(item.id):
            num = int(item.id[1:])
            max_num = max(max_num, num)
    return f"t{max_num + 1:04d}"


def next_subtask_id(items: list[TodoItem], parent_id: str) -> str:
    max_sub = 0
    prefix = parent_id + "."
    for item in items:
        if item.id.startswith(prefix):
            suffix = item.id[len(prefix):]
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

def add_task(text: str, title: str, parent: str | None = None) -> tuple[list[str], str]:
    """Insert a new task and return ``(new_lines, new_id)``.

    A top-level task gets the next ``tNNNN`` ID and is appended to the
    ``* Tasks`` subtree (creating the section if absent). A subtask is inserted
    after its parent's existing descendants with the next dotted child ID.
    Raises ``TaskNotFound`` if ``parent`` is given but does not resolve.
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
        new_id = next_subtask_id(items, parent_id)
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
            # No * Tasks section — create one at end of file
            lines.append("")
            lines.append("* Tasks")
            start = len(lines) - 1
            end = len(lines)
        new_id = next_toplevel_id(items)
        new_level = 2
        insert_at = end

    heading = "*" * new_level + f" TODO {new_id} {title}"
    lines.insert(insert_at, heading)
    return lines, new_id


def change_state(text: str, task_id: str, target: str) -> list[str] | None:
    """Switch a task's keyword to ``target`` (``"TODO"`` or ``"DONE"``).

    Returns the new line list, or ``None`` when the task is already in the
    target state (no write needed). Only the matched heading line changes.
    Raises ``TaskNotFound`` if ``task_id`` does not resolve.
    """
    lines = text.splitlines()
    items = parse_org(text)
    item = find_by_id(items, task_id)
    if item is None:
        raise TaskNotFound(task_id)
    if item.state == target:
        return None
    keyword_from = "TODO" if target == "DONE" else "DONE"
    lines[item.line_num] = lines[item.line_num].replace(keyword_from, target, 1)
    return lines


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
                if rest.startswith("TODO") or rest.startswith("DONE"):
                    problems.append((i, f"heading has TODO/DONE keyword but no valid task ID", ""))

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
