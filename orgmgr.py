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


def cmd_interactive(args: argparse.Namespace) -> int:
    """Open the registry-scoped interactive project browser."""
    import projtui

    workspace, display_path = manager.resolve_registry(args.registry)
    print(f"Finding project in {display_path}")
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1
    return projtui.project_menu(workspace, include_done=not args.todo_only)


def cmd_list(args: argparse.Namespace) -> int:
    workspace, display_path = manager.resolve_registry(args.registry)

    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects_data = manager.summarize_projects(workspace, include_all=args.all)

    if args.format == "json":
        print(json.dumps(projects_data, indent=2))
    else:
        print(f"Registry: {display_path}")
        for idx, p in enumerate(projects_data):
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

    # Resolve the task file: explicit --file, else local task-file discovery.
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
        try:
            found = core.discover_org_file(project_dir)
        except core.OrgFileDiscoveryError as exc:
            print(f"projadd: {exc}", file=sys.stderr)
            return 1
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


def _real_project_path(project: manager.Project) -> Path:
    for item in project.path.iterdir():
        if item.is_symlink() and item.resolve().is_dir():
            return item.resolve()
    if project.org_file:
        return project.org_file.resolve().parent
    return project.path


def cmd_pcd(args: argparse.Namespace) -> int:
    """Select a project and output its directory stack config to a file."""
    import os
    from ortasklib import menu

    workspace, display_path = manager.resolve_registry(args.registry)
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects = manager.discover_projects(workspace)
    if not projects:
        print("No projects discovered.", file=sys.stderr)
        return 0

    rows = []
    for idx, project in enumerate(projects, start=1):
        try:
            org_file = str(project.org_file.relative_to(workspace))
        except ValueError:
            org_file = str(project.org_file)
        rows.append(menu.ProjectRow(idx, project.name, org_file))

    project = None
    if menu.interactive_select_available():
        result = menu.select_project_menu(
            rows,
            title="Projects",
            summary=f"Registry: {display_path}",
            instruction="↑↓/jk · ↵ select · Esc/q exit",
            start_index=0,
        )
        if result.action == "select" and result.index is not None:
            project = projects[result.index]
    else:
        # Non-interactive fallback
        while True:
            menu.print_project_dashboard("Projects", workspace, rows)
            try:
                choice = menu.prompt_text("number, Esc/b/q=exit").lower()
            except menu.ContextCancelled:
                return 1
            if choice in {"b", "q"}:
                return 1
            if choice.isdigit() and 1 <= int(choice) <= len(projects):
                project = projects[int(choice) - 1]
                break
            print("invalid choice", file=sys.stderr)

    if project is None:
        return 1

    real_path = _real_project_path(project)
    out_path = Path(args.out).expanduser().resolve()

    if args.edit:
        proj_dirs_file = real_path / ".projdirs"
        if not proj_dirs_file.exists():
            try:
                proj_dirs_file.parent.mkdir(parents=True, exist_ok=True)
                proj_dirs_file.write_text(".\n", encoding="utf-8")
            except Exception as e:
                print(f"Error creating {proj_dirs_file}: {e}", file=sys.stderr)
                return 1
        try:
            out_path.write_text(str(proj_dirs_file) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"Error writing to output file {out_path}: {e}", file=sys.stderr)
            return 1
        return 0

    # Resolve directories
    proj_dirs_file = real_path / ".projdirs"
    resolved_dirs = []
    if proj_dirs_file.exists():
        try:
            lines = proj_dirs_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                expanded = os.path.expandvars(os.path.expanduser(line))
                path = Path(expanded)
                if not path.is_absolute():
                    path = (real_path / path).resolve()
                else:
                    path = path.resolve()
                resolved_dirs.append(str(path))
        except Exception as e:
            print(f"Warning: could not read {proj_dirs_file}: {e}", file=sys.stderr)

    if not resolved_dirs:
        resolved_dirs = [str(real_path.resolve())]

    try:
        out_path.write_text("\n".join(resolved_dirs) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"Error writing to output file {out_path}: {e}", file=sys.stderr)
        return 1

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
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="open the interactive project browser",
    )
    parser.add_argument(
        "--todo-only",
        action="store_true",
        help="start interactive task views with only TODO tasks visible",
    )

    sub = parser.add_subparsers(dest="command")
    # Keep subparser registration alphabetical; argparse preserves this order.
    sub.add_parser("help", help="show this help message")

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

    p_pcd = sub.add_parser(
        "pcd",
        help="select a project and write its directories to a file",
    )
    p_pcd.add_argument(
        "--out",
        required=True,
        help="output file to write selected paths to",
    )
    p_pcd.add_argument(
        "--edit",
        action="store_true",
        help="write the path to the project's .projdirs file instead of the directories",
    )
    # SUPPRESS default so this subparser does not clobber a global override.
    p_pcd.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry directory to select from")

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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.interactive:
        return cmd_interactive(args)

    if args.command is None or args.command == "help":
        parser.print_help()
        return 0

    cmd = args.command

    dispatch = {
        "list": cmd_list,
        "migrate": cmd_migrate,
        "pcd": cmd_pcd,
        "projadd": cmd_projadd,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
