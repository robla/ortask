#!/usr/bin/env python3
"""orgmgr — global operations command for the ortask suite of tools.

Handles project-level and multi-file Org operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure local imports work regardless of execution directory
script_dir = Path(__file__).parent.resolve()
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import ortask
import projtui


def cmd_list(args: argparse.Namespace) -> int:
    config_path = projtui.default_config_path()
    workspace, display_path = projtui.resolve_projdir(args.projdir, config_path)

    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects = projtui.discover_projects(workspace)
    projects_data = []

    for project in projects:
        rel_file = project.org_file.relative_to(workspace)
        try:
            text = project.org_file.read_text(encoding="utf-8")
        except Exception as e:
            projects_data.append({
                "project": project.name,
                "file": str(rel_file),
                "warning": f"Could not read file: {e}",
                "tasks": []
            })
            continue

        # Check if * Tasks section exists
        lines = text.splitlines()
        has_tasks_section = any(ortask.TASKS_HEADING_RE.match(line) for line in lines)
        if not has_tasks_section:
            projects_data.append({
                "project": project.name,
                "file": str(rel_file),
                "warning": "no parseable * Tasks section found",
                "tasks": []
            })
            continue

        # Parse tasks
        tasks = ortask.parse_org(text)

        # Check for duplicate IDs
        seen = set()
        duplicates = set()
        for t in tasks:
            if t.id:
                key = ortask.canonical_id(t.id)
                if key in seen:
                    duplicates.add(t.id)
                seen.add(key)

        if duplicates:
            dupes_str = ", ".join(sorted(duplicates))
            projects_data.append({
                "project": project.name,
                "file": str(rel_file),
                "warning": f"duplicate task IDs: {dupes_str}",
                "tasks": []
            })
            continue

        # Filter to top-level tasks (level == 2)
        # By default, only include TODO tasks, unless --all is specified
        filtered_tasks = []
        for t in tasks:
            if t.level == 2:
                if args.all or t.state == "TODO":
                    filtered_tasks.append(t)

        projects_data.append({
            "project": project.name,
            "file": str(rel_file),
            "tasks": [
                {
                    "id": t.id,
                    "state": t.state,
                    "title": t.text
                }
                for t in filtered_tasks
            ]
        })

    # Format output
    if args.format == "json":
        print(json.dumps(projects_data, indent=2))
    else:
        for idx, p in enumerate(projects_data):
            if idx > 0:
                print()
            print(f"{p['project']}  {p['file']}")
            if "warning" in p:
                warning_msg = p["warning"]
                if "duplicate task IDs" in warning_msg:
                    print(f"  (invalid: {warning_msg})")
                else:
                    print(f"  (warning: {warning_msg})")
            else:
                for t in p["tasks"]:
                    print(f"  [{t['state']}] {t['id']} {t['title']}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="orgmgr — global operations command for the ortask suite of tools.",
    )
    parser.add_argument(
        "--projdir",
        "--workspace",
        dest="projdir",
        default=None,
        help="project directory containing project subdirectories (overrides config/default)",
    )

    sub = parser.add_subparsers(dest="command")

    # list subcommand
    p_list = sub.add_parser("list", help="list projects and top-level tasks")
    p_list.add_argument(
        "--all",
        action="store_true",
        help="include completed (DONE) tasks as well as open ones",
    )
    p_list.add_argument(
        "--format",
        choices=["plain", "json"],
        default="plain",
        help="output format (plain or json)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Default command is list
    cmd = args.command or "list"

    # Fill defaults for the default command when invoked without subcommand
    if args.command is None:
        args.all = False
        args.format = "plain"

    dispatch = {
        "list": cmd_list,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
