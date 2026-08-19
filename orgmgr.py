#!/usr/bin/env python3
"""orgmgr — global operations command for the ortask suite of tools.

Handles project-level and multi-file Org operations. This script owns argument
parsing and output formatting; project discovery and summaries live in
``ortasklib.manager``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import core, manager, menu


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


class _PcdSession:
    """Pick a project, resolve its directory stack, and write it to a file.

    The output file has exactly one meaning: the directory stack the calling
    shell should have afterwards, top entry first. Editing happens inside this
    session and never travels back through that channel, so ``misc/pcd.func.sh``
    only ever reads a list of directories.
    """

    def __init__(
        self,
        display_path: str,
        projects: list[manager.Project],
        out_path: Path,
    ) -> None:
        self.display_path = display_path
        self.projects = projects
        self.out_path = out_path
        self.status = 1
        self.error: str | None = None

    # -- output ----------------------------------------------------------

    def write_stack(self, directories: list[Path]) -> str:
        """Write the resolved stack and record success. Returns a footer line."""
        try:
            text = "".join(f"{d}\n" for d in directories)
            self.out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.error = f"could not write {self.out_path}: {exc}"
            return "Could not write the directory stack"
        self.status = 0
        count = len(directories)
        return f"{count} {'directory' if count == 1 else 'directories'}"

    def stack_for(
        self, source: manager.DirectorySource, project: manager.Project
    ) -> list[Path]:
        root = manager.real_project_path(project)
        # A section that exists but lists nothing would otherwise write an empty
        # stack, which the shell function reads as "do nothing at all".
        return manager.resolve_directories(source.entries or [], root) or [root]

    # -- views -----------------------------------------------------------

    def run(self) -> int:
        session = menu.InlineMenuSession(
            self.project_view(),
            action_keys=["e"],
            final_message="No project selected",
        )
        session.run()
        if self.error:
            print(self.error, file=sys.stderr)
        return self.status

    def project_view(self) -> menu.MenuView:
        rows = [
            menu.MenuRow(
                index + 1,
                "PROJECT",
                f"{project.name:<12}  "
                f"{manager.friendly_path(manager.real_project_path(project))}",
            )
            for index, project in enumerate(self.projects)
        ]

        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            if result.index is None or not 0 <= result.index < len(self.projects):
                return
            project = self.projects[result.index]
            if result.action == "select":
                self.select_stack(session, project)
            elif result.action == "edit":
                session.push_view(self.edit_view(project))

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title="Projects",
            summary=f"Registry: {self.display_path}",
            instruction="↑↓/jk · ↵ select · e edit · Esc/q cancel",
            empty_text="(no projects)",
            select_help="Load the highlighted project's directory stack",
            back_help="Cancel without changing the directory stack",
            actions={
                "e": menu.MenuAction(
                    "edit", "e", "Edit the highlighted project's directory list"
                )
            },
        )

    def select_stack(
        self, session: menu.InlineMenuSession, project: manager.Project
    ) -> None:
        """Resolve the stack, asking first when more than one source defines one."""
        sources = manager.directory_sources(project)
        if not sources:
            # No ``* Directories`` anywhere: the project root is the whole stack.
            message = self.write_stack([manager.real_project_path(project)])
            session.pop_view(message=f"{project.name}: {message} (project root)")
            return
        if len(sources) == 1:
            message = self.write_stack(self.stack_for(sources[0], project))
            session.pop_view(message=f"{project.name}: {message}")
            return
        session.push_view(self.source_view(project, sources))

    def source_view(
        self,
        project: manager.Project,
        sources: list[manager.DirectorySource],
    ) -> menu.MenuView:
        rows = [
            menu.MenuRow(
                index + 1,
                source.label.upper(),
                f"{len(source.entries or []):>2} dirs  "
                f"{manager.friendly_path(source.path)}",
            )
            for index, source in enumerate(sources)
        ]

        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            if result.action != "select" or result.index is None:
                return
            if not 0 <= result.index < len(sources):
                return
            source = sources[result.index]
            message = self.write_stack(self.stack_for(source, project))
            session.pop_view()
            session.pop_view(message=f"{project.name} ({source.label}): {message}")

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"{project.name}: which directory stack?",
            summary="The private list is not part of the project's repository",
            instruction="↑↓/jk · ↵ use this list · Esc/b/q back",
            select_help="Load the highlighted list",
            back_help="Back to the project list",
        )

    def edit_view(self, project: manager.Project) -> menu.MenuView:
        candidates = manager.directory_candidates(project)
        rows = []
        for index, candidate in enumerate(candidates):
            if candidate.entries is not None:
                note = f"{len(candidate.entries):>2} dirs"
            elif candidate.path.exists():
                note = "no * Directories"
            else:
                note = "       new"
            rows.append(
                menu.MenuRow(
                    index + 1,
                    candidate.label.upper(),
                    f"{note}  {manager.friendly_path(candidate.path)}",
                )
            )

        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            if result.action != "select" or result.index is None:
                return
            if not 0 <= result.index < len(candidates):
                return
            self.edit_source(session, candidates[result.index])

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"{project.name}: edit which directory list?",
            summary="The private list is not part of the project's repository",
            instruction="↑↓/jk · ↵ edit · Esc/b/q back",
            select_help="Open the highlighted file in your editor",
            back_help="Back to the project list",
        )

    def edit_source(
        self, session: menu.InlineMenuSession, candidate: manager.DirectorySource
    ) -> None:
        """Open one directory list in the user's editor, then return to the picker.

        The private file lives in the registry, which ``orgmgr.py`` owns, so it
        is created on demand. The project's task file is never written here —
        ``ortask.py`` owns Org content — so a missing section is only reported.
        """
        if candidate.label == "private" and not candidate.path.exists():
            try:
                core.atomic_write(candidate.path, "* Directories\n")
            except OSError as exc:
                session.set_transient_message(f"could not create the file: {exc}")
                return
        argv = core.editor_argv(candidate.path)
        if argv is None:
            session.set_transient_message("VISUAL or EDITOR is not set")
            return
        hint = ""
        if candidate.label == "project" and candidate.entries is None:
            hint = " (add a '* Directories' section to use it)"

        def run_editor() -> None:
            subprocess.run(argv, check=False)

        def done() -> None:
            session.pop_view(
                message=f"Edited {manager.friendly_path(candidate.path)}{hint}"
            )

        session.suspend(run_editor, on_done=done)


def _pcd_fallback(session: _PcdSession) -> int:
    """Numbered-menu path for pipes and terminals without prompt_toolkit."""
    for index, project in enumerate(session.projects, start=1):
        real = manager.friendly_path(manager.real_project_path(project))
        print(f"{index:>3}  {project.name:<12}  {real}")
    try:
        choice = menu.prompt_text("number, Esc/q=cancel").strip().lower()
    except menu.ContextCancelled:
        return 1
    if not choice.isdigit() or not 1 <= int(choice) <= len(session.projects):
        return 1

    project = session.projects[int(choice) - 1]
    sources = manager.directory_sources(project)
    if not sources:
        session.write_stack([manager.real_project_path(project)])
        return session.status
    if len(sources) > 1:
        for index, source in enumerate(sources, start=1):
            print(f"{index:>3}  {source.label:<8}  "
                  f"{manager.friendly_path(source.path)}")
        try:
            pick = menu.prompt_text("directory list, Esc/q=cancel").strip()
        except menu.ContextCancelled:
            return 1
        if not pick.isdigit() or not 1 <= int(pick) <= len(sources):
            return 1
        sources = [sources[int(pick) - 1]]
    session.write_stack(session.stack_for(sources[0], project))
    if session.error:
        print(session.error, file=sys.stderr)
    return session.status


def cmd_pcd(args: argparse.Namespace) -> int:
    """Select a project and write its directory stack to ``--out``."""
    workspace, display_path = manager.resolve_registry(args.registry)
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects = manager.discover_projects(workspace)
    if not projects:
        print("No projects discovered.", file=sys.stderr)
        return 1

    session = _PcdSession(
        display_path,
        projects,
        Path(args.out).expanduser().resolve(),
    )
    if menu.interactive_select_available():
        return session.run()
    return _pcd_fallback(session)


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
