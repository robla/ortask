#!/usr/bin/env python3
"""Minimal project task menu for org-backed workspaces."""

from __future__ import annotations

import argparse
import configparser
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import ortask


SKIP_PROJECT_DIRS = {".git", ".hg", ".svn", "__pycache__", "docs"}
DEFAULT_PROJDIR = "~/Projects"
CONFIG_SECTION = "projtui"
CONFIG_OPTION = "projdir"


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    org_file: Path


@dataclass(frozen=True)
class MenuItem:
    label: str
    detail: str
    task: ortask.TodoItem | None = None
    line_num: int | None = None


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "ortask" / "projtui.ini"
    return Path.home() / ".config" / "ortask" / "projtui.ini"


def read_config_projdir(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    if not parser.has_option(CONFIG_SECTION, CONFIG_OPTION):
        return None
    value = parser.get(CONFIG_SECTION, CONFIG_OPTION).strip()
    return value or None


def _friendly_path(path: Path, original: str | None = None) -> str:
    if original and original.startswith("~"):
        return original
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def resolve_projdir(cli_projdir: str | None, config_path: Path) -> tuple[Path, str]:
    raw = cli_projdir or read_config_projdir(config_path) or DEFAULT_PROJDIR
    resolved = Path(raw).expanduser().resolve()
    return resolved, _friendly_path(resolved, raw)


def _org_sort_key(path: Path) -> tuple[int, str]:
    lower = path.name.lower()
    if path.name == "TODO.org":
        return (0, lower)
    if lower == "todo.org":
        return (1, lower)
    if path.name.startswith("TODO-") and path.suffix == ".org":
        return (2, lower)
    return (3, lower)


def choose_org_file(project_dir: Path) -> Path | None:
    root_files = sorted(
        (p for p in project_dir.iterdir() if p.is_file() and p.suffix == ".org"),
        key=_org_sort_key,
    )
    if root_files:
        return root_files[0]

    nested: list[Path] = []
    for child in sorted(project_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        nested.extend(
            sorted(
                (p for p in child.iterdir() if p.is_file() and p.suffix == ".org"),
                key=_org_sort_key,
            )
        )
    return nested[0] if nested else None


def discover_projects(workspace: Path) -> list[Project]:
    projects: list[Project] = []
    for child in sorted(workspace.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in SKIP_PROJECT_DIRS or child.name.startswith("."):
            continue
        org_file = choose_org_file(child)
        if org_file:
            projects.append(Project(child.name, child, org_file))
    return projects


def _priority_rank(item: ortask.TodoItem) -> int:
    if item.priority == "A":
        return 0
    if item.priority == "B":
        return 1
    if item.priority == "C":
        return 2
    return 3


def _task_sort_key(item: ortask.TodoItem) -> tuple[int, int, int, int]:
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
    tasks = ortask.parse_org(text)
    if tasks:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for task in tasks:
            if task.id in seen:
                duplicates.add(task.id)
            seen.add(task.id)
        if duplicates:
            dupes = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate task IDs in {org_file}: {dupes}")
        filtered = [task for task in tasks if include_done or task.state == "TODO"]
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


def _prompt_choice(count: int, *, allow_back: bool = True) -> str:
    suffix = "number"
    if allow_back:
        suffix += ", b=back"
    suffix += ", q=quit"
    return input(f"{suffix}> ").strip().lower()


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
        print(ortask._build_org_heading(item.task))
        for line in item.task.body_lines:
            print(line)
        return

    lines = org_file.read_text(encoding="utf-8").splitlines()
    if item.line_num is None:
        return
    start = item.line_num
    base_level = len(lines[start].split(" ", 1)[0])
    print(lines[start])
    for line in lines[start + 1:]:
        if line.startswith("*"):
            level = len(line.split(" ", 1)[0])
            if level <= base_level:
                break
        print(line)


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
    if line_num is not None and editor_name in {"vi", "vim", "nvim"}:
        subprocess.run(parts + [f"+{line_num + 1}", str(org_file)], check=False)
        return
    subprocess.run(parts + [str(org_file)], check=False)


def task_menu(project: Project, include_done: bool) -> bool:
    while True:
        try:
            items = load_menu_items(project.org_file, include_done=include_done)
        except ValueError as exc:
            print(exc)
            return True
        _print_items(f"{project.name} tasks ({project.org_file})", items)
        choice = _prompt_choice(len(items))
        if choice == "q":
            return False
        if choice == "b":
            return True
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            print("invalid choice")
            continue

        item = items[int(choice) - 1]
        if not focus_menu(project.org_file, item):
            return False


def focus_menu(org_file: Path, item: MenuItem) -> bool:
    while True:
        print()
        print(f"Task: {item.label}")
        print("  1. show details")
        if item.task:
            print("  2. mark DONE")
        print("  3. open in editor")
        print("  b. back to task menu")
        print("  q. quit")
        choice = _prompt_choice(3)
        if choice == "q":
            return False
        if choice == "b":
            return True
        if choice == "1":
            _show_context(org_file, item)
        elif choice == "2" and item.task:
            confirm = input(f"mark {item.task.id} DONE? [y/N]> ").strip().lower()
            if confirm == "y":
                args = argparse.Namespace(file=org_file, id=item.task.id)
                ortask.cmd_done(args)
                print(f"marked {item.task.id} DONE")
                return True
        elif choice == "3":
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
        "--projdir",
        "--workspace",
        dest="projdir",
        default=None,
        help="project directory containing project subdirectories",
    )
    parser.add_argument(
        "--include-done",
        action="store_true",
        help="include DONE tasks in ortask-compatible files",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace, display_path = resolve_projdir(args.projdir, default_config_path())
    print(f"Finding project in {display_path}")
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1
    return project_menu(workspace, args.include_done)


if __name__ == "__main__":
    raise SystemExit(main())
