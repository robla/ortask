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

from ortasklib import core, manager


def cmd_list(args: argparse.Namespace) -> int:
    workspace, display_path = manager.resolve_registry(args.registry)

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


def _replace_symlink(link_path: Path, target: Path) -> None:
    """Create (or repoint) ``link_path`` as a symlink to absolute ``target``."""
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(target)


def cmd_projadd(args: argparse.Namespace) -> int:
    registry, registry_display = manager.resolve_registry(args.registry)

    project_dir = Path(args.path).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"projadd: not a directory: {args.path}", file=sys.stderr)
        return 1

    # Resolve the task file: explicit --file, else single-directory discovery.
    org_file: Path | None = None
    if args.file is not None:
        file_arg = Path(args.file).expanduser()
        candidate = file_arg if file_arg.is_absolute() else project_dir / file_arg
        if not candidate.exists():
            print(f"projadd: task file not found: {candidate}", file=sys.stderr)
            return 1
        if not manager.has_task_section(candidate.read_text(encoding="utf-8")):
            print(f"projadd: {candidate} has no '* Tasks' section", file=sys.stderr)
            return 1
        org_file = candidate.resolve()
    else:
        found = core.discover_org_file(project_dir)
        if found is not None:
            if manager.has_task_section(found.read_text(encoding="utf-8")):
                org_file = found.resolve()
            else:
                print(f"warning: {found} has no '* Tasks' section; "
                      f"adding project link only", file=sys.stderr)

    name = args.name or project_dir.name
    subdir = registry / name

    if subdir.exists() and not args.force:
        print(f"projadd: project '{name}' already exists at {subdir} "
              f"(use --force to repoint its links)", file=sys.stderr)
        return 1

    # Warn if another project subdir already links to this project directory.
    if registry.is_dir():
        for other in sorted(registry.iterdir(), key=lambda p: p.name.lower()):
            if not other.is_dir() or other.name == name:
                continue
            if any(e.is_symlink() and e.resolve() == project_dir for e in other.iterdir()):
                print(f"warning: {project_dir} is already linked from '{other.name}'",
                      file=sys.stderr)
                break

    project_link = subdir / project_dir.name
    org_link = (subdir / org_file.name) if org_file is not None else None

    if args.dry_run:
        print(f"[dry-run] would create {subdir}/")
        print(f"[dry-run]   {project_dir.name} -> {project_dir}")
        if org_link is not None:
            print(f"[dry-run]   {org_file.name} -> {org_file}")
        return 0

    subdir.mkdir(parents=True, exist_ok=True)
    _replace_symlink(project_link, project_dir)
    if org_link is not None:
        _replace_symlink(org_link, org_file)

    print(f"added project '{name}' under {registry_display}")
    print(f"  {project_dir.name} -> {project_dir}")
    if org_link is not None:
        print(f"  {org_file.name} -> {org_file}")
    else:
        print("  (no task-file link — none discovered)")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    ortask_path = manager.ortask_config_path()
    existing = manager.read_ortask_registry(ortask_path)

    if args.registry:
        source_raw = args.registry
    elif existing is not None and not args.force:
        source_raw = existing            # keep the value already recorded
    else:
        source_raw = manager.DEFAULT_REGISTRY

    stored = manager.friendly_path(Path(source_raw).expanduser().resolve(), source_raw)

    if args.dry_run:
        print(f"[dry-run] would set [projects] registry = {stored} in "
              f"{manager.friendly_path(ortask_path)}")
        return 0

    manager.write_ortask_registry(ortask_path, stored)
    print(f"recorded registry = {stored} in {manager.friendly_path(ortask_path)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="orgmgr — global operations command for the ortask suite of tools.",
    )
    parser.add_argument(
        "--registry",
        dest="registry",
        default=None,
        help="project registry directory containing project subdirectories",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("help", help="show this help message")

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

    # projadd subcommand
    p_add = sub.add_parser(
        "projadd",
        help="add one project to the registry (creates a symlink subdir)",
    )
    p_add.add_argument(
        "path",
        nargs="?",
        default=".",
        help="project directory to add; default: current directory",
    )
    p_add.add_argument("--name", default=None,
                       help="project subdirectory name (default: directory basename)")
    p_add.add_argument("--file", default=None,
                       help="task file to link instead of running discovery")
    # SUPPRESS default so this subparser does not clobber a global override.
    p_add.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry directory to add into")
    p_add.add_argument("--force", action="store_true",
                       help="repoint links in an existing project subdirectory")
    p_add.add_argument("--dry-run", action="store_true",
                       help="show what would be created without changing anything")

    # migrate subcommand
    p_mig = sub.add_parser(
        "migrate",
        help="record the registry directory in ortask.ini",
    )
    # SUPPRESS default so this subparser does not clobber a global override.
    p_mig.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry directory to record")
    p_mig.add_argument("--force", action="store_true",
                       help="use the default even if ortask.ini already has one")
    p_mig.add_argument("--dry-run", action="store_true",
                       help="show what would be written and removed")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None or args.command == "help":
        parser.print_help()
        return 0

    cmd = args.command

    dispatch = {
        "list": cmd_list,
        "projadd": cmd_projadd,
        "migrate": cmd_migrate,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
