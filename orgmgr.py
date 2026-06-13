#!/usr/bin/env python3
"""orgmgr — global operations command for the ortask suite of tools.

Handles project-level and multi-file Org operations. This script owns argument
parsing and output formatting; project discovery and summaries live in
``ortasklib.manager``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import manager


def cmd_list(args: argparse.Namespace) -> int:
    config_path = manager.default_config_path()
    workspace, display_path = manager.resolve_projdir(args.projdir, config_path)

    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects_data = manager.summarize_projects(workspace, include_all=args.all)

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
