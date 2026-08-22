#!/usr/bin/env python3
"""ortask — query and edit TODO tasks in org-mode files.

Operates on the ``* Tasks`` subtree when present, otherwise on valid task
headings across the org file, using standard TODO/DONE keywords and stable task
IDs (t0001, tw26W24, etc.).

This script is a thin CLI front-end: argument parsing, dispatch, and turning
``ortasklib`` results into human-readable output and exit codes. The parsing,
ID, and edit logic lives in ``ortasklib.core`` and ``ortasklib.tasks``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import log as eventlog
from ortasklib import manager, tasks
from ortasklib.core import (  # noqa: F401 — re-exported for tooling/tests
    TASKS_HEADING_RE,
    OrgFileDiscoveryError,
    TodoItem,
    atomic_write_pair,
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


DEFAULT_NEW_TASK_FILE = "tasks.org"
BOOTSTRAP_TASK_FILE_NAMES = {"tasks.org", "task.org", "todo.org", "TODO.org"}


def _is_dedicated_task_file(path: Path) -> bool:
    """True for filenames ortask may initialize as task files."""
    name = path.name
    return name in BOOTSTRAP_TASK_FILE_NAMES or name.endswith(".task.org")


def _can_create_tasks_section(path: Path, text: str) -> bool:
    """Only bootstrap empty files that are clearly intended to be task files."""
    return not text.strip() and _is_dedicated_task_file(path)


def _archive_path(path: Path) -> Path:
    """Return the stock Org archive path beside the canonical source file."""
    source = path.expanduser().resolve()
    return source.with_name(source.name + "_archive")


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    text = args.file.read_text(encoding="utf-8")
    all_items = parse_org(text)

    if not all_items:
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
    archive_path = _archive_path(args.file)
    archive_text = (
        archive_path.read_text(encoding="utf-8")
        if archive_path.exists()
        else ""
    )
    try:
        new_lines, new_id = tasks.add_task(
            text,
            args.title,
            args.parent,
            allow_create_section=_can_create_tasks_section(args.file, text),
            reserved_ids=tasks.task_ids_in_headings(archive_text),
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
    eventlog.record(
        eventlog.make_event(
            "ort",
            "add",
            task={"id": new_id, "title": args.title},
            detail={"parent": normalize_id(args.parent)} if args.parent else None,
        ),
        source_file=args.file,
    )
    print(f"added {new_id} \"{args.title}\" to {args.file}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    path = args.file.expanduser()
    if not _is_dedicated_task_file(path):
        print(
            f"init: not a dedicated task-file name: {path.name}",
            file=sys.stderr,
        )
        return 1
    if not path.parent.is_dir():
        print(f"init: parent directory not found: {path.parent}", file=sys.stderr)
        return 1

    if path.exists():
        if not path.is_file():
            print(f"init: not a file: {path}", file=sys.stderr)
            return 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(f"init: cannot read {path}: {exc}", file=sys.stderr)
            return 1
        if text.strip():
            print(f"init: refusing to overwrite nonempty file: {path}", file=sys.stderr)
            return 1
        try:
            _write_lines(path, ["* Tasks"])
        except OSError as exc:
            print(f"init: cannot write {path}: {exc}", file=sys.stderr)
            return 1
    else:
        created = False
        try:
            # Exclusive creation preserves the refusal-to-overwrite contract if
            # another process creates the path after the existence check.
            with path.open("x", encoding="utf-8") as handle:
                created = True
                handle.write("* Tasks\n")
        except FileExistsError:
            print(
                f"init: refusing to overwrite existing file: {path}",
                file=sys.stderr,
            )
            return 1
        except OSError as exc:
            if created:
                path.unlink(missing_ok=True)
            print(f"init: cannot create {path}: {exc}", file=sys.stderr)
            return 1

    eventlog.record(
        eventlog.make_event("ort", "init", detail={"created": True}),
        source_file=path,
    )
    print(f"initialized {path}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: archive
# ---------------------------------------------------------------------------

def cmd_archive(args: argparse.Namespace) -> int:
    source_path = args.file.expanduser().resolve()
    archive_path = _archive_path(args.file)
    try:
        source_text = source_path.read_text(encoding="utf-8")
        archive_text = (
            archive_path.read_text(encoding="utf-8")
            if archive_path.exists()
            else ""
        )
        source_items = {
            canonical_id(item.id): item for item in parse_org(source_text)
        }
        result = tasks.archive_tasks(
            source_text,
            archive_text,
            source_file=str(source_path),
            task_id=args.id,
        )
        if not result.task_ids:
            print(f"no DONE tasks to archive in {source_path}")
            return 0
        atomic_write_pair(
            archive_path,
            result.archive_text,
            source_path,
            result.source_text,
        )
    except tasks.TaskNotFound as exc:
        print(f"task not found: {exc}", file=sys.stderr)
        return 1
    except tasks.ArchiveError as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 1

    session = eventlog.new_session()
    events = []
    for task_id in result.task_ids:
        item = source_items.get(canonical_id(task_id))
        events.append(
            eventlog.make_event(
                "ort",
                "archive",
                session=session,
                task={"id": task_id, "title": item.text if item else ""},
                detail={"archive": manager.friendly_path(archive_path)},
            )
        )
    eventlog.record_many(events, source_file=source_path)

    noun = "subtree" if len(result.task_ids) == 1 else "subtrees"
    print(
        f"archived {len(result.task_ids)} {noun} "
        f"({', '.join(result.task_ids)}) to {archive_path}"
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommands: done / open
# ---------------------------------------------------------------------------

def _change_state(args: argparse.Namespace, target: str) -> int:
    args.id = normalize_id(args.id)
    text = args.file.read_text(encoding="utf-8")
    item = find_by_id(parse_org(text), args.id)
    try:
        new_lines = tasks.change_state(text, args.id, target)
    except tasks.TaskNotFound:
        print(f"task not found: {args.id}", file=sys.stderr)
        return 1
    if new_lines is not None:
        _write_lines(args.file, new_lines)
        eventlog.record(
            eventlog.make_event(
                "ort",
                "done" if target == "DONE" else "open",
                task={
                    "id": args.id,
                    "title": item.text if item else "",
                    "from": item.state if item else None,
                    "to": target,
                },
            ),
            source_file=args.file,
        )
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
    session = eventlog.new_session()
    generated = parse_org("* Tasks\n" + "\n".join(block) + "\n")
    events = []
    for item in generated:
        events.append(
            eventlog.make_event(
                "ort",
                "apply",
                session=session,
                task={"id": item.id, "title": item.text},
                detail={"template": args.template, "week": week_id[2:]},
            )
        )
    eventlog.record_many(events, source_file=args.file)
    print(f"applied template '{args.template}' as {week_id} "
          f"({len(block)} lines) to {args.file}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: interactive
# ---------------------------------------------------------------------------

def cmd_interactive(args: argparse.Namespace) -> int:
    from ortasklib import taskui

    return taskui.local_file_menu(args.file, include_done=True)


# ---------------------------------------------------------------------------
# Subcommand: log
# ---------------------------------------------------------------------------

def cmd_log(args: argparse.Namespace) -> int:
    """Read task activity without consulting it as task state."""
    try:
        day_start, since, until = eventlog.time_window(
            args.since, args.until, args.day_start
        )
        registry, _ = manager.resolve_registry()
        project = None
        file_filter = None
        if not args.all:
            canonical = args.file.expanduser().resolve()
            project = eventlog.project_for_file(canonical, registry)
            if project is None:
                file_filter = manager.friendly_path(canonical)
        events = eventlog.read_events(
            registry=registry,
            since=since,
            until=until,
            project=project,
            file=file_filter,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"log: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        for event in events:
            sys.stdout.write(event.raw)
        return 0
    output = (
        eventlog.format_org(events, day_start=day_start)
        if args.format == "org"
        else eventlog.format_plain(events)
    )
    if output:
        print(output)
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
        help="org file to operate on (default: tasks.org, task.org, *.task.org, or compatibility names)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="open an interactive task menu for the resolved org file",
    )

    sub = parser.add_subparsers(dest="command")

    # Keep subparser registration alphabetical; argparse preserves this order.
    p_add = sub.add_parser("add", help="add a new task")
    p_add.add_argument("title", metavar="TITLE")
    p_add.add_argument("--parent", metavar="ID", default=None)

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

    p_archive = sub.add_parser(
        "archive", help="move DONE tasks or one task subtree to the archive")
    p_archive.add_argument("id", metavar="ID", nargs="?", default=None)

    p_done = sub.add_parser("done", help="mark a task DONE")
    p_done.add_argument("id", metavar="ID")

    sub.add_parser("help", help="show this help message")

    sub.add_parser("init", help="create an empty dedicated task file")

    # list remains the default when no subcommand is supplied.
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

    p_log = sub.add_parser("log", help="read the append-only activity log")
    p_log.add_argument("--since", metavar="WHEN", default=None)
    p_log.add_argument("--until", metavar="WHEN", default=None)
    p_log.add_argument("--all", action="store_true",
                       help="include events from every registered project")
    p_log.add_argument("--limit", type=int, default=None,
                       help="show only the newest N matching events")
    p_log.add_argument("--day-start", default="00:00", metavar="HH:MM")
    p_log.add_argument("--format", choices=["plain", "json", "org"], default="plain")

    p_open = sub.add_parser("open", help="reopen a task (DONE -> TODO)")
    p_open.add_argument("id", metavar="ID")

    p_repair = sub.add_parser("repair", help="find and fix ID problems")
    p_repair.add_argument("--dry-run", action="store_true")

    p_show = sub.add_parser("show", help="show a single task by ID")
    p_show.add_argument("id", metavar="ID")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return 0

    cmd = args.command or "list"

    if cmd == "init" and args.interactive:
        print("init: --interactive is not supported", file=sys.stderr)
        return 1

    if args.file is None:
        if cmd == "init":
            configured = os.environ.get("ORTASK_FILE")
            args.file = Path(configured) if configured else Path(DEFAULT_NEW_TASK_FILE)
        else:
            try:
                resolved = resolve_org_file()
            except OrgFileDiscoveryError as exc:
                print(exc, file=sys.stderr)
                return 1
            if resolved is None:
                if cmd == "log" and args.all and not args.interactive:
                    args.file = None
                elif cmd == "add" and not args.interactive:
                    args.file = Path(DEFAULT_NEW_TASK_FILE)
                else:
                    print("no org file found (create tasks.org or use --file)",
                          file=sys.stderr)
                    return 1
            else:
                args.file = resolved

    if cmd != "init" and args.file is not None and not args.file.exists():
        if (
            cmd == "add"
            and not args.interactive
            and _is_dedicated_task_file(args.file)
            and args.file.parent.exists()
        ):
            args.file.write_text("", encoding="utf-8")
        else:
            print(f"file not found: {args.file}", file=sys.stderr)
            return 1

    if args.interactive:
        return cmd_interactive(args)

    # For list, fill in defaults that argparse only sets when the
    # subcommand is explicitly given
    if cmd == "list" and args.command is None:
        args.state = "todo"
        args.root_only = False
        args.items = None
        args.format = "plain"

    dispatch = {
        "add": cmd_add,
        "apply": cmd_apply,
        "archive": cmd_archive,
        "done": cmd_done,
        "init": cmd_init,
        "list": cmd_list,
        "log": cmd_log,
        "open": cmd_open,
        "repair": cmd_repair,
        "show": cmd_show,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
