"""Local task helpers used by ``ortask.py``.

These functions take Org text (or parsed items) and return data or new line
lists; they never read/write files, print, or call ``sys.exit``. ``ortask.py``
owns the CLI translation: file I/O, human-readable output, and exit codes.
"""

from __future__ import annotations

import json

from .core import (
    BARE_HEADING_RE,
    HEADING_RE,
    NUMERIC_ID_RE,
    TodoItem,
    build_org_heading,
    canonical_id,
    find_by_id,
    find_tasks_range,
    normalize_id,
    parse_org,
)


class TaskNotFound(Exception):
    """Raised by edit/show helpers when a task ID is absent.

    ``str(exc)`` is the (normalized) ID that could not be found.
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
