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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.layout import FormattedTextControl, HSplit, VSplit, Window
    from prompt_toolkit.widgets import TextArea
except ImportError:  # pragma: no cover - optional interactive dependency
    Document = None
    Condition = None
    FormattedText = None
    FormattedTextControl = None
    HSplit = None
    VSplit = None
    Window = None
    TextArea = None

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import orglib  # noqa: E402 — after the sys.path insert above
from ortasklib import (
    core,
    log as eventlog,
    manager,
    menu,
    taskui,
    viewstate,
    viewui,
)


# ---------------------------------------------------------------------------
# The one project list
#
# Every project surface — ``-i``, ``cdproj``, and both numbered fallbacks — builds
# rows from the same projects and anchors the highlight the same way. What
# differs is what Enter does and, because the two are easy to confuse otherwise,
# how each names itself and what its third column says.
# ---------------------------------------------------------------------------

PROJECT_SORT_ACTION = menu.MenuAction(
    "sort", "s", "Cycle project sorting: Priority, Alphabetical, and Modified"
)
PROJECT_FILTER_LABELS = {
    manager.PROJECT_FILTER_ALL: "",
    manager.PROJECT_FILTER_OPEN: "open only",
}
PROJECT_SORT_LABELS = {
    manager.PROJECT_SORT_PRIORITY: "Priority",
    manager.PROJECT_SORT_ALPHABETICAL: "Alphabetical",
    manager.PROJECT_SORT_MODIFIED: "Modified",
}
#: What each order's two directions actually mean. A Boolean "reversed" tells a
#: reader nothing about what will be at the top; these words do.
PROJECT_SORT_DIRECTIONS = {
    manager.PROJECT_SORT_PRIORITY: ("highest first", "lowest first"),
    manager.PROJECT_SORT_ALPHABETICAL: ("A-Z", "Z-A"),
    manager.PROJECT_SORT_MODIFIED: ("newest first", "oldest first"),
}
PROJECT_VIEW_AXES = viewstate.ViewAxes(
    manager.PROJECT_FILTERS,
    manager.PROJECT_SORT_MODES,
    PROJECT_FILTER_LABELS,
    PROJECT_SORT_LABELS,
    directions=PROJECT_SORT_DIRECTIONS,
    sort_noun=" sort",
)

PROJECT_FILTER_ACTION = menu.MenuAction(
    "filter", "C-t", "Show all projects or only those with open tasks"
)
PROJECT_MENU_ACTIONS = {
    # The project list is flat, so Right has no subtree to open and can simply
    # act as Enter, the way it does on a leaf task in the task list.
    "right": menu.MenuAction(
        "select", "Right", "Open the highlighted project"
    ),
    "m": menu.MenuAction(
        "metadata", "m", "Edit the highlighted project's metadata"
    ),
    "s": PROJECT_SORT_ACTION,
    "c-t": PROJECT_FILTER_ACTION,
    "v": menu.MenuAction("view", "v", "Open the view options screen"),
    "s-up": menu.MenuAction(
        "priority_up", "Shift+↑", "Raise the highlighted project's priority"
    ),
    "s-down": menu.MenuAction(
        "priority_down", "Shift+↓", "Lower the highlighted project's priority"
    ),
    "c-s": menu.MenuAction(
        "save", "C-s", "Save buffered project metadata to projects.org"
    ),
    "c-_": menu.MenuAction(
        "undo", "C-/", "Undo the most recent buffered project edit"
    ),
    "c-r": menu.MenuAction(
        "redo", "C-r", "Redo the most recently undone project edit"
    ),
}


def _project_menu_instruction(
    view: "viewstate.ViewState | str", *, dirty: bool = False
) -> str:
    if isinstance(view, str):
        view = PROJECT_VIEW_AXES.initial(sort=view)
    prefix = "FILE MODIFIED · " if dirty else ""
    return (
        f"{prefix}{view.badge()} · ↑↓/jk · S-↑/↓ priority · ↵/→ open · s sort · "
        "C-t filter · v view · m metadata · C-s save · C-/ undo · C-r redo · "
        "C-g help · Esc/b/q exit"
    )


PROJECT_MENU_INSTRUCTION = _project_menu_instruction(manager.PROJECT_SORT_PRIORITY)


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


def _project_summary(
    project: manager.Project,
    metadata: manager.ProjectMetadata | None = None,
) -> str:
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
    if metadata is not None and metadata.warning:
        parts.append(f"metadata: {metadata.warning}")
    if metadata is not None:
        mismatch = _project_task_file_mismatch(project, metadata)
        if mismatch:
            parts.append(mismatch)
    return "  ·  ".join(parts)


def _snapshot(
    project: manager.Project,
    snapshots: dict[str, manager.ProjectSnapshot] | None,
) -> manager.ProjectSnapshot:
    """This render's reading of one project's task file."""
    if snapshots is not None and project.name in snapshots:
        return snapshots[project.name]
    return manager.read_project_snapshot(project)


def _project_tasks(
    project: manager.Project,
    snapshot: manager.ProjectSnapshot | None = None,
) -> str:
    """How much open work a project has — the navigator's reason to exist.

    Counts the same top-level tasks ``projmgr.py list`` shows, so the number
    here and the rows there agree. The count comes from the render's snapshot
    rather than a fresh parse, so a row cannot disagree with the filter that
    kept it.
    """
    if project.warning:
        return f"({project.warning})"
    if manager.canonical_org_file(project) is None:
        return "(no task file)"
    reading = snapshot if snapshot is not None else manager.read_project_snapshot(project)
    if not reading.readable:
        return "(unreadable)"
    if not reading.total_tasks:
        return "(no tasks)"
    return f"{reading.open_tasks} open"


#: Shown when a project's task file cannot be read, so its age is unknowable.
UNKNOWN_AGE = "-"

#: Padding for the project name in the navigator and the cdproj picker, which
#: share it so a project sits in the same place in both. A longer name still
#: pushes the row right rather than being cut.
PROJECT_NAME_WIDTH = 14

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_YEAR = 365 * _DAY

#: Largest unit that still reads as a whole number, from the last change.
_AGE_UNITS = (
    (_MINUTE, 1, ""),
    (_HOUR, _MINUTE, "m"),
    (_DAY, _HOUR, "h"),
    (_WEEK, _DAY, "d"),
    (_YEAR, _WEEK, "w"),
)


def _project_modified(
    snapshot: manager.ProjectSnapshot | None = None,
    *,
    now_ns: int | None = None,
) -> str:
    """How long since the task file changed — the Modified sort, made visible.

    Sorting by a key nobody can see is a guess, so this appears in every order
    rather than only in the one that uses it. Relative age rather than a
    timestamp: the question a reader has here is how stale a project is, not
    exactly when it was touched.
    """
    if snapshot is None or snapshot.modified_ns is None:
        return UNKNOWN_AGE
    now = time.time_ns() if now_ns is None else now_ns
    seconds = max((now - snapshot.modified_ns) // 1_000_000_000, 0)
    for limit, size, unit in _AGE_UNITS:
        if seconds < limit:
            return f"{seconds // size}{unit}" if unit else "now"
    return f"{seconds // _YEAR}y"


def _project_priority(metadata: manager.ProjectMetadata) -> str:
    """Compact priority text for a navigator row; blank means deliberately unset."""
    return metadata.priority.upper() if metadata.priority else " "


def _project_task_file_mismatch(
    project: manager.Project, metadata: manager.ProjectMetadata
) -> str | None:
    """Avoid deriving a second warning from an already-ambiguous mirror."""
    if metadata.warning and "TASK_FILE" in metadata.warning:
        return None
    return manager.task_file_mirror_mismatch(project, metadata.task_file)


def _project_metadata_note(
    project: manager.Project, metadata: manager.ProjectMetadata
) -> str:
    notes: list[str] = []
    if metadata.warning:
        notes.append(f"metadata: {metadata.warning}")
    if _project_task_file_mismatch(project, metadata):
        notes.append("TASK_FILE differs")
    return f"({' · '.join(notes)})" if notes else ""


def _navigator_row(
    project: manager.Project,
    metadata: manager.ProjectMetadata,
    snapshot: manager.ProjectSnapshot | None = None,
    *,
    now_ns: int | None = None,
) -> str:
    """Priority, name, workload, age, and description for one interactive row."""
    # Joined rather than concatenated so an overflowing column — a broken
    # project's warning lands in the workload one — still cannot run into the
    # next field.
    prefix = "  ".join(
        (
            f"[{_project_priority(metadata)}] {project.name:<{PROJECT_NAME_WIDTH}}",
            f"{_project_tasks(project, snapshot):<12}",
            f"{_project_modified(snapshot, now_ns=now_ns):<4}",
        )
    )
    suffix = "  ".join(
        part
        for part in (
            metadata.description or "",
            _project_metadata_note(project, metadata),
        )
        if part
    )
    return f"{prefix}  {suffix}".rstrip()


def _navigator_dashboard_detail(
    project: manager.Project,
    metadata: manager.ProjectMetadata,
    snapshot: manager.ProjectSnapshot | None = None,
    *,
    now_ns: int | None = None,
) -> str:
    """Fallback rows carry context that the highlight summary normally supplies."""
    # Age is omitted rather than shown as unknown: a project whose file cannot
    # be read is already saying so in the workload column.
    facts = [_project_tasks(project, snapshot)]
    age = _project_modified(snapshot, now_ns=now_ns)
    if age != UNKNOWN_AGE:
        facts.append(age)
    parts = [f"{_project_location(project)}  ({', '.join(facts)})"]
    if metadata.description:
        parts.append(metadata.description)
    note = _project_metadata_note(project, metadata)
    if note:
        parts.append(note)
    return "  ".join(parts)


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
    metadata_rows: bool = False

    def dashboard(self) -> Callable[[manager.Project], str]:
        return self.dashboard_detail or self.detail


NAVIGATOR = _ProjectListing(
    "Project navigator",
    "PROJ",
    "Location",
    _project_tasks,
    dashboard_detail=_project_location_and_tasks,
    metadata_rows=True,
)
CDPROJ = _ProjectListing(
    "Change directory", "CD", "Directories", _project_stack
)


def _project_rows(
    projects: list[manager.Project],
    listing: _ProjectListing,
    metadata: dict[str, manager.ProjectMetadata] | None = None,
    snapshots: dict[str, manager.ProjectSnapshot] | None = None,
) -> list[menu.MenuRow]:
    metadata = metadata or {}
    return [
        menu.MenuRow(
            index + 1,
            listing.label,
            (
                _navigator_row(
                    project,
                    metadata.get(project.name, manager.ProjectMetadata()),
                    _snapshot(project, snapshots),
                )
                if listing.metadata_rows
                else f"{project.name:<{PROJECT_NAME_WIDTH}}  {listing.detail(project)}"
            ),
        )
        for index, project in enumerate(projects)
    ]


def _print_project_dashboard(
    projects: list[manager.Project],
    display_path: str,
    listing: _ProjectListing,
    metadata: dict[str, manager.ProjectMetadata] | None = None,
    snapshots: dict[str, manager.ProjectSnapshot] | None = None,
) -> None:
    metadata = metadata or {}
    if listing.metadata_rows:
        rows = []
        for index, project in enumerate(projects):
            project_metadata = metadata.get(project.name, manager.ProjectMetadata())
            rows.append(
                menu.ProjectRow(
                    index + 1,
                    f"[{_project_priority(project_metadata)}] {project.name}",
                    _navigator_dashboard_detail(
                        project,
                        project_metadata,
                        _snapshot(project, snapshots),
                    ),
                )
            )
    else:
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
    metadata: dict[str, manager.ProjectMetadata] | None = None,
    snapshots: dict[str, manager.ProjectSnapshot] | None = None,
    selected_index: int = 0,
    on_resume=None,
) -> menu.MenuView:
    view = menu.MenuView(
        rows=_project_rows(projects, listing, metadata, snapshots),
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
        project = projects[index]
        project_metadata = (metadata or {}).get(project.name)
        return _project_summary(project, project_metadata)

    view.status_text = selected_summary
    return view


class _ProjectBrowser:
    """Project navigator: pick a project, work its tasks, come back."""

    def __init__(self, workspace: Path, display_path: str, include_done: bool) -> None:
        self.workspace = workspace
        self.display_path = display_path
        self.include_done = include_done
        #: What the project list shows and in what order.
        self.view_state = PROJECT_VIEW_AXES.initial()
        self.session: menu.InlineMenuSession | None = None
        self.task_controller: taskui.InteractiveTaskController | None = None
        self.index_buffer: taskui.OrgBuffer | None = None
        self.index_error: str | None = None
        self.index_conflicts: tuple[manager.ProjectIndexMergeConflict, ...] = ()
        self._reconciliation_key: (
            tuple[taskui.ExternalFileChange, str] | None
        ) = None
        self._rendered_project_view: menu.MenuView | None = None
        self._rendered_projects: list[manager.Project] = []
        try:
            projects = manager.discover_projects(self.workspace)
            index_path, _ = manager.require_registry_index(self.workspace, projects)
            self.index_buffer = taskui.OrgBuffer(
                index_path,
                registry=self.workspace,
            )
        except (OSError, manager.RegistryIndexError) as exc:
            self.index_error = str(exc)

    @property
    def sort_mode(self) -> str:
        """The active sort position, under the name the navigator already uses."""
        return self.view_state.sort

    @sort_mode.setter
    def sort_mode(self, mode: str) -> None:
        self.view_state = self.view_state.with_sort(mode)

    def _save_index_from_screen(self, session: menu.InlineMenuSession) -> None:
        """What ``C-s`` means everywhere in the navigator, screens included.

        Unlike the list's own save this leaves the current view in place: the
        key wrote the file, which is no reason to close the screen the user is
        working in.
        """
        if self.index_buffer is None:
            session.set_transient_message(
                self.index_error or "projects.org is not available for editing"
            )
            return
        saved, message = self._save_index_changes()
        if not saved:
            if self.index_conflicts:
                session.push_view(self._conflict_view(parent_is_save=False))
            else:
                session.set_transient_message(message)
            return
        session.set_message(message)

    def _ordered(
        self,
        projects: list[manager.Project],
        metadata: dict[str, manager.ProjectMetadata],
        snapshots: dict[str, manager.ProjectSnapshot],
    ) -> list[manager.Project]:
        """Filter, then order — one place and one snapshot, so a render agrees.

        Every question a render asks about a project's task file is answered
        from ``snapshots``, which read it once.
        """
        kept = manager.filter_projects(
            projects, self.view_state.filter, snapshots=snapshots
        )
        return manager.sort_projects(
            kept,
            metadata,
            self.view_state.sort,
            reverse=self.view_state.reverse,
            snapshots=snapshots,
        )

    def _metadata(
        self, projects: list[manager.Project]
    ) -> dict[str, manager.ProjectMetadata]:
        if self.index_buffer is not None:
            return manager.project_metadata_from_text(
                self.index_buffer.read(), projects
            )
        return manager.read_project_metadata(self.workspace, projects)

    def _buffer_status(self) -> str:
        if self.index_buffer is None:
            return ""
        if self.index_conflicts:
            names = tuple(
                dict.fromkeys(
                    conflict.project
                    for conflict in self.index_conflicts
                    if conflict.project
                )
            )
            target = ", ".join(names) if names else "projects.org structure"
            count = self.index_buffer.undo_count
            noun = "edit" if count == 1 else "edits"
            return f"MERGE CONFLICT: {target} · FILE MODIFIED: {count} {noun}"
        return taskui.buffer_status(self.index_buffer)

    def _index_exit_concern(self) -> menu.ExitConcern | None:
        """Expose projects.org and active metadata drafts to the exit gateway."""
        if self.index_buffer is None:
            return None
        workspaces: list[menu.WorkspaceView] = []
        if self.session is not None:
            workspaces = [
                view
                for view in self.session.views
                if isinstance(view, menu.WorkspaceView)
                and view.exit_owner is self
                and view.is_dirty is not None
                and view.is_dirty()
            ]
        dirty = self.index_buffer.dirty or bool(workspaces)
        preparation: dict[str, str | None] = {"message": None}

        def prepare() -> menu.ExitActionResult:
            for workspace in workspaces:
                assert workspace.prepare_exit is not None
                result = workspace.prepare_exit()
                if not result.success:
                    return result
            ready, message = self._reconcile_index(force=True)
            if not ready:
                return menu.ExitActionResult(
                    False,
                    message or self._conflict_message(),
                )
            preparation["message"] = message
            return menu.ExitActionResult(True)

        def save() -> menu.ExitActionResult:
            assert self.index_buffer is not None
            changed = self.index_buffer.dirty
            try:
                self.index_buffer.save()
            except taskui.BufferChangedError as exc:
                return menu.ExitActionResult(False, str(exc))
            for workspace in workspaces:
                assert workspace.mark_exit_saved is not None
                workspace.mark_exit_saved()
            message = (
                "Saved changes to projects.org"
                if changed
                else "Saved projects.org (unchanged)"
            )
            if preparation["message"]:
                message += f" · {preparation['message']}"
            return menu.ExitActionResult(True, message)

        def discard() -> menu.ExitActionResult:
            assert self.index_buffer is not None
            for workspace in workspaces:
                assert workspace.discard_exit is not None
                workspace.discard_exit()
            self.index_buffer.discard()
            self.index_conflicts = ()
            self._reconciliation_key = None
            return menu.ExitActionResult(
                True,
                "Discarded changes to projects.org",
            )

        return menu.ExitConcern(
            label=manager.friendly_path(self.index_buffer.path.resolve()),
            dirty=dirty,
            prepare=prepare if dirty else None,
            save=save if dirty else None,
            discard=discard if dirty else None,
        )

    def _exit_concerns(self) -> tuple[menu.ExitConcern, ...]:
        """Return dirty sources in deterministic projects-then-tasks order."""
        concerns: list[menu.ExitConcern] = []
        index = self._index_exit_concern()
        if index is not None:
            concerns.append(index)
        if self.task_controller is not None:
            concerns.append(self.task_controller.exit_concern())
        return tuple(concerns)

    def _conflict_message(self) -> str:
        names = tuple(
            dict.fromkeys(
                conflict.project
                for conflict in self.index_conflicts
                if conflict.project
            )
        )
        if names:
            return f"Merge conflict: {', '.join(names)}"
        if self.index_conflicts:
            return f"Merge conflict: {self.index_conflicts[0].detail}"
        if self.index_buffer is not None and self.index_buffer.external_change:
            return self.index_buffer.external_change.summary
        return "projects.org cannot be saved"

    # Composition boundary (t0038.3): this browser coordinates observation,
    # project-index policy, and conflict views. Parsing and patch algorithms do
    # not belong here; add them to the lower layer and consume their results.
    def _reconcile_index(self, *, force: bool) -> tuple[bool, str | None]:
        """Reconcile one observed index revision entirely in memory."""
        if self.index_buffer is None:
            return False, self.index_error or "projects.org is unavailable"
        previous_saved = self.index_buffer.saved_text
        change = self.index_buffer.check_external_change(force=force)
        if change is None:
            self.index_conflicts = ()
            self._reconciliation_key = None
            message = (
                "Reloaded external projects.org changes"
                if self.index_buffer.saved_text != previous_saved
                else None
            )
            return True, message
        if change.kind != "changed" or change.text is None:
            self.index_conflicts = ()
            self._reconciliation_key = None
            return False, change.summary

        key = (change, self.index_buffer.read())
        if not force and key == self._reconciliation_key:
            return False, None
        plan = manager.plan_project_index_merge(
            self.index_buffer.saved_text,
            self.index_buffer.read(),
            change.text,
        )
        self._reconciliation_key = key
        if not plan.can_merge:
            self.index_conflicts = plan.conflicts
            return False, self._conflict_message()

        assert plan.merged_text is not None
        replayed = ", ".join(plan.replayed_projects) or "project edits"
        self.index_buffer.rebase_external_change(
            change,
            plan.merged_text,
            description=f"Reapply {replayed} after external changes",
        )
        self.index_conflicts = ()
        self._reconciliation_key = None
        absorbed = ", ".join(plan.absorbed_projects)
        message = (
            f"Merged external changes: {absorbed}"
            if absorbed
            else "Merged external projects.org changes"
        )
        return True, message

    @staticmethod
    def _selected_project(
        projects: list[manager.Project], index: int | None
    ) -> manager.Project | None:
        if index is None or not 0 <= index < len(projects):
            return None
        return projects[index]

    def _replace_view(
        self,
        session: menu.InlineMenuSession,
        project: manager.Project | None,
        fallback_index: int,
    ) -> None:
        session.replace_view(
            self.view(project.name if project is not None else None, fallback_index)
        )

    def _save_index(
        self,
        session: menu.InlineMenuSession,
        project: manager.Project | None,
        fallback_index: int,
    ) -> bool:
        if self.index_buffer is None:
            session.set_transient_message(
                self.index_error or "projects.org is not available for editing"
            )
            return False
        saved, message = self._save_index_changes()
        if not saved:
            if self.index_conflicts:
                session.push_view(self._conflict_view(parent_is_save=False))
            else:
                session.set_transient_message(message)
            return False
        self._replace_view(session, project, fallback_index)
        session.set_outcome(message)
        return True

    def _save_index_changes(self) -> tuple[bool, str]:
        """Reconcile, then perform the final exact-preimage save."""
        assert self.index_buffer is not None
        ready, reconciliation = self._reconcile_index(force=True)
        if not ready:
            return False, reconciliation or self._conflict_message()
        changed = self.index_buffer.dirty
        try:
            self.index_buffer.save()
        except taskui.BufferChangedError as exc:
            return False, str(exc)
        message = (
            "Saved changes to projects.org"
            if changed
            else "Saved projects.org (unchanged)"
        )
        if reconciliation:
            message += f" · {reconciliation}"
        return True, message

    def _metadata_view(self, project: manager.Project) -> menu.WorkspaceView:
        """Build one compact editor over the browser's shared index buffer."""
        assert self.index_buffer is not None
        assert all(
            dependency is not None
            for dependency in (
                Document,
                Condition,
                FormattedText,
                FormattedTextControl,
                HSplit,
                VSplit,
                Window,
                TextArea,
            )
        )
        workspace: menu.WorkspaceView | None = None

        def field_is_editing(focus_index: int) -> bool:
            return (
                workspace is not None
                and workspace.editing_index == focus_index
            )

        def field_style(focus_index: int) -> str:
            if workspace is None or workspace.focused_index != focus_index:
                return ""
            return (
                "class:field.editing"
                if field_is_editing(focus_index)
                else "class:choice.focused"
            )

        source = self.index_buffer.read()
        metadata = manager.project_metadata_from_text(source, [project])[project.name]
        if metadata.warning:
            raise ValueError(metadata.warning)

        candidates = manager.directory_candidates_from_index(
            project, self.index_buffer.path, source
        )
        custom = candidates[0]
        inherited = next(
            (candidate for candidate in candidates[1:] if candidate.defines_stack),
            None,
        )
        root = manager.real_project_path(project)
        if custom.defines_stack:
            effective_entries = custom.entries or []
            effective_source = "custom"
        elif inherited is not None:
            effective_entries = inherited.entries or []
            effective_source = "project"
        else:
            effective_entries = [str(root)]
            effective_source = "project root"
        effective_directories = manager.unique_resolved_directories(
            effective_entries, root
        ) or [root]
        effective_state: dict[str, str | int] = {
            "count": len(effective_directories),
            "source": effective_source,
        }

        resolved_file = manager.canonical_org_file(project)
        resolved_display = (
            manager.friendly_path(resolved_file)
            if resolved_file is not None
            else "(no task file)"
        )
        description_area = TextArea(
            text=metadata.description or "",
            multiline=False,
            wrap_lines=False,
            height=1,
            dont_extend_height=True,
            read_only=Condition(lambda: not field_is_editing(1)),
        )
        description_area.window.always_hide_cursor = Condition(
            lambda: not field_is_editing(1)
        )
        description_area.window.style = lambda: field_style(1)
        description_area.buffer.cursor_position = len(description_area.text)
        directory_area = TextArea(
            text="\n".join(custom.entries or []),
            multiline=True,
            wrap_lines=False,
            scrollbar=True,
            height=3,
            dont_extend_height=True,
            read_only=Condition(lambda: not field_is_editing(2)),
        )
        directory_area.window.always_hide_cursor = Condition(
            lambda: not field_is_editing(2)
        )
        directory_area.window.style = lambda: field_style(2)
        directory_area.buffer.cursor_position = len(directory_area.text)
        draft: dict[str, str | None] = {
            "priority": metadata.priority,
            "task_file": metadata.task_file,
        }
        baseline: dict[str, object] = {
            "priority": draft["priority"],
            "description": metadata.description,
            "task_file": draft["task_file"],
            "directories": tuple(custom.entries or []),
        }
        def workspace_summary() -> str:
            parts = [
                "Effective directories: "
                f"{effective_state['count']} ({effective_state['source']})"
            ]
            mismatch = manager.task_file_mirror_mismatch(
                project, draft["task_file"]
            )
            if mismatch:
                parts.append(mismatch)
            return " · ".join(parts)

        def description_value() -> str | None:
            value = description_area.text.strip()
            return value or None

        def directory_values() -> tuple[str, ...]:
            return tuple(
                line.strip()
                for line in directory_area.text.splitlines()
                if line.strip()
            )

        def is_dirty() -> bool:
            return (
                draft["priority"] != baseline["priority"]
                or description_value() != baseline["description"]
                or draft["task_file"] != baseline["task_file"]
                or directory_values() != baseline["directories"]
            )

        def reset_control(control, text: str) -> None:
            control.buffer.reset(Document(text, cursor_position=len(text)))

        def discard_workspace_edits() -> None:
            draft["priority"] = baseline["priority"]
            draft["task_file"] = baseline["task_file"]
            reset_control(description_area, str(baseline["description"] or ""))
            reset_control(
                directory_area,
                "\n".join(str(entry) for entry in baseline["directories"]),
            )

        def readonly_line(label_text: str, value) -> object:
            def render():
                return FormattedText(
                    [
                        ("class:field.label", f"{label_text}: "),
                        ("", value()),
                    ]
                )

            return Window(
                FormattedTextControl(render),
                height=1,
                always_hide_cursor=True,
                wrap_lines=False,
            )

        def field_label(label_text: str) -> object:
            return Window(
                FormattedTextControl(
                    FormattedText([("class:field.label", label_text)])
                ),
                height=1,
                always_hide_cursor=True,
            )

        def compact_choice(label_text: str, focus_index: int) -> object:
            def render():
                value = draft["priority"] or "none"
                text = f" {label_text} [{value}] "
                focused = (
                    workspace is not None
                    and workspace.focused_index == focus_index
                )
                editing = focused and field_is_editing(focus_index)
                fragments = (
                    [
                        (
                            "class:field.editing"
                            if editing
                            else "class:choice.focused",
                            text,
                        )
                    ]
                    if focused
                    else [
                        ("class:field.label", f" {label_text} "),
                        ("", f"[{value}] "),
                    ]
                )
                return FormattedText([("[SetCursorPosition]", ""), *fragments])

            return Window(
                FormattedTextControl(render, focusable=True, show_cursor=False),
                height=1,
                width=22,
                dont_extend_width=True,
                always_hide_cursor=True,
            )

        def compact_button(label_text: str, focus_index: int, width: int) -> object:
            def render():
                focused = (
                    workspace is not None
                    and workspace.focused_index == focus_index
                )
                style = "class:choice.focused" if focused else "class:field.label"
                return FormattedText(
                    [
                        ("[SetCursorPosition]", ""),
                        (style, f"[ {label_text} ]"),
                    ]
                )

            return Window(
                FormattedTextControl(render, focusable=True, show_cursor=False),
                height=1,
                width=width,
                dont_extend_width=True,
                always_hide_cursor=True,
            )

        priority_control = compact_choice("Priority", 0)
        mirror_control = compact_button("Refresh TASK_FILE", 3, 23)
        editor_control = compact_button("Open projects.org in editor", 4, 32)
        container = HSplit(
            [
                VSplit(
                    [
                        readonly_line("Project", lambda: project.name),
                        priority_control,
                    ],
                    height=1,
                ),
                field_label("Description"),
                description_area,
                readonly_line("Resolved task file", lambda: resolved_display),
                readonly_line(
                    "Recorded TASK_FILE",
                    lambda: draft["task_file"] or "(unset)",
                ),
                mirror_control,
                field_label("Custom directories (one path per line)"),
                directory_area,
                editor_control,
            ]
        )

        def apply_workspace_edits() -> menu.ExitActionResult:
            assert self.index_buffer is not None
            revised = self.index_buffer.read()
            changed_fields: list[str] = []
            try:
                if draft["priority"] != baseline["priority"]:
                    changed = manager.change_project_priority(
                        revised, project.name, draft["priority"]
                    )
                    if changed is not None:
                        revised = changed
                        changed_fields.append("priority")
                description = description_value()
                if description != baseline["description"]:
                    changed = manager.change_project_property(
                        revised, project.name, "DESCRIPTION", description
                    )
                    if changed is not None:
                        revised = changed
                        changed_fields.append("description")
                if draft["task_file"] != baseline["task_file"]:
                    changed = manager.change_project_property(
                        revised, project.name, "TASK_FILE", draft["task_file"]
                    )
                    if changed is not None:
                        revised = changed
                        changed_fields.append("task-file mirror")
                directories = directory_values()
                if directories != baseline["directories"]:
                    paths = manager.unique_resolved_directories(
                        list(directories), root
                    )
                    changed = manager.change_project_directories(
                        revised, project.name, paths
                    )
                    if changed is not None:
                        revised = changed
                        changed_fields.append("directories")
            except (
                orglib.OrgStructureError,
                manager.RegistryIndexError,
                ValueError,
            ) as exc:
                return menu.ExitActionResult(False, str(exc))

            if changed_fields:
                self.index_buffer.apply_text(
                    revised,
                    description=(
                        f"Edit {project.name} " + " and ".join(changed_fields)
                    ),
                )
            return menu.ExitActionResult(True)

        def mark_workspace_saved() -> None:
            assert self.index_buffer is not None
            current = orglib.parse(self.index_buffer.read()).project(project.name)
            assert current is not None
            current_directories = orglib.parse(
                self.index_buffer.read()
            ).directories(project.name).section
            baseline["priority"] = current.priority
            baseline["description"] = current.description
            baseline["task_file"] = current.task_file
            baseline["directories"] = tuple(
                current_directories.entries if current_directories is not None else ()
            )
            draft["priority"] = current.priority
            draft["task_file"] = current.task_file
            reset_control(description_area, current.description or "")
            reset_control(
                directory_area,
                "\n".join(
                    current_directories.entries
                    if current_directories is not None
                    else ()
                ),
            )
            if current_directories is not None:
                effective = manager.unique_resolved_directories(
                    list(current_directories.entries), root
                ) or [root]
                effective_state["count"] = len(effective)
                effective_state["source"] = "custom"
            if workspace is not None:
                workspace.summary = workspace_summary()

        def save_workspace(session: menu.InlineMenuSession) -> bool:
            prepared = apply_workspace_edits()
            if not prepared.success:
                session.set_transient_message(prepared.message)
                return False
            saved, message = self._save_index_changes()
            if not saved:
                if self.index_conflicts:
                    session.push_view(self._conflict_view(parent_is_save=False))
                else:
                    session.set_transient_message(message)
                return False
            mark_workspace_saved()
            session.set_outcome(message)
            return True

        def launch_editor(session: menu.InlineMenuSession) -> None:
            assert self.index_buffer is not None
            if core.editor_argv(self.index_buffer.path) is None:
                session.set_transient_message("VISUAL or EDITOR is not set")
                return
            saved, message = self._save_index_changes()
            if not saved:
                if self.index_conflicts:
                    session.push_view(self._conflict_view(parent_is_save=False))
                else:
                    session.set_transient_message(message)
                return
            section = orglib.parse(self.index_buffer.read()).project(project.name)
            if section is None:
                session.set_transient_message(
                    f"projects.org has no section for {project.name!r}"
                )
                return
            argv = core.editor_argv(
                self.index_buffer.path, section.heading_span.start_line + 1
            )
            assert argv is not None

            def run_editor() -> None:
                subprocess.run(argv, check=False)

            def done() -> None:
                assert self.index_buffer is not None
                try:
                    self.index_buffer.reload()
                    replacement = self._metadata_view(project)
                except (
                    OSError,
                    UnicodeError,
                    orglib.OrgStructureError,
                    ValueError,
                ) as exc:
                    session.set_transient_message(
                        f"Cannot reload projects.org: {exc}"
                    )
                    return
                self.index_conflicts = ()
                self._reconciliation_key = None
                session.replace_view(replacement)
                session.set_outcome(
                    f"Edited {manager.friendly_path(self.index_buffer.path)}"
                )

            session.set_outcome(message)
            session.suspend(run_editor, on_done=done)

        def dirty_resolution_view(*, for_editor: bool) -> menu.MenuView:
            rows = [
                menu.MenuRow(
                    1,
                    "SAVE",
                    "Save projects.org"
                    + (" and open the editor" if for_editor else " and return"),
                ),
                menu.MenuRow(2, "CONTINUE", "Return to the metadata workspace"),
                menu.MenuRow(
                    3,
                    "DISCARD",
                    "Discard workspace fields"
                    + (" and open the editor" if for_editor else " and return"),
                ),
            ]

            def handle(
                session: menu.InlineMenuSession,
                result: menu.MenuResult,
            ) -> None:
                if result.action != "select" or result.index is None:
                    return
                if result.index == 1:
                    session.pop_view()
                    return
                if result.index == 2:
                    discard_workspace_edits()
                    session.pop_view()
                    if for_editor:
                        launch_editor(session)
                    else:
                        session.pop_view(message="Discarded project metadata edits")
                    return
                session.pop_view()
                if save_workspace(session):
                    if for_editor:
                        launch_editor(session)
                    else:
                        session.pop_view(message="Saved project metadata")

            return menu.MenuView(
                rows=rows,
                on_result=handle,
                title=f"Unsaved project metadata: {project.name}",
                title_right=f"Registry: {self.display_path}",
                summary="One or more workspace fields have changed",
                instruction="↑↓/jk · ↵ choose · Esc/b/q continue editing",
                select_help="Choose how to handle unsaved metadata fields",
                back_help="Continue editing without resolving changes",
                selected_index=1,
            )

        def back(session: menu.InlineMenuSession) -> bool:
            if not is_dirty():
                return True
            session.push_view(dirty_resolution_view(for_editor=False))
            return False

        def change_choice(
            session: menu.InlineMenuSession,
            focus_index: int,
            direction: int,
        ) -> None:
            if focus_index != 0:
                return
            draft["priority"] = manager.shift_project_priority(
                draft["priority"], direction
            )
            session.set_message(None)

        def activate_control(
            session: menu.InlineMenuSession,
            focus_index: int,
        ) -> None:
            if focus_index == 3:
                target = (
                    manager.friendly_path(resolved_file)
                    if resolved_file is not None
                    else None
                )
                if draft["task_file"] == target:
                    session.set_transient_message("TASK_FILE mirror already matches")
                    return
                draft["task_file"] = target
                if workspace is not None:
                    workspace.summary = workspace_summary()
                session.set_transient_message(
                    "TASK_FILE correction staged; C-s saves it"
                )
                return
            if focus_index != 4:
                return
            if is_dirty():
                session.push_view(dirty_resolution_view(for_editor=True))
            else:
                launch_editor(session)

        workspace = menu.WorkspaceView(
            container=container,
            focus_targets=[
                priority_control,
                description_area,
                directory_area,
                mirror_control,
                editor_control,
            ],
            on_save=save_workspace,
            title=f"Project metadata: {project.name}",
            title_right=f"Registry: {self.display_path}",
            summary=workspace_summary(),
            instruction=(
                "↑↓←→ fields · Enter edit/open · "
                "C-s save · C-g help · Esc back"
            ),
            editing_instruction=(
                "EDITING FIELD · arrows edit · Esc finish · "
                "C-s save · C-g help"
            ),
            dirty_label="PROJECT EDITED",
            help_entries=[
                ("Arrow keys", "Move among fields and actions"),
                ("Tab/Shift-Tab", "Move among fields and actions"),
                ("Enter (field)", "Begin editing the focused field"),
                ("Left/Right (editing)", "Change project priority"),
                ("Typing (editing)", "Edit description or directories"),
                ("Enter (description)", "Finish editing the description"),
                ("Enter (directories)", "Insert a new directory line"),
                ("Enter (button)", "Refresh TASK_FILE or open the index editor"),
                ("Ctrl-S", "Save the entire projects.org buffer"),
                ("Esc", "Finish editing; from navigation, return"),
                ("C-g", "Show or close this help"),
            ],
            edit_focus_indices=frozenset({0, 1, 2}),
            multiline_edit_focus_indices=frozenset({2}),
            focused_index=1,
            is_dirty=is_dirty,
            on_back=back,
            status_text=self._buffer_status,
            choice_focus_indices=frozenset({0}),
            on_choice_change=change_choice,
            activate_focus_indices=frozenset({3, 4}),
            on_activate=activate_control,
            exit_owner=self,
            exit_label=project.name,
            prepare_exit=apply_workspace_edits,
            mark_exit_saved=mark_workspace_saved,
            discard_exit=discard_workspace_edits,
        )
        return workspace

    def _initial_view(self) -> menu.MenuView:
        """Return the project-list root; recovery is pushed above it."""
        return self.view()

    def _push_recovery_view(self, session: menu.InlineMenuSession) -> None:
        if self.index_buffer is None:
            return
        recovered = taskui.recovery_text(self.index_buffer)
        if recovered is not None:
            session.push_view(self._recovery_view(recovered))

    def _poll_index(self, session: menu.InlineMenuSession) -> None:
        """Adopt or reconcile index writes without touching the real file."""
        if self.index_buffer is None:
            return
        previous_saved = self.index_buffer.saved_text
        previous_text = self.index_buffer.read()
        previous_conflicts = self.index_conflicts
        _ready, message = self._reconcile_index(force=False)
        changed = (
            self.index_buffer.saved_text != previous_saved
            or self.index_buffer.read() != previous_text
        )
        if changed and session.current_view is self._rendered_project_view:
            index = self._rendered_project_view.selected_index
            project = self._selected_project(self._rendered_projects, index)
            self._replace_view(session, project, index)
        if message and (changed or self.index_conflicts != previous_conflicts):
            session.set_transient_message(message)

    def run(self) -> None:
        session = menu.InlineMenuSession(
            self._initial_view(),
            action_keys=(
                *taskui.InteractiveTaskController.action_keys(),
                *PROJECT_MENU_ACTIONS,
            ),
            final_message="No project changes",
            exit_name="ptui",
            exit_concerns=self._exit_concerns,
            on_poll=self._poll_index,
        )
        self.session = session
        self._push_recovery_view(session)
        session.run()
        if session.error:
            print(session.error, file=sys.stderr)

    def view(
        self,
        selected_name: str | None = None,
        fallback_index: int = 0,
    ) -> menu.MenuView:
        discovered = manager.discover_projects(self.workspace)
        metadata = self._metadata(discovered)
        snapshots = manager.snapshot_projects(discovered)
        projects = self._ordered(discovered, metadata, snapshots)

        def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
            project = self._selected_project(projects, result.index)
            fallback = result.index if result.index is not None else 0
            if result.action == "sort":
                self.view_state = self.view_state.next_sort()
                self._replace_view(session, project, fallback)
                session.set_transient_message(f"Sort: {self.view_state.sort_label}")
                return
            if result.action == "filter":
                self.view_state = self.view_state.next_filter()
                self._replace_view(session, project, fallback)
                session.set_transient_message(
                    f"Showing: {self.view_state.filter_label or 'all projects'}"
                )
                return
            if result.action == "view":
                def apply_view(
                    _inner: menu.InlineMenuSession,
                    revised: viewstate.ViewState,
                ) -> None:
                    # The list below rebuilds from this when the screen is
                    # popped; nothing to redraw while it is covered.
                    self.view_state = revised

                session.push_view(
                    viewui.view_options_screen(
                        self.view_state,
                        apply_view,
                        title="View options: project navigator",
                        save=self._save_index_from_screen,
                        save_help="Save buffered project metadata to projects.org",
                        title_right=f"Registry: {self.display_path}",
                    )
                )
                return
            if result.action == "save":
                self._save_index(session, project, fallback)
                return
            if result.action in {"undo", "redo"}:
                if self.index_buffer is None:
                    session.set_transient_message(
                        self.index_error or "projects.org is not available for editing"
                    )
                    return
                description = (
                    self.index_buffer.undo()
                    if result.action == "undo"
                    else self.index_buffer.redo()
                )
                if description is None:
                    session.set_transient_message(f"Nothing to {result.action}")
                    return
                self._replace_view(session, project, fallback)
                verb = "Undid" if result.action == "undo" else "Redid"
                session.set_transient_message(f"{verb}: {description}")
                return
            if result.action == "metadata":
                if project is None:
                    return
                if self.index_buffer is None:
                    session.set_transient_message(
                        self.index_error or "projects.org is not available for editing"
                    )
                    return
                try:
                    workspace = self._metadata_view(project)
                except (
                    OSError,
                    UnicodeError,
                    orglib.OrgStructureError,
                    manager.RegistryIndexError,
                    ValueError,
                ) as exc:
                    session.set_transient_message(str(exc))
                    return
                session.push_view(workspace)
                return
            if result.action in {"priority_up", "priority_down"}:
                if project is None:
                    return
                if self.index_buffer is None:
                    session.set_transient_message(
                        self.index_error or "projects.org is not available for editing"
                    )
                    return
                current = metadata[project.name].priority
                direction = 1 if result.action == "priority_up" else -1
                try:
                    target = manager.shift_project_priority(current, direction)
                    revised = manager.change_project_priority(
                        self.index_buffer.read(), project.name, target
                    )
                except (orglib.OrgStructureError, ValueError) as exc:
                    session.set_transient_message(str(exc))
                    return
                if revised is None:
                    boundary = "highest" if direction > 0 else "unset"
                    session.set_transient_message(
                        f"{project.name} priority is already {boundary}"
                    )
                    return
                description = (
                    f"Set {project.name} priority to {target}"
                    if target is not None
                    else f"Clear {project.name} priority"
                )
                self.index_buffer.apply_text(revised, description=description)
                self._replace_view(session, project, fallback)
                session.set_transient_message(description)
                return
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
            self.task_controller = controller
            controller.attach(session)

        def resume(session: menu.InlineMenuSession) -> None:
            self.task_controller = None
            view = session.current_view
            index = view.selected_index
            name = projects[index].name if 0 <= index < len(projects) else None
            session.replace_view(self.view(name, index))

        view = _project_view(
            projects,
            self.display_path,
            handle,
            listing=NAVIGATOR,
            instruction=_project_menu_instruction(
                self.view_state,
                dirty=self.index_buffer.dirty if self.index_buffer is not None else False,
            ),
            select_help="Open the highlighted project",
            actions=PROJECT_MENU_ACTIONS,
            metadata=metadata,
            snapshots=snapshots,
            selected_index=_anchor_index(projects, selected_name, fallback_index),
            on_resume=resume,
        )
        selected_summary = view.status_text

        def status() -> str:
            return " · ".join(
                part
                for part in (
                    self._buffer_status(),
                    selected_summary() if selected_summary is not None else "",
                )
                if part
            )

        view.status_text = status
        view.on_back = self._handle_project_back
        if self.index_buffer is not None and self.index_buffer.dirty:
            view.title += " *"
        self._rendered_project_view = view
        self._rendered_projects = projects
        return view

    def _handle_project_back(self, session: menu.InlineMenuSession) -> bool:
        if self.index_buffer is None or not self.index_buffer.dirty:
            return True
        session.push_view(self._save_view())
        return False

    def _save_view(self) -> menu.MenuView:
        assert self.index_buffer is not None
        rows = [
            menu.MenuRow(1, "SAVE", "Write pending edits to projects.org"),
            menu.MenuRow(2, "DISCARD", "Revert edits and remove #projects.org#"),
            menu.MenuRow(3, "CONTINUE", "Return to the project list"),
        ]

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            if result.index == 2:
                session.pop_view()
                return
            if result.index == 0:
                saved, message = self._save_index_changes()
                if not saved:
                    if self.index_conflicts:
                        session.push_view(self._conflict_view(parent_is_save=True))
                    else:
                        session.set_transient_message(message)
                    return
            else:
                self.index_buffer.discard()
                self.index_conflicts = ()
                self._reconciliation_key = None
                message = "Discarded changes to projects.org"
            session.pop_view()
            session.pop_view(message=message)

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title="Save changes to projects.org?",
            title_right=f"Registry: {self.display_path}",
            summary="The project index has buffered edits",
            instruction="↑↓/jk · ↵ choose · Esc/b/q continue editing",
            select_help="Choose how to resolve the buffered edits",
            back_help="Continue editing without saving or discarding",
            status_text=self._buffer_status,
        )

    def _conflict_view(self, *, parent_is_save: bool) -> menu.MenuView:
        assert self.index_buffer is not None
        rows = [
            menu.MenuRow(
                1,
                "RELOAD",
                "Discard local edits and load the current projects.org",
            ),
            menu.MenuRow(
                2,
                "RETRY",
                "Repeat exact detection and in-memory reconciliation",
            ),
            menu.MenuRow(3, "CONTINUE", "Keep both versions and continue editing"),
        ]

        def return_to_project(
            session: menu.InlineMenuSession,
            message: str,
        ) -> None:
            session.pop_view()
            if parent_is_save:
                session.pop_view()
            session.set_transient_message(message)

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            if result.index == 2:
                return_to_project(session, "Continuing with merge conflict unresolved")
                return
            if result.index == 0:
                try:
                    self.index_buffer.reload()
                except (OSError, UnicodeError) as exc:
                    session.set_transient_message(f"Cannot reload projects.org: {exc}")
                    return
                self.index_conflicts = ()
                self._reconciliation_key = None
                return_to_project(session, "Reloaded projects.org; local edits discarded")
                return

            ready, message = self._reconcile_index(force=True)
            if not ready:
                session.set_transient_message(message or self._conflict_message())
                return
            return_to_project(
                session,
                message or "projects.org no longer conflicts",
            )

        details = "; ".join(conflict.detail for conflict in self.index_conflicts)
        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=self._conflict_message(),
            title_right=f"Registry: {self.display_path}",
            summary=details,
            instruction="↑↓/jk · ↵ choose · Esc/b/q back · C-g help",
            select_help="Resolve or defer the projects.org merge conflict",
            back_help="Return without resolving the conflict",
            status_text=self._buffer_status,
        )

    def _recovery_view(self, recovered: str) -> menu.MenuView:
        assert self.index_buffer is not None
        auto = self.index_buffer.autosave_path.name
        rows = [
            menu.MenuRow(1, "KEEP", f"Preserve {auto} for later"),
            menu.MenuRow(2, "RECOVER", f"Load {auto} into the project buffer"),
            menu.MenuRow(3, "DISCARD", f"Delete {auto} and use projects.org"),
        ]
        resolving_choice = False

        def finish(session: menu.InlineMenuSession, message: str) -> None:
            nonlocal resolving_choice
            resolving_choice = True
            try:
                session.pop_view(message=message)
            finally:
                resolving_choice = False

        def keep(session: menu.InlineMenuSession) -> None:
            finish(session, f"Keeping {auto} for later")

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            if result.index == 1:
                self.index_buffer.recover(recovered)
                finish(session, f"Recovered {auto} into the project buffer")
            elif result.index == 2:
                self.index_buffer.discard()
                finish(session, f"Discarded recovery data in {auto}")
            else:
                keep(session)

        def back(session: menu.InlineMenuSession) -> bool:
            if not resolving_choice:
                session.set_outcome(f"Keeping {auto} for later")
            return True

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title="Unsaved project metadata found",
            title_right=f"Registry: {self.display_path}",
            summary=f"Recovery file: {auto}",
            instruction="↑↓/jk · ↵ choose · Esc/b/q keep for later",
            select_help="Choose how to handle the recovery data",
            back_help="Keep recovery data and open the saved project list",
            on_back=back,
        )


def project_menu(workspace: Path, display_path: str, include_done: bool = False) -> int:
    """Interactive project navigator, with the numbered menu as the fallback."""
    if menu.interactive_select_available():
        _ProjectBrowser(workspace, display_path, include_done).run()
        return 0

    view = PROJECT_VIEW_AXES.initial()
    while True:
        discovered = manager.discover_projects(workspace)
        metadata = manager.read_project_metadata(workspace, discovered)
        snapshots = manager.snapshot_projects(discovered)
        projects = manager.sort_projects(
            manager.filter_projects(discovered, view.filter, snapshots=snapshots),
            metadata,
            view.sort,
            reverse=view.reverse,
            snapshots=snapshots,
        )
        _print_project_dashboard(
            projects, display_path, NAVIGATOR, metadata, snapshots
        )
        try:
            choice = menu.prompt_text(
                f"{view.badge()}; number, s=sort, t=filter, Esc/q=quit"
            ).strip().lower()
        except menu.ContextCancelled:
            return 0
        if choice == "s":
            view = view.next_sort()
            continue
        # ``C-t`` is not typeable at a plain prompt, so the numbered fallback
        # spells the same cycle as a letter, next to ``s``.
        if choice in {"t", "\x14", "c-t"}:
            view = view.next_filter()
            continue
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
    return project_menu(workspace, display_path)


def _list_note(record: dict) -> str:
    """Why a project has no task rows, in the words the verbose form uses."""
    warning = record.get("warning")
    if warning is not None:
        label = "invalid" if "duplicate task IDs" in warning else "warning"
        return f"({label}: {warning})"
    if record["file"] is None:
        return "(no task file)"
    return ""


def _list_workload(record: dict) -> str:
    """How much open work a project has, or why it has no task rows.

    Counted from the record the verbose listing prints, so the number here and
    the rows there can never disagree.
    """
    note = _list_note(record)
    if note:
        return note
    open_tasks = sum(1 for task in record["tasks"] if task["state"] == "TODO")
    listed = len(record["tasks"])
    if listed == open_tasks:
        return f"{open_tasks} open"
    return f"{open_tasks} open of {listed}"      # --all lists DONE tasks too


def _list_rows(records: list[dict]) -> list[str]:
    """One line per project: name, how much is open, and where it lives.

    Column widths come from the listing itself, so a long project name widens
    the table rather than pushing its own row out of line. A note standing in
    for a count — a warning, or no task file — may overflow instead, since one
    broken project should not pad every other row out to the width of its
    complaint. The name column starts at the navigator's width, so a project
    sits in about the same place whichever surface you are reading.
    """
    cells = [
        (record["project"], _list_workload(record), record["file"] or record["path"])
        for record in records
    ]
    name_width = max(
        [len(name) for name, _, _ in cells] + [PROJECT_NAME_WIDTH]
    )
    work_width = max(
        [
            len(workload)
            for (_, workload, _), record in zip(cells, records)
            if not _list_note(record)
        ],
        default=0,
    )
    return [
        f"{name:<{name_width}}  {workload:<{work_width}}  {location}".rstrip()
        for name, workload, location in cells
    ]


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
    elif not args.verbose:
        # One line per project. A registry is a place to choose from, and a
        # screenful of task rows per project buries the choice.
        print(f"Registry: {display_path}")
        print()
        for row in _list_rows(projects_data):
            print(row)
    else:
        print(f"Registry: {display_path}")
        for p in projects_data:
            print()
            print(f"{p['project']}  {p['file'] or p['path']}")
            note = _list_note(p)
            if note:
                print(f"  {note}")
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


def _resolve_current_pwd() -> Path:
    pwd_env = os.environ.get("PWD")
    if pwd_env:
        return Path(pwd_env).expanduser()
    return Path.cwd()


def cmd_info(args: argparse.Namespace) -> int:
    """Display resolved project context and metadata."""
    is_single_field = (
        getattr(args, "name", False)
        or getattr(args, "path", False)
        or getattr(args, "file", False)
    )
    registry, registry_display = manager.resolve_registry(
        getattr(args, "registry", None)
    )
    if not registry.is_dir():
        if not is_single_field:
            print(f"project directory not found: {registry}", file=sys.stderr)
        return 1

    projects = manager.discover_projects(registry)
    target = getattr(args, "target", None)

    project = None
    if target:
        # 1. Exact match by registered project name
        for p in projects:
            if p.name == target:
                project = p
                break
        # 2. Match by filesystem path
        if project is None:
            target_path = Path(target).expanduser().resolve()
            for p in projects:
                real_p = manager.real_project_path(p)
                if (real_p and real_p.resolve() == target_path) or p.entry_path.resolve() == target_path:
                    project = p
                    break
        if project is None:
            if not is_single_field:
                print(f"info: project not found in registry: {target}", file=sys.stderr)
            return 1
    else:
        # Infer from PWD / project_root_for
        pwd = _resolve_current_pwd()
        try:
            root = manager.project_root_for(pwd)
        except Exception as exc:
            if not is_single_field:
                print(f"info: {exc}", file=sys.stderr)
            return 1

        matches = [p for p in projects if manager.real_project_path(p) == root]
        if len(matches) == 1:
            project = matches[0]
        elif len(matches) == 0:
            if not is_single_field:
                print(f"info: no registered project matches {manager.friendly_path(root)}", file=sys.stderr)
            return 1
        else:
            if not is_single_field:
                names = ", ".join(p.name for p in matches)
                print(f"info: multiple registered projects match {manager.friendly_path(root)}: {names}", file=sys.stderr)
            return 1

    real_path = manager.real_project_path(project)
    task_file = manager.canonical_org_file(project)

    if getattr(args, "name", False):
        print(project.name)
        return 0

    if getattr(args, "path", False):
        if real_path is None:
            return 1
        print(str(real_path))
        return 0

    if getattr(args, "file", False):
        if task_file is None or not task_file.exists():
            return 1
        print(str(task_file))
        return 0

    # Full report
    snapshots = manager.snapshot_projects([project])
    snapshot = snapshots.get(project.name)
    metadata_map = manager.read_project_metadata(registry, [project])
    metadata = metadata_map.get(project.name)

    # Directories stack
    dir_source_label = "none"
    dir_count = 0
    try:
        candidates = manager.directory_candidates(project)
        for c in candidates:
            if c.defines_stack:
                dir_source_label = c.label
                dir_count = len(c.entries) if c.entries is not None else 0
                break
    except Exception:
        pass

    # Heading in projects.org
    priority_part = f"[#{metadata.priority}] " if (metadata and metadata.priority) else ""
    heading_str = f"* {priority_part}{project.name}"

    open_count = snapshot.open_tasks if (snapshot and snapshot.readable) else 0
    total_count = snapshot.total_tasks if (snapshot and snapshot.readable) else 0
    completed_count = (
        (total_count - open_count)
        if (total_count is not None and open_count is not None)
        else 0
    )

    fmt = getattr(args, "format", "plain")
    if fmt == "json":
        data = {
            "project": project.name,
            "directory": str(real_path) if real_path else None,
            "task_file": str(task_file) if task_file else None,
            "registry": str(registry),
            "heading": heading_str,
            "directories": {
                "source": dir_source_label,
                "count": dir_count,
            },
            "tasks": {
                "open": open_count,
                "completed": completed_count,
                "total": total_count,
            } if task_file else None,
            "warning": project.warning,
        }
        print(json.dumps(data, indent=2))
        return 0

    # Plain text format
    print(f"Project:     {project.name}")
    dir_display = manager.friendly_path(real_path) if real_path else "(broken link)"
    print(f"Directory:   {dir_display}")

    task_file_display = manager.friendly_path(task_file) if task_file else "none"
    mismatch = manager.task_file_mirror_mismatch(
        project, metadata.task_file if metadata else None
    )
    if mismatch:
        task_file_display += f" ({mismatch})"
    print(f"Task file:   {task_file_display}")

    reg_display = f"{manager.friendly_path(registry)} (heading: {heading_str})"
    print(f"Registry:    {reg_display}")

    dir_info = f"{dir_source_label} ({dir_count} {'entry' if dir_count == 1 else 'entries'})"
    print(f"Directories: {dir_info}")

    if task_file and snapshot and snapshot.readable:
        tasks_info = f"open: {open_count}, completed: {completed_count}, total: {total_count}"
    else:
        tasks_info = "no task file" if not task_file else "unreadable"
    print(f"Tasks:       {tasks_info}")

    if project.warning:
        print(f"Warning:     {project.warning}")

    return 0


@dataclass(frozen=True)
class _IndexSectionPlan:
    """What `add` will do to the registry index, decided before anything writes."""

    path: Path
    revised: str | None          # text to write, or None to leave the file alone
    note: str                    # the summary line, once it has happened
    dry_note: str                # the same line, before it has


def _plan_index_section(
    registry: Path,
    name: str,
    project_dir: Path,
    org_file: Path | None,
) -> _IndexSectionPlan:
    """Write `name` a section to work from, or say why the index is left alone.

    An unmigrated or unreadable index is not an error here: the symlinks are
    what register a project, so `add` reports the skip and still succeeds.
    """
    index = manager.PROJECTS_INDEX_NAME
    path = registry / index
    try:
        path, text = manager.require_registry_index(registry)
        revised = manager.add_project_section(
            text,
            name,
            manager.initial_directory_stack(project_dir, org_file),
            task_file=org_file,
        )
    except (manager.RegistryIndexError, ValueError, OSError) as exc:
        return _IndexSectionPlan(path, None, f"({index} untouched: {exc})", "")
    if revised is None:
        return _IndexSectionPlan(path, None, f"({index} already names '{name}')", "")
    return _IndexSectionPlan(
        path,
        revised,
        f"{index}: added section '* {name}'",
        f"{index}: would add section '* {name}'",
    )


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
            print(
                f"warning: {exc}; adding project link only",
                file=sys.stderr,
            )
            found = None
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

    index_plan = _plan_index_section(registry, name, project_dir, org_file)

    if args.dry_run:
        print(f"[dry-run] would create {subdir}/")
        print(f"[dry-run]   {project_dir.name} -> {project_dir}")
        if org_link is not None:
            print(f"[dry-run]   {org_file.name} -> {org_file}")
        print(f"[dry-run]   {index_plan.dry_note or index_plan.note}")
        return 0

    subdir.mkdir(parents=True, exist_ok=True)
    _replace_symlink(project_link, project_dir)
    if org_link is not None:
        _replace_symlink(org_link, org_file)

    # The links are the registration; the index heading is a convenience on
    # top of them, so a failure here is a warning rather than a failed add.
    index_note = index_plan.note
    index_seeded = index_plan.revised is not None
    if index_seeded:
        try:
            core.atomic_write(index_plan.path, index_plan.revised)
        except OSError as exc:
            print(f"warning: cannot write {index_plan.path}: {exc}", file=sys.stderr)
            index_note = f"({manager.PROJECTS_INDEX_NAME} untouched: write failed)"
            index_seeded = False

    eventlog.record(
        eventlog.make_event(
            "pmgr",
            "add",
            project=name,
            detail={
                "path": manager.friendly_path(project_dir),
                "task_file": manager.friendly_path(org_file) if org_file else None,
                "index_section": index_seeded,
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
    print(f"  {index_note}")
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

    pwd = _resolve_current_pwd()
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


def _add_project_section_action(
    registry: Path, action: manager.RepairAction
) -> bool:
    """Write the section this action already described, and say it happened."""
    assert action.project is not None
    try:
        index_path, index_text = manager.require_registry_index(registry)
        revised = manager.add_project_section(
            index_text,
            action.project,
            list(action.paths),
            task_file=action.task_file,
        )
        if revised is None:
            return False
        core.atomic_write(index_path, revised)
    except (manager.RegistryIndexError, ValueError, OSError) as exc:
        print(f"  cannot add: {exc}", file=sys.stderr)
        return False
    print(f"  added '* {action.project}'")
    eventlog.record(
        eventlog.make_event(
            "pmgr",
            "repair",
            project=action.project,
            detail={"index_section": True},
        ),
        registry=registry,
        source_file=action.task_file,
    )
    return True


#: Every executable repair kind, and the function that performs it. A finding
#: whose action names a kind absent from here is reported and never executed,
#: so adding an action to the manager layer cannot start writing on its own.
_REPAIR_EXECUTORS = {
    "add-project-section": _add_project_section_action,
}


def _render_action(action: manager.RepairAction) -> None:
    """Show the change before it is offered, so an answer can be informed."""
    print(f"    proposed: {action.summary}")
    for path in action.paths:
        print(f"      - {manager.format_directory_path(path)}")


def _apply_repair_plan(
    registry: Path, findings: list[manager.RepairFinding], *, force: bool
) -> tuple[int, int]:
    """Offer each planned action in turn; return (applied, left unresolved).

    Driven entirely by the findings already reported: nothing is rediscovered
    here, so the command cannot apply a change it did not show. Declining or
    cancelling leaves the finding unresolved rather than retrying it.
    """
    applied = 0
    unresolved = 0
    cancelled = False
    for finding in findings:
        action = finding.action
        if action is None or action.kind not in _REPAIR_EXECUTORS:
            unresolved += 1
            continue
        if cancelled:
            unresolved += 1
            continue
        # The plan was printed in full above; the prompt names the change, so
        # repeating it here would only make the report harder to read.
        if not force:
            try:
                answer = menu.prompt_text(f"{action.summary}? [y]es, [N]o").lower()
            except menu.ContextCancelled:
                print("  cancelled")
                cancelled = True
                unresolved += 1
                continue
            if answer not in {"y", "yes"}:
                print(f"  skipped: {action.summary}")
                unresolved += 1
                continue
        if _REPAIR_EXECUTORS[action.kind](registry, action):
            applied += 1
        else:
            unresolved += 1
    return applied, unresolved


def _project_warning_finding(
    registry: Path,
    record: dict,
) -> manager.RepairFinding:
    """Turn one project summary warning into an actionable repair finding."""
    project = str(record["project"])
    warning = str(record["warning"])
    org_file = record.get("file")
    if warning.startswith("duplicate task IDs") and org_file:
        kind = "duplicate-task-ids"
        suggestion = (
            f"edit {org_file} so every task ID is unique, then rerun pmgr repair"
        )
    elif warning == "no parseable tasks found" and org_file:
        kind = "no-parseable-tasks"
        suggestion = (
            f"add a parseable Org task heading to {org_file}, then rerun "
            "pmgr repair"
        )
    elif warning.startswith("Could not read file") and org_file:
        kind = "task-file-unreadable"
        suggestion = f"make {org_file} readable, then rerun pmgr repair"
    else:
        kind = "invalid-project-entry"
        entry = manager.friendly_path(registry / project)
        suggestion = (
            f"correct the project and task-file links under {entry}, then rerun "
            "pmgr repair"
        )
    return manager.RepairFinding(
        kind,
        f"{project}: {warning}",
        suggestion,
    )


def _diagnose_registry(
    registry: Path,
) -> tuple[list[manager.RepairFinding], list[str], list[manager.Project]]:
    """Everything `repair` has to say about a registry, and its projects."""
    findings: list[manager.RepairFinding] = []
    notes: list[str] = []
    projects = manager.discover_projects(registry)

    for record in manager.summarize_projects(registry, include_all=True):
        if "warning" in record:
            findings.append(_project_warning_finding(registry, record))
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
                findings.append(
                    manager.RepairFinding(
                        "dangling-registry-link",
                        f"{child.name}: dangling link {link.name} -> "
                        f"{os.readlink(link)}",
                        f"remove or repoint {manager.friendly_path(link)}, then "
                        "rerun pmgr repair",
                    )
                )

    findings.extend(manager.registry_index_findings(registry, projects))
    return findings, notes, projects


def cmd_repair(args: argparse.Namespace) -> int:
    """Diagnose registry problems and repair what is safe (asking first)."""
    registry, registry_display = manager.resolve_registry(getattr(args, "registry", None))
    if not registry.is_dir():
        print(f"project directory not found: {registry}", file=sys.stderr)
        return 1

    findings, notes, projects = _diagnose_registry(registry)

    print(f"Registry: {registry_display}")
    for note in notes:
        print(f"  note: {note}")
    for finding in findings:
        print(f"  problem: {finding.message}")
        print(f"    suggestion: {finding.suggestion}")
        if finding.action is not None:
            _render_action(finding.action)
    if not findings:
        print("  no problems found")
        return 0

    if getattr(args, "dry_run", False):
        return 2

    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    force = getattr(args, "force", False)
    if not is_interactive and not force:
        print(
            "repair: interactive confirmation requires a TTY; use --dry-run or --force",
            file=sys.stderr,
        )
        return 1

    applied, _ = _apply_repair_plan(registry, findings, force=force)
    if not applied:
        print("  nothing applied; every problem above still stands")
        return 2

    # Re-diagnosed rather than deduced: a repair can reveal or resolve more
    # than the action that was run, and what remains is what a rerun would see.
    remaining, _, _ = _diagnose_registry(registry)
    if not remaining:
        print("  all problems fixed")
        return 0
    print(f"  {len(remaining)} problem(s) remain:")
    for finding in remaining:
        print(f"    problem: {finding.message}")
        print(f"      suggestion: {finding.suggestion}")
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
        self.index_buffer: taskui.OrgBuffer | None = None
        if projects:
            index_path, _ = manager.require_registry_index(
                projects[0].path.parent, projects
            )
            self.index_buffer = taskui.OrgBuffer(
                index_path,
                registry=projects[0].path.parent,
            )

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
        assert self.index_buffer is not None
        candidates = manager.directory_candidates_from_index(
            project,
            self.index_buffer.path,
            self.index_buffer.read(),
        )
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
            assert self.index_buffer is not None
            if core.editor_argv(path) is None:
                session.set_transient_message("VISUAL or EDITOR is not set")
                return
            try:
                lookup = orglib.parse(self.index_buffer.read()).directories(
                    project.name
                )
                if lookup.section is None:
                    revised = manager.change_project_directories(
                        self.index_buffer.read(), project.name, []
                    )
                    if revised is not None:
                        self.index_buffer.apply_text(
                            revised,
                            description=f"Initialize {project.name} directories",
                        )
                self.index_buffer.save()
                lookup = orglib.parse(self.index_buffer.read()).directories(
                    project.name
                )
            except (
                orglib.OrgStructureError,
                manager.RegistryIndexError,
                taskui.BufferChangedError,
                ValueError,
            ) as exc:
                session.set_transient_message(str(exc))
                return
            assert lookup.project_span is not None
            path = self.index_buffer.path
            line_num = lookup.project_span.start_line + 1
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
            if candidate.label == "private":
                assert self.index_buffer is not None
                try:
                    self.index_buffer.reload()
                except (OSError, UnicodeError) as exc:
                    session.set_transient_message(
                        f"Cannot reload projects.org: {exc}"
                    )
                    return
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

    sub.add_parser("help", help="show this help message")

    p_info = sub.add_parser(
        "info",
        help="display resolved project context and metadata",
    )
    p_info.add_argument(
        "target",
        nargs="?",
        default=None,
        metavar="PROJECT|PATH",
        help="project name or directory (default: infer from PWD)",
    )
    group = p_info.add_mutually_exclusive_group()
    group.add_argument(
        "--name",
        "-n",
        action="store_true",
        help="print only the registered project name",
    )
    group.add_argument(
        "--path",
        "-p",
        action="store_true",
        help="print only the project directory path",
    )
    group.add_argument(
        "--file",
        "-f",
        action="store_true",
        help="print only the canonical task file path",
    )
    group.add_argument(
        "--format",
        choices=["plain", "json"],
        default="plain",
        help="output format (plain or json)",
    )
    p_info.add_argument(
        "--registry",
        default=argparse.SUPPRESS,
        help="registry directory to inspect",
    )

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
        "-v",
        "--verbose",
        action="store_true",
        help="list each project's top-level tasks, not just a summary line",
    )
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

    p_repair = sub.add_parser(
        "repair",
        help="diagnose registry problems and fix them (asking first)",
    )
    p_repair.add_argument("--registry", default=argparse.SUPPRESS,
                          help="registry directory to check")
    p_repair.add_argument("--dry-run", action="store_true",
                          help="report problems without changing anything")
    p_repair.add_argument("--force", action="store_true",
                          help="apply all available fixes without prompting")
    p_repair.set_defaults(dry_run=False, force=False)

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
        "info": cmd_info,
        "init": cmd_init,
        "list": cmd_list,
        "log": cmd_log,
        "migrate": cmd_migrate,
        "projadd": cmd_add,         # deprecated alias
        "repair": cmd_repair,
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
