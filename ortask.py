#!/usr/bin/env python3
"""ortask — query and edit TODO tasks in org-mode files.

Operates on the ``* Tasks`` subtree of an org file, using standard
TODO/DONE keywords and stable task IDs (t0001, tw26W24, etc.).

This script is a thin CLI front-end: argument parsing, dispatch, and turning
``ortasklib`` results into human-readable output and exit codes. The parsing,
ID, and edit logic lives in ``ortasklib.core`` and ``ortasklib.tasks``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import tasks
from ortasklib.core import (  # noqa: F401 — re-exported for tooling/tests
    TASKS_HEADING_RE,
    OrgFileDiscoveryError,
    TodoItem,
    build_org_heading as _build_org_heading,
    canonical_id,
    filter_items,
    find_by_id,
    find_tasks_range as _find_tasks_range,
    normalize_id,
    parse_org,
    resolve_org_file,
    write_lines as _write_lines,
)
from ortasklib.tasks import (  # noqa: F401 — re-exported for tooling/tests
    find_repair_problems as _find_repair_problems,
    format_json,
    format_org,
    format_plain,
)


BOOTSTRAP_TASK_FILE_NAMES = {"task.org", "todo.org", "tasks.org", "TODO.org"}


def _can_create_tasks_section(path: Path, text: str) -> bool:
    """Only bootstrap empty files that are clearly intended to be task files."""
    if text.strip():
        return False
    name = path.name
    return name in BOOTSTRAP_TASK_FILE_NAMES or name.endswith(".task.org")


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
    try:
        lines = tasks.show_lines(text, args.id)
    except tasks.TaskNotFound:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: add
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    try:
        new_lines, new_id = tasks.add_task(
            text,
            args.title,
            args.parent,
            allow_create_section=_can_create_tasks_section(args.file, text),
        )
    except tasks.MissingTasksSection:
        print(
            f"no '* Tasks' section found in {args.file}; add one explicitly "
            "before using 'ortask.py add'",
            file=sys.stderr,
        )
        return 1
    except tasks.TaskNotFound as exc:
        print(f"parent task not found: {exc}", file=sys.stderr)
        return 1
    _write_lines(args.file, new_lines)
    print(f"added {new_id} \"{args.title}\" to {args.file}")
    return 0


# ---------------------------------------------------------------------------
# Subcommands: done / open
# ---------------------------------------------------------------------------

def _change_state(args: argparse.Namespace, target: str) -> int:
    args.id = normalize_id(args.id)
    text = args.file.read_text(encoding="utf-8")
    try:
        new_lines = tasks.change_state(text, args.id, target)
    except tasks.TaskNotFound:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    if new_lines is not None:
        _write_lines(args.file, new_lines)
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    return _change_state(args, "DONE")


def cmd_open(args: argparse.Namespace) -> int:
    return _change_state(args, "TODO")


# ---------------------------------------------------------------------------
# Subcommand: repair
# ---------------------------------------------------------------------------

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
# Subcommand: apply
# ---------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    try:
        iso_year, week_num, monday = tasks.resolve_week_target(args.week, args.date)
        new_lines, block, week_id = tasks.apply_template(
            text, iso_year, week_num, monday, profile=args.template
        )
    except tasks.TemplateError as exc:
        print(f"apply: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        for line in block:
            print(line)
        return 0

    _write_lines(args.file, new_lines)
    print(f"applied template '{args.template}' as {week_id} "
          f"({len(block)} lines) to {args.file}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: interactive
# ---------------------------------------------------------------------------

def cmd_interactive(args: argparse.Namespace) -> int:
    import projtui

    return projtui.local_file_menu(args.file, include_done=True)


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
        help="org file to operate on (default: task.org, *.task.org, or legacy names)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="open an interactive task menu for the resolved org file",
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

    # apply
    p_apply = sub.add_parser(
        "apply", help="instantiate the * Template subtree as new tasks")
    p_apply.add_argument("--template", default="weekly",
                         help="template profile to apply (default: weekly)")
    p_apply.add_argument("--week", default=None,
                         help="target ISO week, e.g. 2026W26 or 26W26 (default: this week)")
    p_apply.add_argument("--date", default=None,
                         help="target date YYYY-MM-DD; derives the week when --week is omitted")
    p_apply.add_argument("--dry-run", action="store_true",
                         help="print the tasks that would be inserted without writing")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return 0

    if args.file is None:
        try:
            resolved = resolve_org_file()
        except OrgFileDiscoveryError as exc:
            print(exc, file=sys.stderr)
            return 1
        if resolved is None:
            print("no org file found (create task.org or use --file)",
                  file=sys.stderr)
            return 1
        args.file = resolved

    if not args.file.exists():
        print(f"file not found: {args.file}", file=sys.stderr)
        return 1

    if args.interactive:
        return cmd_interactive(args)

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
        "apply": cmd_apply,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
