#!/usr/bin/env python3
"""Minimal project task menu for org-backed workspaces.

This script owns the interactive terminal UI. Project discovery, config
resolution, and task parsing/editing come from ``ortasklib``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import core, tasks
from ortasklib.manager import (
    Project,
    canonical_org_file,
    discover_projects,
    resolve_registry,
)


DETAIL_LINE_LIMIT = 20

# ANSI escape codes for colorful prompts
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_RESET = "\033[0m"


@dataclass(frozen=True)
class MenuItem:
    label: str
    detail: str
    task: core.TodoItem | None = None
    line_num: int | None = None


def _priority_rank(item: core.TodoItem) -> int:
    if item.priority == "A":
        return 0
    if item.priority == "B":
        return 1
    if item.priority == "C":
        return 2
    return 3


def _task_sort_key(item: core.TodoItem) -> tuple[int, int, int, int]:
    return (
        0 if item.state == "TODO" else 1,
        _priority_rank(item),
        item.level,
        item.line_num,
    )


def _read_only_headings(text: str) -> list[MenuItem]:
    items: list[MenuItem] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("*"):
            continue
        stars, _, title = line.partition(" ")
        if not stars or any(ch != "*" for ch in stars):
            continue
        indent = "  " * max(len(stars) - 1, 0)
        items.append(MenuItem(f"{indent}{title.strip()}", "read-only org heading", line_num=i))
    return items


def load_menu_items(org_file: Path, include_done: bool = False) -> list[MenuItem]:
    text = org_file.read_text(encoding="utf-8")
    task_items = core.parse_org(text)
    if task_items:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for task in task_items:
            key = core.canonical_id(task.id)
            if key in seen:
                duplicates.add(task.id)
            seen.add(key)
        if duplicates:
            dupes = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate task IDs in {org_file}: {dupes}")
        filtered = [task for task in task_items if include_done or task.state == "TODO"]
        return [
            MenuItem(
                label=f"[{task.state}] {task.id} {task.text}",
                detail="ortask task",
                task=task,
                line_num=task.line_num,
            )
            for task in sorted(filtered, key=_task_sort_key)
        ]
    return _read_only_headings(text)


def _prompt_choice(
    count: int,
    *,
    allow_back: bool = True,
    allow_editor: bool = False,
) -> str:
    suffix = "number"
    if allow_editor:
        suffix += ", e=open editor"
    if allow_back:
        suffix += ", b=back"
    suffix += ", q=quit"
    return input(f"{ANSI_BOLD}{ANSI_CYAN}{suffix}> {ANSI_RESET}").strip().lower()


def _print_items(title: str, items: list[MenuItem]) -> None:
    print()
    print(title)
    for idx, item in enumerate(items, start=1):
        print(f"  {idx}. {item.label}")
    if not items:
        print("  (no items)")


def _show_context(org_file: Path, item: MenuItem) -> None:
    print()
    if item.task:
        items = core.parse_org(org_file.read_text(encoding="utf-8"))
        lines: list[str] = []
        selected = item.task
        prefix = selected.id + "."
        for task in items:
            if task.id == selected.id or task.id.startswith(prefix):
                lines.append(core.build_org_heading(task))
                lines.extend(task.body_lines)
        for line in lines[:DETAIL_LINE_LIMIT]:
            print(line)
        if len(lines) > DETAIL_LINE_LIMIT:
            print(f"... truncated {len(lines) - DETAIL_LINE_LIMIT} more line(s)")
        return

    lines = org_file.read_text(encoding="utf-8").splitlines()
    if item.line_num is None:
        return
    start = item.line_num
    base_level = len(lines[start].split(" ", 1)[0])
    display_lines = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("*"):
            level = len(line.split(" ", 1)[0])
            if level <= base_level:
                break
        display_lines.append(line)
    for line in display_lines[:DETAIL_LINE_LIMIT]:
        print(line)
    if len(display_lines) > DETAIL_LINE_LIMIT:
        print(f"... truncated {len(display_lines) - DETAIL_LINE_LIMIT} more line(s)")


def _direct_subtasks(org_file: Path, item: MenuItem) -> list[MenuItem]:
    if item.task is None:
        return []
    selected = item.task
    prefix = selected.id + "."
    items = core.parse_org(org_file.read_text(encoding="utf-8"))
    children = [
        task for task in items
        if task.id.startswith(prefix) and task.level == selected.level + 1
    ]
    return [
        MenuItem(
            label=f"[{task.state}] {task.id} {task.text}",
            detail="ortask task",
            task=task,
            line_num=task.line_num,
        )
        for task in children
    ]


def _open_editor(org_file: Path, line_num: int | None) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        print("VISUAL or EDITOR is not set")
        return
    parts = shlex.split(editor)
    if not parts:
        print("VISUAL or EDITOR is empty")
        return
    editor_name = Path(parts[0]).name
    line = None if line_num is None else line_num + 1
    if line is not None and editor_name in {"vi", "vim", "nvim", "less"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
        return
    if line is not None and editor_name in {"emacs", "emacsclient"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
        return
    if line is not None and editor_name in {"nano", "pico"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
        return
    if line is not None and editor_name in {"code", "codium"}:
        subprocess.run(parts + ["--goto", f"{org_file}:{line}"], check=False)
        return
    subprocess.run(parts + [str(org_file)], check=False)


def task_menu(project: Project, include_done: bool) -> bool:
    org_file = canonical_org_file(project)
    while True:
        try:
            items = load_menu_items(org_file, include_done=include_done)
        except ValueError as exc:
            print(exc)
            return True
        _print_items(f"{project.name} tasks ({org_file})", items)
        choice = _prompt_choice(len(items), allow_editor=True)
        if choice == "q":
            return False
        if choice == "b":
            return True
        if choice == "e":
            _open_editor(org_file, None)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            print("invalid choice")
            continue

        item = items[int(choice) - 1]
        if not focus_menu(org_file, item):
            return False


def focus_menu(org_file: Path, item: MenuItem) -> bool:
    _show_context(org_file, item)
    while True:
        subtasks = _direct_subtasks(org_file, item)
        print()
        if subtasks:
            print("Subtasks:")
            for idx, subtask in enumerate(subtasks, start=1):
                print(f"  {idx}. {subtask.label}")
        print("Actions:")
        if item.task:
            print("  d. mark DONE")
        print("  e. open in editor")
        print("  b. back to task menu")
        print("  q. quit")
        choice = input(f"{ANSI_BOLD}{ANSI_CYAN}number, d/e/b/q> {ANSI_RESET}").strip().lower()
        if choice == "q":
            return False
        if choice == "b":
            return True
        if choice.isdigit() and 1 <= int(choice) <= len(subtasks):
            if not focus_menu(org_file, subtasks[int(choice) - 1]):
                return False
            _show_context(org_file, item)
        elif choice == "d" and item.task:
            confirm = input(f"{ANSI_BOLD}{ANSI_CYAN}mark {item.task.id} DONE? [y/N]> {ANSI_RESET}").strip().lower()
            if confirm == "y":
                text = org_file.read_text(encoding="utf-8")
                try:
                    new_lines = tasks.change_state(text, item.task.id, "DONE")
                except tasks.TaskNotFound:
                    new_lines = None
                if new_lines is not None:
                    core.write_lines(org_file, new_lines)
                print(f"marked {item.task.id} DONE")
                return True
        elif choice == "e":
            _open_editor(org_file, item.line_num)
        else:
            print("invalid choice")


def project_menu(workspace: Path, include_done: bool) -> int:
    while True:
        projects = discover_projects(workspace)
        print()
        print(f"Projects in {workspace}")
        for idx, project in enumerate(projects, start=1):
            rel_file = project.org_file.relative_to(workspace)
            print(f"  {idx}. {project.name}    {rel_file}")
        if not projects:
            print("  (no project org files found)")
        choice = _prompt_choice(len(projects), allow_back=False)
        if choice == "q":
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(projects):
            print("invalid choice")
            continue
        if not task_menu(projects[int(choice) - 1], include_done):
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal project menu for org task files.",
    )
    parser.add_argument(
        "--registry",
        dest="registry",
        default=None,
        help="project registry directory containing project subdirectories",
    )
    parser.add_argument(
        "--include-done",
        action="store_true",
        help="include DONE tasks in ortask-compatible files",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace, display_path = resolve_registry(args.registry)
    print(f"Finding project in {display_path}")
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1
    return project_menu(workspace, args.include_done)


if __name__ == "__main__":
    raise SystemExit(main())
