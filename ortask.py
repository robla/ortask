#!/usr/bin/env python3
"""ortask — query and edit TODO tasks in org-mode files.

Operates on the ``* Tasks`` subtree of an org file, using standard
TODO/DONE keywords and stable task IDs (t0001, tw26W24, etc.).
"""

from __future__ import annotations

import argparse
import json
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


def resolve_org_file() -> Path | None:
    """Find the default org file using the probe order.

    1. ORTASK_FILE env var
    2. Probe for well-known names: todo.org, tasks.org
    3. Pick first .org file alphabetically (warn if multiple)
    """
    env = os.environ.get("ORTASK_FILE")
    if env:
        return Path(env)

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
# Parser
# ---------------------------------------------------------------------------

def _find_tasks_range(lines: list[str]) -> tuple[int, int]:
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
    start, end = _find_tasks_range(lines)
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


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_plain(items: list[TodoItem]) -> str:
    lines = []
    for item in items:
        indent = "  " * (item.level - 2)
        lines.append(f"{indent}[{item.state}] {item.id} {item.text}")
    return "\n".join(lines)


def _build_org_heading(item: TodoItem) -> str:
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


def format_org(items: list[TodoItem]) -> str:
    return "\n".join(_build_org_heading(item) for item in items)


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
# Writer — atomic file replacement
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
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


def _write_lines(path: Path, lines: list[str]) -> None:
    _atomic_write(path, "\n".join(lines) + "\n" if lines else "")


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    all_items = parse_org(text)

    if not all_items:
        start, _ = _find_tasks_range(text.splitlines())
        if start < 0:
            print(f"no '* Tasks' section found in {args.file}", file=sys.stderr)
        else:
            print(f"no tasks found in {args.file}", file=sys.stderr)
        return 1

    items = filter_items(all_items, state=args.state, root_only=args.root_only,
                         max_items=args.items)
    if not items:
        print(f"no matching tasks", file=sys.stderr)
        return 0

    if args.format == "plain":
        out = format_plain(items)
    elif args.format == "json":
        out = format_json(items)
    elif args.format == "org":
        out = format_org(items)
    else:
        print(f"unknown format: {args.format}", file=sys.stderr)
        return 1
    print(out)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: show
# ---------------------------------------------------------------------------

def cmd_show(args: argparse.Namespace) -> int:
    args.id = normalize_id(args.id)
    text = args.file.read_text(encoding="utf-8")
    items = parse_org(text)
    item = find_by_id(items, args.id)
    if item is None:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1

    # Print the heading
    print(_build_org_heading(item))
    # Print body lines
    for line in item.body_lines:
        print(line)
    # Print subtasks
    prefix = item.id + "."
    for sub in items:
        if sub.id.startswith(prefix):
            print(_build_org_heading(sub))
            for line in sub.body_lines:
                print(line)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: add
# ---------------------------------------------------------------------------

def _next_toplevel_id(items: list[TodoItem]) -> str:
    max_num = 0
    for item in items:
        # Only consider top-level IDs (no dots)
        if "." not in item.id and NUMERIC_ID_RE.match(item.id):
            num = int(item.id[1:])
            max_num = max(max_num, num)
    return f"t{max_num + 1:04d}"


def _next_subtask_id(items: list[TodoItem], parent_id: str) -> str:
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


def cmd_add(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    lines = text.splitlines()
    items = parse_org(text)
    start, end = _find_tasks_range(lines)

    if args.parent:
        args.parent = normalize_id(args.parent)
        parent = find_by_id(items, args.parent)
        if parent is None:
            print(f"parent task not found: {args.parent}", file=sys.stderr)
            return 1
        parent_id = parent.id
        new_id = _next_subtask_id(items, parent_id)
        new_level = parent.level + 1
        # Insert after the last sibling/descendant of the parent
        insert_at = parent.line_num + 1
        prefix = parent_id + "."
        for item in items:
            if item.id.startswith(prefix) or item is parent:
                # Move past this item's heading and body
                insert_at = max(insert_at, item.line_num + 1 + len(item.body_lines))
    else:
        if start < 0:
            # No * Tasks section — create one at end of file
            lines.append("")
            lines.append("* Tasks")
            start = len(lines) - 1
            end = len(lines)
        new_id = _next_toplevel_id(items)
        new_level = 2
        insert_at = end

    heading = "*" * new_level + f" TODO {new_id} {args.title}"
    lines.insert(insert_at, heading)
    _write_lines(args.file, lines)
    print(f"added {new_id} \"{args.title}\" to {args.file}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: done
# ---------------------------------------------------------------------------

def cmd_done(args: argparse.Namespace) -> int:
    args.id = normalize_id(args.id)
    text = args.file.read_text(encoding="utf-8")
    lines = text.splitlines()
    items = parse_org(text)
    item = find_by_id(items, args.id)
    if item is None:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    if item.state == "DONE":
        return 0
    lines[item.line_num] = lines[item.line_num].replace("TODO", "DONE", 1)
    _write_lines(args.file, lines)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: open
# ---------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace) -> int:
    args.id = normalize_id(args.id)
    text = args.file.read_text(encoding="utf-8")
    lines = text.splitlines()
    items = parse_org(text)
    item = find_by_id(items, args.id)
    if item is None:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    if item.state == "TODO":
        return 0
    lines[item.line_num] = lines[item.line_num].replace("DONE", "TODO", 1)
    _write_lines(args.file, lines)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: repair
# ---------------------------------------------------------------------------

def _find_repair_problems(text: str) -> list[tuple[int, str, str]]:
    """Return list of (line_num, problem_description, suggested_fix_line).

    Problems detected:
    - Duplicate IDs
    - Headings under * Tasks that have TODO/DONE but no valid ID
    - Subtask IDs that don't match their parent's ID prefix
    """
    lines = text.splitlines()
    start, end = _find_tasks_range(lines)
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


def cmd_repair(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    problems = _find_repair_problems(text)

    if not problems:
        return 0

    for line_num, desc, _ in problems:
        print(f"line {line_num + 1}: {desc}", file=sys.stderr)

    if args.dry_run:
        return 2

    # For now, repair only reports. Full auto-fix of all problem types
    # (renumbering, ID assignment) is deferred — the problems are reported
    # so the user can fix them manually or in Emacs.
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ortask.py",
        description="Query and edit TODO tasks in org-mode files.",
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help="org file to operate on (default: todo.org, tasks.org, or first *.org)",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("help", help="show this help message")

    # list (default when no subcommand)
    p_list = sub.add_parser("list", help="print tasks")
    state_group = p_list.add_mutually_exclusive_group()
    state_group.add_argument("--todo", dest="state", action="store_const", const="todo",
                             help="show only open tasks (default)")
    state_group.add_argument("--done", dest="state", action="store_const", const="done",
                             help="show only completed tasks")
    state_group.add_argument("--all", dest="state", action="store_const", const="all",
                             help="show all tasks")
    p_list.set_defaults(state="todo")
    p_list.add_argument("--root-only", action="store_true")
    p_list.add_argument("--items", type=int, default=None)
    p_list.add_argument("--format", choices=["plain", "json", "org"], default="plain")

    # show
    p_show = sub.add_parser("show", help="show a single task by ID")
    p_show.add_argument("id", metavar="ID")

    # add
    p_add = sub.add_parser("add", help="add a new task")
    p_add.add_argument("title", metavar="TITLE")
    p_add.add_argument("--parent", metavar="ID", default=None)

    # done
    p_done = sub.add_parser("done", help="mark a task DONE")
    p_done.add_argument("id", metavar="ID")

    # open
    p_open = sub.add_parser("open", help="reopen a task (DONE -> TODO)")
    p_open.add_argument("id", metavar="ID")

    # repair
    p_repair = sub.add_parser("repair", help="find and fix ID problems")
    p_repair.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return 0

    if args.file is None:
        resolved = resolve_org_file()
        if resolved is None:
            print("no org file found (create todo.org or use --file)",
                  file=sys.stderr)
            return 1
        args.file = resolved

    if not args.file.exists():
        print(f"file not found: {args.file}", file=sys.stderr)
        return 1

    cmd = args.command or "list"

    # For list, fill in defaults that argparse only sets when the
    # subcommand is explicitly given
    if cmd == "list" and args.command is None:
        args.state = "todo"
        args.root_only = False
        args.items = None
        args.format = "plain"

    dispatch = {
        "list": cmd_list,
        "show": cmd_show,
        "add": cmd_add,
        "done": cmd_done,
        "open": cmd_open,
        "repair": cmd_repair,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
