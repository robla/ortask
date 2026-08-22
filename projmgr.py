#!/usr/bin/env python3
"""projmgr — the project-layer command for the ortask suite of tools.

Owns the registry: listing projects, registering and removing them, checking
them, opening the project navigator, and writing a project's directory stack for
``cdproj``. ``ortask.py`` owns Org task content; this script never writes it.
``docs/projects.md`` is the model, ``docs/projmgr.md`` the command reference.
The intended aliases are ``pmgr`` and, for ``-i``, ``ptui``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import orglib  # noqa: E402 — after the sys.path insert above
from ortasklib import core, log as eventlog, manager, menu, taskui


# ---------------------------------------------------------------------------
# The one project list
#
# Every project surface — ``-i``, ``cdproj``, and both numbered fallbacks — builds
# rows from the same projects and anchors the highlight the same way. What
# differs is what Enter does and, because the two are easy to confuse otherwise,
# how each names itself and what its third column says.
# ---------------------------------------------------------------------------

PROJECT_MENU_INSTRUCTION = "↑↓/jk · ↵ open · C-g help · Esc/b/q exit"


def _project_note(project: manager.Project) -> str:
    """The trailing note for a project that cannot simply be opened."""
    if project.warning:
        return f"  ({project.warning})"
    if project.org_file is None:
        return "  (no task file)"
    return ""


def _project_location(project: manager.Project) -> str:
    """Where a project is, plus whatever the user needs told about it."""
    real = manager.friendly_path(manager.real_project_path(project))
    return f"{real}{_project_note(project)}"


def _project_summary(project: manager.Project) -> str:
    """Where the highlighted project actually lives.

    A project menu must never make the reader guess at a location, so this
    names the directory and the task file for whichever row is highlighted.
    The task file is given by name when it sits directly in the project
    directory, and by path when it does not.
    """
    directory = manager.real_project_path(project)
    parts = [manager.friendly_path(directory)]
    org_file = manager.canonical_org_file(project)
    if org_file is not None:
        parts.append(
            org_file.name
            if org_file.parent == directory
            else manager.friendly_path(org_file)
        )
    elif project.warning:
        parts.append(project.warning)
    else:
        parts.append("(no task file)")
    return "  ·  ".join(parts)


def _project_tasks(project: manager.Project) -> str:
    """How much open work a project has — the navigator's reason to exist.

    Counts the same top-level tasks ``projmgr.py list`` shows, so the number
    here and the rows there agree.
    """
    if project.warning:
        return f"({project.warning})"
    org_file = manager.canonical_org_file(project)
    if org_file is None:
        return "(no task file)"
    try:
        tasks = orglib.parse(org_file.read_text(encoding="utf-8")).tasks()
    except OSError:
        return "(unreadable)"
    if not tasks:
        return "(no tasks)"
    root_level = min(task.level for task in tasks)
    open_tasks = sum(
        1 for task in tasks if task.level == root_level and task.state == "TODO"
    )
    return f"{open_tasks} open"


def _project_stack(project: manager.Project) -> str:
    """Effective stack size before the aligned project location.

    A registry-defined stack is called "custom" in the UI. The asterisk is a
    compact footnote marker rather than an internal source-name label.
    """
    location = _project_location(project)
    sources = manager.directory_sources(project)
    if sources:
        source = sources[0]
        directories = _stack_for_source(source, project)
        marker = "*" if source.label == "private" else " "
    else:
        directories = [manager.real_project_path(project)]
        marker = " "
    return f"{len(directories):>2} dir{marker}  {location}"


def _project_location_and_tasks(project: manager.Project) -> str:
    """Location first, open work in parentheses — the navigator's table row."""
    return f"{_project_location(project)}  ({_project_tasks(project)})"


@dataclass(frozen=True)
class _ProjectListing:
    """How one surface presents the shared project list.

    ``ptui`` and ``cdproj`` show the same projects for different reasons. They
    differ here — in the title, the row label, and what the third column says —
    and nowhere else, so neither can drift out of step with the other about
    which projects exist or how to move around them.
    """

    title: str
    label: str
    detail_header: str
    detail: Callable[[manager.Project], str]
    #: The numbered dashboard's column, when it cannot be the picker's. A table
    #: has no highlight to describe, so anything the picker delegates to the
    #: summary line has to appear in the row instead. ``detail_header`` names
    #: this column, not the picker's.
    dashboard_detail: Callable[[manager.Project], str] | None = None

    def dashboard(self) -> Callable[[manager.Project], str]:
        return self.dashboard_detail or self.detail


NAVIGATOR = _ProjectListing(
    "Project navigator",
    "PROJ",
    "Location",
    _project_tasks,
    dashboard_detail=_project_location_and_tasks,
)
CDPROJ = _ProjectListing(
    "Change directory", "CD", "Directories", _project_stack
)


def _project_rows(
    projects: list[manager.Project], listing: _ProjectListing
) -> list[menu.MenuRow]:
    return [
        menu.MenuRow(
            index + 1,
            listing.label,
            f"{project.name:<12}  {listing.detail(project)}",
        )
        for index, project in enumerate(projects)
    ]


def _print_project_dashboard(
    projects: list[manager.Project],
    display_path: str,
    listing: _ProjectListing,
) -> None:
    detail = listing.dashboard()
    rows = [
        menu.ProjectRow(index + 1, project.name, detail(project))
        for index, project in enumerate(projects)
    ]
    menu.print_project_dashboard(
        listing.title, display_path, rows, listing.detail_header
    )


def _anchor_index(
    projects: list[manager.Project],
    selected_name: str | None,
    fallback_index: int,
) -> int:
    """Keep the highlight on the same project across a refresh."""
    if selected_name is not None:
        for index, project in enumerate(projects):
            if project.name == selected_name:
                return index
    if not projects:
        return 0
    return min(max(fallback_index, 0), len(projects) - 1)


def _project_view(
    projects: list[manager.Project],
    display_path: str,
    handle,
    *,
    listing: _ProjectListing,
    instruction: str,
    select_help: str,
    back_help: str | None = None,
    actions: dict | None = None,
    selected_index: int = 0,
    on_resume=None,
) -> menu.MenuView:
    view = menu.MenuView(
        rows=_project_rows(projects, listing),
        on_result=handle,
        title=listing.title,
        title_right=f"Registry: {display_path}",
        instruction=instruction,
        empty_text="(no projects)",
        select_help=select_help,
        back_help=back_help,
        selected_index=selected_index,
        on_resume=on_resume,
        actions=actions or {},
    )

    def selected_summary() -> str:
        index = view.selected_index
        if not 0 <= index < len(projects):
            return ""
        return _project_summary(projects[index])

    view.status_text = selected_summary
    return view


class _ProjectBrowser:
    """Project navigator: pick a project, work its tasks, come back."""

    def __init__(self, workspace: Path, display_path: str, include_done: bool) -> None:
        self.workspace = workspace
        self.display_path = display_path
        self.include_done = include_done
        self.session: menu.InlineMenuSession | None = None

    def run(self) -> None:
        session = menu.InlineMenuSession(
            self.view(),
            action_keys=taskui.InteractiveTaskController.action_keys(),
            final_message="No task changes",
        )
        self.session = session
        session.run()
        if session.error:
            print(session.error, file=sys.stderr)

    def view(
        self,
        selected_name: str | None = None,
        fallback_index: int = 0,
    ) -> menu.MenuView:
        projects = manager.discover_projects(self.workspace)

        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            if result.action != "select" or result.index is None:
                return
            if not 0 <= result.index < len(projects):
                return
            project = projects[result.index]
            org_file = manager.canonical_org_file(project)
            if org_file is None:
                session.set_transient_message(
                    f"{project.name}: no task file"
                    + (f" — {project.warning}" if project.warning else "")
                )
                return
            controller = taskui.InteractiveTaskController(
                project,
                taskui.OrgBuffer(
                    org_file,
                    project=project.name,
                    registry=self.workspace,
                ),
                self.include_done,
            )
            controller.attach(session)

        def resume(session: menu.InlineMenuSession) -> None:
            view = session.current_view
            index = view.selected_index
            name = projects[index].name if 0 <= index < len(projects) else None
            session.replace_view(self.view(name, index))

        return _project_view(
            projects,
            self.display_path,
            handle,
            listing=NAVIGATOR,
            instruction=PROJECT_MENU_INSTRUCTION,
            select_help="Open the highlighted project",
            selected_index=_anchor_index(projects, selected_name, fallback_index),
            on_resume=resume,
        )


def project_menu(workspace: Path, display_path: str, include_done: bool) -> int:
    """Interactive project navigator, with the numbered menu as the fallback."""
    if menu.interactive_select_available():
        _ProjectBrowser(workspace, display_path, include_done).run()
        return 0

    while True:
        projects = manager.discover_projects(workspace)
        _print_project_dashboard(projects, display_path, NAVIGATOR)
        try:
            choice = menu.prompt_text("number, Esc/q=quit").strip().lower()
        except menu.ContextCancelled:
            return 0
        if choice in {"b", "q", ""}:
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(projects):
            print("invalid choice")
            continue
        taskui.task_menu(
            projects[int(choice) - 1], include_done, registry=workspace
        )


def cmd_interactive(args: argparse.Namespace) -> int:
    """Open the registry-scoped interactive project navigator."""
    workspace, display_path = manager.resolve_registry(args.registry)
    print(f"Finding project in {display_path}")
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1
    return project_menu(workspace, display_path, include_done=not args.todo_only)


def cmd_list(args: argparse.Namespace) -> int:
    workspace, display_path = manager.resolve_registry(args.registry)

    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    if args.format == "names":
        # One bare project name per line. This is the shell completions' only
        # view of the registry, so the marker rule stays here in Python instead
        # of being re-implemented (wrongly) as a glob in bash.
        for project in manager.discover_projects(workspace):
            print(project.name)
        return 0

    projects_data = manager.summarize_projects(workspace, include_all=args.all)

    if args.format == "json":
        print(json.dumps(projects_data, indent=2))
    else:
        print(f"Registry: {display_path}")
        for p in projects_data:
            print()
            print(f"{p['project']}  {p['file'] or p['path']}")
            if "warning" in p:
                warning_msg = p["warning"]
                if "duplicate task IDs" in warning_msg:
                    print(f"  (invalid: {warning_msg})")
                else:
                    print(f"  (warning: {warning_msg})")
            elif p["file"] is None:
                print("  (no task file)")
            else:
                for t in p["tasks"]:
                    print(f"  [{t['state']}] {t['id']} {t['title']}")

    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Read activity for one project or the complete registry."""
    try:
        day_start, since, until = eventlog.time_window(
            args.since, args.until, args.day_start
        )
        registry, _ = manager.resolve_registry(args.registry)
        events = eventlog.read_events(
            registry=registry,
            since=since,
            until=until,
            project=args.project,
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


def _replace_symlink(link_path: Path, target: Path) -> None:
    """Create (or repoint) ``link_path`` as a symlink to absolute ``target``."""
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(target)


def cmd_add(args: argparse.Namespace) -> int:
    """Register one project. With no path, register the project you are in."""
    registry, registry_display = manager.resolve_registry(args.registry)

    if args.path is None:
        # Implicit: find the project root, so running this from a subdirectory
        # registers the project rather than the subdirectory.
        project_dir = manager.project_root_for(Path.cwd())
        if project_dir != Path.cwd().resolve():
            print(f"using project root {manager.friendly_path(project_dir)}")
    else:
        # Explicit: take the path literally and walk nothing.
        project_dir = Path(args.path).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"add: not a directory: {args.path}", file=sys.stderr)
        return 1

    # Resolve the task file: explicit --file, else local task-file discovery.
    org_file: Path | None = None
    if args.file is not None:
        file_arg = Path(args.file).expanduser()
        candidate = file_arg if file_arg.is_absolute() else project_dir / file_arg
        if not candidate.exists():
            print(f"add: task file not found: {candidate}", file=sys.stderr)
            return 1
        if not manager.has_task_section(candidate.read_text(encoding="utf-8")):
            print(f"add: {candidate} has no '* Tasks' section", file=sys.stderr)
            return 1
        org_file = candidate.resolve()
    else:
        try:
            found = core.discover_org_file(project_dir)
        except core.OrgFileDiscoveryError as exc:
            print(f"add: {exc}", file=sys.stderr)
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
        print(f"add: project '{name}' already exists at {subdir} "
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

    eventlog.record(
        eventlog.make_event(
            "pmgr",
            "add",
            project=name,
            detail={
                "path": manager.friendly_path(project_dir),
                "task_file": manager.friendly_path(org_file) if org_file else None,
            },
        ),
        registry=registry,
        source_file=org_file,
    )

    print(f"added project '{name}' under {registry_display}")
    print(f"  {project_dir.name} -> {project_dir}")
    if org_link is not None:
        print(f"  {org_file.name} -> {org_file}")
    else:
        print("  (no task-file link — none discovered)")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
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


def _print_migration_errors(error: manager.RegistryMigrationError) -> None:
    for message in error.messages:
        print(f"migrate: {message}", file=sys.stderr)


def cmd_migrate(args: argparse.Namespace) -> int:
    """Move legacy per-entry directory files into the registry index."""
    registry, registry_display = manager.resolve_registry(args.registry)
    try:
        plan = manager.plan_registry_migration(registry)
    except manager.RegistryMigrationError as exc:
        _print_migration_errors(exc)
        return 1

    if args.dry_run:
        print(f"Registry: {registry_display}")
        print(f"[dry-run] proposed {manager.friendly_path(plan.index_path)}:")
        print(plan.index_text, end="" if plan.index_text.endswith("\n") else "\n")
        if plan.legacy_files:
            print("[dry-run] would remove:")
            for legacy in plan.legacy_files:
                print(f"  {manager.friendly_path(legacy.path)}")
        elif plan.writes_index:
            print("[dry-run] no legacy private files; would create migration marker")
        else:
            print("[dry-run] registry is already migrated; no changes")
        return 0

    try:
        manager.apply_registry_migration(plan)
    except manager.RegistryMigrationError as exc:
        _print_migration_errors(exc)
        return 1

    changed = plan.writes_index or bool(plan.legacy_files)
    if changed:
        eventlog.record(
            eventlog.make_event(
                "pmgr",
                "migrate",
                detail={"entries": len(plan.legacy_files)},
            ),
            registry=registry,
        )

    if plan.writes_index:
        print(f"wrote {manager.friendly_path(plan.index_path)}")
    elif not plan.legacy_files:
        print(f"registry already migrated: {manager.friendly_path(plan.index_path)}")
        return 0
    else:
        print(f"resumed migration from {manager.friendly_path(plan.index_path)}")
    for legacy in plan.legacy_files:
        print(f"  removed {manager.friendly_path(legacy.path)}")
    print("migration complete")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    """Remove one project's registry entry. The real project is never touched."""
    registry, registry_display = manager.resolve_registry(args.registry)
    entry = registry / args.name

    if not entry.is_dir() or entry.parent != registry:
        print(f"rm: no project '{args.name}' in {registry_display}", file=sys.stderr)
        return 1

    children = sorted(entry.iterdir(), key=lambda p: p.name.lower())
    # Everything a registry entry normally holds is a symlink. Anything else is
    # real data that exists nowhere else, so it is never removed casually — and
    # a real subdirectory is never removed at all.
    real_dirs = [c for c in children if c.is_dir() and not c.is_symlink()]
    if real_dirs:
        names = ", ".join(c.name for c in real_dirs)
        print(f"rm: {args.name} holds real directories ({names}); "
              f"remove them by hand first", file=sys.stderr)
        return 1
    real_files = [c for c in children if not c.is_symlink()]
    if real_files and not args.force:
        names = ", ".join(c.name for c in real_files)
        print(f"rm: {args.name} also holds {names}; "
              f"use --force to delete it with the entry", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] would remove {entry}/")
        for child in children:
            print(f"[dry-run]   {child.name}")
        return 0

    for child in children:
        child.unlink()
    entry.rmdir()
    eventlog.record(
        eventlog.make_event("pmgr", "rm", project=args.name),
        registry=registry,
    )
    print(f"removed project '{args.name}' from {registry_display}")
    for child in real_files:
        print(f"  deleted {child.name}")
    return 0


def _set_dirs_project(
    projects: list[manager.Project],
    requested: str | None,
) -> manager.Project:
    """Select an explicit project or the unique registry match for ``$PWD``."""
    if requested is not None:
        project = next((item for item in projects if item.name == requested), None)
        if project is None:
            raise ValueError(f"project not found in registry: {requested}")
        if project.warning:
            raise ValueError(f"{requested}: {project.warning}")
        return project

    pwd = Path(os.environ.get("PWD", str(Path.cwd()))).expanduser()
    root = manager.project_root_for(pwd)
    matches = [
        project
        for project in projects
        if not project.warning and manager.real_project_path(project) == root
    ]
    if len(matches) == 1:
        return matches[0]

    names = ", ".join(project.name for project in projects) or "(none)"
    if not matches:
        raise ValueError(
            f"no registered project matches {manager.friendly_path(root)}; "
            f"registered projects: {names}"
        )
    raise ValueError(
        f"multiple registered projects match {manager.friendly_path(root)}: "
        + ", ".join(project.name for project in matches)
    )


def _set_dirs_input(args: argparse.Namespace) -> list[Path]:
    """Read and resolve the proposed stack without consulting the index."""
    if args.stdin and args.directories:
        raise ValueError("--stdin cannot be combined with directory arguments")
    raw = sys.stdin.read().splitlines() if args.stdin else args.directories
    raw = [entry for entry in raw if entry]
    if not raw:
        raise ValueError("at least one directory is required")
    return manager.unique_resolved_directories(raw, Path.cwd())


def _set_dirs_keep_missing(
    args: argparse.Namespace, plan: manager.RegistryDirectoriesPlan
) -> bool | None:
    """Resolve subtraction policy; ``None`` means the user cancelled."""
    if not plan.missing:
        return False

    can_prompt = sys.stdin.isatty() and sys.stdout.isatty() and not args.stdin
    choice = args.missing
    if choice is None and not can_prompt:
        choice = "keep"
    if choice is not None:
        if not can_prompt:
            action = "keeping" if choice == "keep" else "removing"
            print(
                f"set-dirs: {action} {len(plan.missing)} existing "
                f"{'directory' if len(plan.missing) == 1 else 'directories'} "
                "absent from input",
                file=sys.stderr,
            )
        return choice == "keep"

    print("Existing private directories absent from the live stack:")
    for path in plan.missing:
        print(f"  - {manager.format_directory_path(path)}")
    while True:
        try:
            answer = menu.prompt_text("[r]emove, [k]eep, [c]ancel").lower()
        except menu.ContextCancelled:
            return None
        if answer in {"r", "remove"}:
            return False
        if answer in {"k", "keep", ""}:
            return True
        if answer in {"c", "cancel"}:
            return None


def cmd_set_dirs(args: argparse.Namespace) -> int:
    """Write a supplied directory stack into one registry-index section."""
    try:
        live_paths = _set_dirs_input(args)
    except ValueError as exc:
        print(f"set-dirs: {exc}", file=sys.stderr)
        return 1

    registry, _ = manager.resolve_registry(args.registry)
    if not registry.is_dir():
        print(f"set-dirs: project directory not found: {registry}", file=sys.stderr)
        return 1
    projects = manager.discover_projects(registry)
    try:
        project = _set_dirs_project(projects, args.project)
        plan = manager.plan_registry_directories_update(project, live_paths)
    except (ValueError, manager.RegistryIndexError) as exc:
        print(f"set-dirs: {exc}", file=sys.stderr)
        return 1

    keep_missing = _set_dirs_keep_missing(args, plan)
    if keep_missing is None:
        print("set-dirs: cancelled", file=sys.stderr)
        return 1

    try:
        revised, section = manager.render_registry_directories_update(
            plan, keep_missing=keep_missing
        )
    except manager.RegistryIndexError as exc:
        print(f"set-dirs: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"[dry-run] {project.name} in "
            f"{manager.friendly_path(plan.index_path)}:"
        )
        print(section, end="" if section.endswith("\n") else "\n")
        if revised == plan.previous_index_text:
            print("[dry-run] no change")
        return 0

    try:
        changed = manager.apply_registry_directories_update(
            plan, keep_missing=keep_missing
        )
    except manager.RegistryIndexError as exc:
        print(f"set-dirs: {exc}", file=sys.stderr)
        return 1
    action = "updated" if changed else "unchanged"
    if changed:
        final_paths = manager.proposed_registry_directories(
            plan, keep_missing=keep_missing
        )
        eventlog.record(
            eventlog.make_event(
                "pmgr",
                "set-dirs",
                project=project.name,
                detail={
                    "directories": [
                        manager.format_directory_path(path) for path in final_paths
                    ]
                },
            ),
            registry=registry,
        )
    print(
        f"{project.name}: directories {action} in "
        f"{manager.friendly_path(plan.index_path)}"
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report registry problems. Read-only; exits 2 when it finds any."""
    registry, registry_display = manager.resolve_registry(args.registry)
    if not registry.is_dir():
        print(f"project directory not found: {registry}", file=sys.stderr)
        return 1

    problems: list[str] = []
    notes: list[str] = []
    projects = manager.discover_projects(registry)

    for record in manager.summarize_projects(registry, include_all=True):
        if "warning" in record:
            problems.append(f"{record['project']}: {record['warning']}")
        elif record["file"] is None:
            notes.append(f"{record['project']}: no task file")

    for child in sorted(registry.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == manager.LOG_DIRECTORY_NAME:
            continue
        project = manager.read_project_entry(child)
        if project is None:
            notes.append(f"{child.name}: not a project entry, ignored")
            continue
        if project.warning:
            continue        # its broken link is already the story
        # A dangling task-file link is invisible to discovery: the project reads
        # as "no task file" when the truth is that its link is stale.
        for link in sorted(child.iterdir(), key=lambda p: p.name.lower()):
            if link.is_symlink() and not link.exists():
                problems.append(
                    f"{child.name}: dangling link {link.name} -> "
                    f"{os.readlink(link)}"
                )

    problems.extend(manager.registry_index_problems(registry, projects))

    print(f"Registry: {registry_display}")
    for note in notes:
        print(f"  note: {note}")
    for problem in problems:
        print(f"  problem: {problem}")
    if not problems:
        print("  no problems found")
        return 0
    return 2


def _dedupe(paths: list[Path]) -> list[Path]:
    """Drop repeats, keeping the first occurrence and so the list's own order."""
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _stack_for_source(
    source: manager.DirectorySource,
    project: manager.Project,
) -> list[Path]:
    """Resolve and deduplicate one source, falling back to the project root."""
    root = manager.real_project_path(project)
    entries = manager.resolve_directories(source.entries or [], root)
    return _dedupe(entries) or [root]


class _CdprojSession:
    """Pick a project, resolve its directory stack, and write it to a file.

    The output file has exactly one meaning: the directory stack the calling
    shell should have afterwards, top entry first. Editing happens inside this
    session and never travels back through that channel, so ``misc/cdproj.func.sh``
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
        self.warnings: list[str] = []

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
        return _stack_for_source(source, project)

    def resolve_stack(
        self, project: manager.Project
    ) -> tuple[list[Path], str, list[str]]:
        """The stack, the label that produced it, and any warnings.

        The private list wins outright and sets the order; the project's own
        list is never merged in, only checked against. See "Which list wins" in
        ``docs/cdproj.md`` for why, and for the cases this covers.

        This is the only place a stack is resolved. The picker, ``cdproj
        PROJECT``, and the numbered fallback all come through here, so they
        cannot drift apart.
        """
        root = manager.real_project_path(project)
        sources = manager.directory_sources(project)
        private = next((s for s in sources if s.label == "private"), None)
        public = next((s for s in sources if s.label == "project"), None)

        winner = private or public
        if winner is None:
            # No ``* Directories`` anywhere: the project root is the whole stack.
            return [root], "project root", []

        directories = self.stack_for(winner, project)
        if private is None or public is None:
            return directories, winner.label, []

        # Both lists exist. Compare resolved paths, so "docs", "./docs", and the
        # absolute form are one entry rather than three.
        chosen = set(directories)
        missing = [
            path
            for path in _dedupe(manager.resolve_directories(public.entries or [], root))
            if path not in chosen
        ]
        warnings: list[str] = []
        if missing:
            warnings.append(
                f"{project.name}: in {public.path.name} but not "
                f"{private.path.name}: "
                + ", ".join(manager.friendly_path(path) for path in missing)
            )
        return directories, private.label, warnings

    def write_selection(self, project: manager.Project) -> tuple[str, str]:
        """Resolve one project's stack, write it, and bank its warnings."""
        try:
            directories, label, warnings = self.resolve_stack(project)
        except manager.RegistryIndexError as exc:
            self.error = f"cdproj: {exc}"
            return "Could not resolve the directory stack", "index error"
        self.warnings.extend(warnings)
        message = self.write_stack(directories)
        if self.status == 0:
            eventlog.record(
                eventlog.make_event(
                    "pmgr",
                    "cdproj",
                    project=project.name,
                    detail={
                        "directories": [
                            manager.friendly_path(path) for path in directories
                        ]
                    },
                ),
                registry=project.path.parent,
            )
        return message, label

    def report(self) -> None:
        """Warnings and errors reach stderr only once any picker has exited.

        The ``--out`` file carries directories and nothing else, and the inline
        session erases itself on the way out, so stderr is the one channel that
        survives to the shell that called ``cdproj``.
        """
        for warning in self.warnings:
            print(warning, file=sys.stderr)
        if self.error:
            print(self.error, file=sys.stderr)

    # -- views -----------------------------------------------------------

    def run(self) -> int:
        session = menu.InlineMenuSession(
            self.project_view(),
            action_keys=["e"],
            final_message="No project selected",
        )
        session.run()
        self.report()
        return self.status

    def project_view(self) -> menu.MenuView:
        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            if result.index is None or not 0 <= result.index < len(self.projects):
                return
            project = self.projects[result.index]
            if result.action == "select":
                self.select_stack(session, project)
            elif result.action == "edit":
                session.push_view(self.edit_view(project))

        return _project_view(
            self.projects,
            self.display_path,
            handle,
            listing=CDPROJ,
            instruction=(
                "* = custom · ↑↓/jk · ↵ select · e edit · Esc/q cancel"
            ),
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
        """Write the highlighted project's stack and close the picker."""
        message, label = self.write_selection(project)
        session.pop_view(message=f"{project.name} ({label}): {message}")

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
            self.edit_source(session, project, candidates[result.index])

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
        self,
        session: menu.InlineMenuSession,
        project: manager.Project,
        candidate: manager.DirectorySource,
    ) -> None:
        """Open one directory list in the user's editor, then return to the picker.

        A missing private section is added to the migrated index on demand. The
        project's task file is never written here, so a missing shared section
        is only reported.
        """
        path = candidate.path
        line_num = candidate.line_num
        if candidate.label == "private":
            try:
                path, line_num = manager.ensure_registry_project_directories(project)
            except manager.RegistryIndexError as exc:
                session.set_transient_message(str(exc))
                return
        argv = core.editor_argv(path, line_num)
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
                message=f"Edited {manager.friendly_path(path)}{hint}"
            )

        session.suspend(run_editor, on_done=done)


def _cdproj_fallback(session: _CdprojSession) -> int:
    """Numbered-menu path for pipes and terminals without prompt_toolkit."""
    _print_project_dashboard(session.projects, session.display_path, CDPROJ)
    try:
        choice = menu.prompt_text(
            "* = custom · number, Esc/q=cancel"
        ).strip().lower()
    except menu.ContextCancelled:
        return 1
    if not choice.isdigit() or not 1 <= int(choice) <= len(session.projects):
        return 1

    session.write_selection(session.projects[int(choice) - 1])
    session.report()
    return session.status


def cmd_cdproj(args: argparse.Namespace) -> int:
    """Select a project and write its directory stack to ``--out``."""
    workspace, display_path = manager.resolve_registry(args.registry)
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1

    projects = manager.discover_projects(workspace)
    try:
        manager.require_registry_index(workspace, projects)
    except manager.RegistryIndexError as exc:
        for message in exc.messages:
            print(f"cdproj: {message}", file=sys.stderr)
        return 1
    if not projects:
        print("No projects discovered.", file=sys.stderr)
        return 1

    session = _CdprojSession(
        display_path,
        projects,
        Path(args.out).expanduser().resolve(),
    )
    if getattr(args, "project", None) is not None:
        project = next((p for p in projects if p.name == args.project), None)
        if project is None:
            print(f"project not found in registry: {args.project}", file=sys.stderr)
            return 1
        session.write_selection(project)
        session.report()
        return session.status

    if menu.interactive_select_available():
        return session.run()
    return _cdproj_fallback(session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="projmgr — the project-layer command for the ortask suite.",
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
    p_add = sub.add_parser(
        "add",
        help="register one project (creates a subdirectory of symlinks)",
    )
    p_add.add_argument(
        "path",
        nargs="?",
        default=None,
        help="project directory to add; default: the project you are in",
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

    p_cdproj = sub.add_parser(
        "cdproj",
        help="select a project and write its directories to a file",
    )
    p_cdproj.add_argument(
        "--out",
        required=True,
        help="output file to write selected paths to",
    )
    # SUPPRESS default so this subparser does not clobber a global override.
    p_cdproj.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry directory to select from")
    p_cdproj.add_argument(
        "project",
        nargs="?",
        default=None,
        help="optional project name to resolve immediately without TUI",
    )

    p_doctor = sub.add_parser(
        "doctor",
        help="report broken, ambiguous, or unreadable registry entries",
    )
    p_doctor.add_argument("--registry", default=argparse.SUPPRESS,
                          help="registry directory to check")

    sub.add_parser("help", help="show this help message")

    p_init = sub.add_parser(
        "init",
        help="record the registry directory in ortask.ini",
    )
    # SUPPRESS default so this subparser does not clobber a global override.
    p_init.add_argument("--registry", default=argparse.SUPPRESS,
                        help="registry directory to record")
    p_init.add_argument("--force", action="store_true",
                        help="use the default even if ortask.ini already has one")
    p_init.add_argument("--dry-run", action="store_true",
                        help="show what would be written and removed")

    p_list = sub.add_parser("list", help="list projects and top-level tasks")
    p_list.add_argument(
        "--all",
        action="store_true",
        help="include completed (DONE) tasks as well as open ones",
    )
    p_list.add_argument(
        "--format",
        choices=["plain", "json", "names"],
        default="plain",
        help="output format: plain, json, or bare project names for completion",
    )

    p_log = sub.add_parser("log", help="read the append-only activity log")
    p_log.add_argument("--since", metavar="WHEN", default=None)
    p_log.add_argument("--until", metavar="WHEN", default=None)
    p_log.add_argument("--project", default=None,
                       help="show events for one registry project")
    p_log.add_argument("--limit", type=int, default=None,
                       help="show only the newest N matching events")
    p_log.add_argument("--day-start", default="00:00", metavar="HH:MM")
    p_log.add_argument("--format", choices=["plain", "json", "org"], default="plain")
    p_log.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry whose log should be read")

    p_mig = sub.add_parser(
        "migrate",
        help="move legacy private directory files into projects.org",
    )
    p_mig.add_argument("--registry", default=argparse.SUPPRESS,
                       help="registry directory to migrate")
    p_mig.add_argument("--dry-run", action="store_true",
                       help="show the proposed index and removals")

    p_projadd = sub.add_parser(
        "projadd",
        help="deprecated alias for add",
    )
    p_projadd.add_argument("path", nargs="?", default=None)
    p_projadd.add_argument("--name", default=None)
    p_projadd.add_argument("--file", default=None)
    p_projadd.add_argument("--registry", default=argparse.SUPPRESS)
    p_projadd.add_argument("--force", action="store_true")
    p_projadd.add_argument("--dry-run", action="store_true")

    p_rm = sub.add_parser(
        "rm",
        help="remove one project's registry entry (never the project itself)",
    )
    p_rm.add_argument("name", help="registry entry to remove")
    p_rm.add_argument("--registry", default=argparse.SUPPRESS,
                      help="registry directory to remove from")
    p_rm.add_argument("--force", action="store_true",
                      help="also delete regular files held in the entry")
    p_rm.add_argument("--dry-run", action="store_true",
                      help="show what would be removed without changing anything")

    p_set_dirs = sub.add_parser(
        "set-dirs",
        help="save a directory stack in one projects.org section",
    )
    p_set_dirs.add_argument(
        "directories",
        nargs="*",
        help="directory stack in top-first order",
    )
    p_set_dirs.add_argument(
        "--project",
        default=None,
        help="registry project name; default: infer from PWD",
    )
    p_set_dirs.add_argument(
        "--stdin",
        action="store_true",
        help="read one directory per line instead of positional arguments",
    )
    p_set_dirs.add_argument(
        "--missing",
        choices=["keep", "remove"],
        default=None,
        help="how to handle existing entries absent from the supplied stack",
    )
    p_set_dirs.add_argument("--registry", default=argparse.SUPPRESS,
                            help="registry directory containing projects.org")
    p_set_dirs.add_argument("--dry-run", action="store_true",
                            help="show the replacement section without writing")

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
        "add": cmd_add,
        "cdproj": cmd_cdproj,
        "doctor": cmd_doctor,
        "init": cmd_init,
        "list": cmd_list,
        "log": cmd_log,
        "migrate": cmd_migrate,
        "projadd": cmd_add,         # deprecated alias
        "rm": cmd_rm,
        "set-dirs": cmd_set_dirs,
    }

    handler = dispatch.get(cmd)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
