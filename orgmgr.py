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


def _registry_not_set_up(registry_path: Path) -> None:
    friendly = manager.friendly_path(registry_path)
    print("orgmgr.py projadd: project registry not set up yet.", file=sys.stderr)
    print(f"Run `orgmgr.py migrate` once to create {friendly}", file=sys.stderr)
    print("(migrating any existing projtui.py `projdir` config), then re-run projadd.",
          file=sys.stderr)


def cmd_projadd(args: argparse.Namespace) -> int:
    registry_path = manager.registry_config_path()
    if not manager.registry_exists(registry_path):
        _registry_not_set_up(registry_path)
        return 1

    raw = args.path
    resolved = Path(raw).expanduser().resolve()

    # Resolve the one project directory and its task file (no recursion).
    if args.file is not None:
        if not resolved.is_dir():
            print(f"projadd: not a directory: {raw}", file=sys.stderr)
            return 1
        project_dir = resolved
        file_arg = Path(args.file).expanduser()
        org_file = file_arg if file_arg.is_absolute() else project_dir / file_arg
        store_file = True
    elif resolved.is_file():
        project_dir = resolved.parent
        org_file = resolved
        store_file = True
    elif resolved.is_dir():
        project_dir = resolved
        org_file = core.discover_org_file(project_dir)
        store_file = False
    else:
        print(f"projadd: path not found: {raw}", file=sys.stderr)
        return 1

    if org_file is None or not org_file.exists():
        print(f"projadd: no Org task file found in {project_dir}", file=sys.stderr)
        return 1

    text = org_file.read_text(encoding="utf-8")
    if not manager.has_task_section(text):
        print(f"projadd: {org_file} has no '* Tasks' section", file=sys.stderr)
        return 1

    name = args.name or project_dir.name
    if store_file:
        stored = manager.friendly_path(org_file.resolve())
    else:
        stored = manager.friendly_path(project_dir, raw)

    registry = manager.read_registry(registry_path) or {}
    if name in registry and not args.force:
        print(f"projadd: project '{name}' already registered as {registry[name]} "
              f"(use --force to overwrite)", file=sys.stderr)
        return 1
    for other_name, other_path in registry.items():
        if other_name != name and other_path == stored:
            print(f"warning: {stored} is already registered as '{other_name}'",
                  file=sys.stderr)
            break

    if args.dry_run:
        print(f"[dry-run] would register: {name} = {stored}")
        print(f"[dry-run] task file: {org_file}")
        return 0

    registry[name] = stored
    manager.write_registry(registry_path, registry)
    print(f"registered {name} = {stored}")
    print(f"task file: {org_file}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    registry_path = manager.registry_config_path()
    existing = manager.read_registry(registry_path)

    if existing is not None and not args.force:
        print(f"already migrated: {manager.friendly_path(registry_path)} has a "
              f"[projects] registry ({len(existing)} project(s)).")
        print("Re-run with --force to merge in newly discovered projects.")
        return 0

    # Locate the source projdir: explicit --projdir, else the legacy projtui.ini.
    if args.projdir:
        source: Path | None = Path(args.projdir).expanduser()
    else:
        projdir_raw = manager.read_config_projdir(manager.default_config_path())
        source = Path(projdir_raw).expanduser() if projdir_raw else None

    discovered: dict[str, str] = {}
    if source is not None:
        if not source.is_dir():
            print(f"migrate: projdir not found: {source}", file=sys.stderr)
            return 1
        discovered = manager.collect_projdir_projects(source.resolve())

    # Merge: existing entries win on a name collision (per --force semantics).
    merged = dict(existing or {})
    for name, path in discovered.items():
        if name in merged:
            if merged[name] != path:
                print(f"warning: keeping existing '{name}' = {merged[name]} "
                      f"(discovered {path})", file=sys.stderr)
            continue
        merged[name] = path

    if args.dry_run:
        print(f"[dry-run] would write {manager.friendly_path(registry_path)} with "
              f"{len(merged)} project(s):")
        for name, path in merged.items():
            print(f"  {name} = {path}")
        return 0

    manager.write_registry(registry_path, merged)
    if discovered:
        src = manager.friendly_path(source.resolve()) if source else "(none)"
        print(f"migrated {len(discovered)} project(s) from {src} into "
              f"{manager.friendly_path(registry_path)}.")
    else:
        print(f"initialized empty registry at {manager.friendly_path(registry_path)} "
              f"(no projdir projects to import).")
    print("Note: `[projtui] projdir` is now deprecated in favor of the registry.")
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

    # projadd subcommand
    p_add = sub.add_parser(
        "projadd",
        help="register one project directory in the shared registry",
    )
    p_add.add_argument(
        "path",
        nargs="?",
        default=".",
        help="project directory (or Org file); default: current directory",
    )
    p_add.add_argument("--name", default=None,
                       help="registry name (default: directory basename)")
    p_add.add_argument("--file", default=None,
                       help="use this Org file directly instead of discovery")
    p_add.add_argument("--force", action="store_true",
                       help="overwrite an existing entry with the same name")
    p_add.add_argument("--dry-run", action="store_true",
                       help="show what would be written without changing config")

    # migrate subcommand
    p_mig = sub.add_parser(
        "migrate",
        help="initialize the shared registry (importing any projdir projects)",
    )
    # SUPPRESS default so this subparser does not clobber a global --projdir.
    p_mig.add_argument("--projdir", default=argparse.SUPPRESS,
                       help="workspace to import, overriding projtui.ini")
    p_mig.add_argument("--force", action="store_true",
                       help="merge into an existing registry")
    p_mig.add_argument("--dry-run", action="store_true",
                       help="show the registry that would be written")

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
