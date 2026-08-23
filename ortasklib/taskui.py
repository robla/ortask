"""Interactive task UI: the buffered task list and issue workspace.

Used by ``ortask.py -i`` for one local file and by ``projmgr.py -i`` for a
project selected from the registry. This module owns the task-side terminal UI;
project discovery and config resolution come from ``manager``, parsing and
editing from ``core``/``tasks``, and the bounded application shell from
``menu``.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from textbuffer import (
    BufferChangedError,
    BufferTransaction,
    DiskSignature,
    ExternalFileChange,
    TextFileBuffer,
)

try:
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.layout import (
        FormattedTextControl,
        HSplit,
        ScrollOffsets,
        VSplit,
        Window,
    )
    from prompt_toolkit.widgets import TextArea
except ImportError:  # pragma: no cover - optional interactive dependency
    Document = None
    Condition = None
    FormattedText = None
    FormattedTextControl = None
    HSplit = None
    ScrollOffsets = None
    VSplit = None
    Window = None
    TextArea = None

from . import core, log as eventlog, menu, tasks, viewstate
from .manager import Project, canonical_org_file, friendly_path


DETAIL_LINE_LIMIT = 20
WORKSPACE_SUBTASK_HEIGHT = 5


@dataclass(frozen=True)
class MenuItem:
    label: str
    detail: str
    task: core.TodoItem | None = None
    line_num: int | None = None


def autosave_path_for(path: Path) -> Path:
    """Emacs-style auto-save sibling: ``todo.org`` -> ``#todo.org#``."""
    return path.parent / f"#{path.name}#"


class OrgBuffer(TextFileBuffer):
    """In-memory editing buffer for one Org file, with Emacs-style auto-save.

    This compatibility adapter supplies Org line conversion, the Emacs-style
    auto-save name, ortask's atomic writer, and best-effort activity logging to
    the format-neutral :class:`textbuffer.TextFileBuffer` state machine.
    """

    def __init__(
        self,
        path: Path,
        *,
        project: str | None = None,
        registry: Path | None = None,
    ) -> None:
        self.project = project
        self.registry = registry
        super().__init__(
            path,
            autosave_path=autosave_path_for(path),
            atomic_write=core.atomic_write,
            on_save=self._record_save,
        )

    def apply(
        self,
        new_lines: list[str],
        *,
        description: str = "Edit Org file",
    ) -> bool:
        """Record one logical edit and refresh the auto-save file."""
        return self.apply_text(
            core.lines_to_text(new_lines),
            description=description,
        )

    def apply_text(
        self,
        text: str,
        *,
        description: str = "Edit Org file",
    ) -> bool:
        """Keep the historical Org-specific default transaction label."""
        return super().apply_text(text, description=description)

    def _record_save(self, previous: str, current: str, path: Path) -> None:
        eventlog.record_task_edits(
            previous,
            current,
            path,
            project=self.project,
            registry=self.registry,
        )


def buffer_status(buf: OrgBuffer) -> str:
    """Shared header status for an interactive file-editing buffer."""
    count = buf.undo_count
    if buf.external_change is not None:
        alert = f"EXTERNAL CHANGE: {buf.external_change.summary}"
        if buf.dirty:
            noun = "edit" if count == 1 else "edits"
            return f"{alert} · FILE MODIFIED: {count} {noun}"
        return alert
    if buf.dirty:
        noun = "edit" if count == 1 else "edits"
        return f"FILE MODIFIED: {count} {noun}"
    if buf.can_undo:
        noun = "step" if count == 1 else "steps"
        return f"FILE CLEAN · Undo: {count} {noun}"
    if buf.can_redo:
        return "FILE CLEAN · Redo available"
    return ""


def _task_sort_key(item: core.TodoItem) -> int:
    """Interactive menus preserve the hierarchy by using Org file order."""
    return item.line_num


def _stable_sort_key(item: core.TodoItem) -> int:
    """Ordering for the highlight-bar selector.

    Same as :func:`_task_sort_key`: file order preserves Org hierarchy and keeps
    a task's row stable when its TODO/DONE state is toggled.
    """
    return item.line_num


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


def load_menu_items(
    buf: OrgBuffer,
    include_done: bool = True,
    *,
    filter_mode: str | None = None,
    sort_key=_task_sort_key,
) -> list[MenuItem]:
    text = buf.read()
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
            raise ValueError(f"duplicate task IDs in {buf.path}: {dupes}")
        mode = _task_filter_mode(include_done, filter_mode)
        filtered = [
            task
            for task in task_items
            if viewstate.task_state_matches(task.state, mode)
        ]
        return [
            MenuItem(
                label=f"[{task.state}] {task.id} {task.text}",
                detail="ortask task",
                task=task,
                line_num=task.line_num,
            )
            for task in sorted(filtered, key=sort_key)
        ]
    return _read_only_headings(text)


def _filter_menu_items(
    items: list[MenuItem], filter_mode: str
) -> list[MenuItem]:
    mode = _task_filter_mode(True, filter_mode)
    if not any(item.task is not None for item in items):
        return items
    return [
        item
        for item in items
        if item.task is not None
        and viewstate.task_state_matches(item.task.state, mode)
    ]


def _task_tree(
    items: list[MenuItem], org_text: str
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, int]]:
    """Return parent, direct-child, and display-depth maps in Org order."""
    parent_ids: dict[str, str] = {}
    child_ids: dict[str, list[str]] = {}
    depths: dict[str, int] = {}
    tasks_by_line = {
        item.task.line_num: item.task
        for item in items
        if item.task is not None
    }
    heading_stack: list[tuple[int, str | None]] = []

    for line_num, line in enumerate(org_text.splitlines()):
        if not core.ORG_HEADING_RE.match(line):
            continue
        level = len(line) - len(line.lstrip("*"))
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        task = tasks_by_line.get(line_num)
        task_id = task.id if task is not None else None
        if task_id is not None:
            parent_id = next(
                (
                    ancestor_id
                    for _, ancestor_id in reversed(heading_stack)
                    if ancestor_id is not None
                ),
                None,
            )
            if parent_id is None:
                depths[task_id] = 0
            else:
                parent_ids[task_id] = parent_id
                child_ids.setdefault(parent_id, []).append(task_id)
                depths[task_id] = depths[parent_id] + 1
        heading_stack.append((level, task_id))
    return parent_ids, child_ids, depths


def _include_task_ancestors(
    items: list[MenuItem], parent_ids: dict[str, str]
) -> set[str]:
    included = {item.task.id for item in items if item.task is not None}
    for task_id in tuple(included):
        parent_id = parent_ids.get(task_id)
        while parent_id is not None:
            included.add(parent_id)
            parent_id = parent_ids.get(parent_id)
    return included


def _visible_task_items(
    items: list[MenuItem],
    included_ids: set[str],
    expanded_ids: set[str],
    parent_ids: dict[str, str],
) -> list[MenuItem]:
    visible: list[MenuItem] = []
    visible_ids: set[str] = set()
    for item in items:
        if item.task is None:
            visible.append(item)
            continue
        task_id = item.task.id
        if task_id not in included_ids:
            continue
        parent_id = parent_ids.get(task_id)
        if parent_id is None or (
            parent_id in visible_ids and parent_id in expanded_ids
        ):
            visible.append(item)
            visible_ids.add(task_id)
    return visible


def _nearest_visible_task_id(
    selected_id: str | None,
    visible_ids: set[str],
    parent_ids: dict[str, str],
) -> str | None:
    while selected_id is not None:
        if selected_id in visible_ids:
            return selected_id
        selected_id = parent_ids.get(selected_id)
    return None


TASK_FILTERS = viewstate.TASK_FILTERS


def _task_filter_mode(include_done: bool, filter_mode: str | None = None) -> str:
    if filter_mode is None:
        return viewstate.FILTER_ALL if include_done else viewstate.FILTER_TODO
    normalized = filter_mode.lower()
    if normalized not in TASK_FILTERS:
        raise ValueError(f"unknown task filter: {filter_mode}")
    return normalized


def task_view(
    include_done: bool, filter_mode: str | None = None
) -> viewstate.ViewState:
    """The task list's view state, seeded by ``--todo`` on the first render.

    The sort axis has one position until ``t0039.5`` gives the tree a
    sibling-scoped order, so nothing here offers ``s`` yet.
    """
    return viewstate.TASK_VIEW_AXES.initial(
        _task_filter_mode(include_done, filter_mode)
    )


def _next_task_filter(filter_mode: str) -> str:
    return viewstate.next_position(
        TASK_FILTERS, _task_filter_mode(True, filter_mode)
    )


def _task_filter_label(filter_mode: str) -> str:
    return task_view(True, filter_mode).filter_label


def _prompt_choice(
    count: int,
    *,
    allow_back: bool = True,
    allow_editor: bool = False,
    allow_filter: bool = False,
) -> str:
    suffix = "number"
    if allow_editor:
        suffix += ", e=open editor"
    if allow_filter:
        suffix += ", C-t=filter"
    if allow_back:
        suffix += ", Esc/b/q=back"
    else:
        suffix += ", Esc/b/q=exit"
    return menu.prompt_text(suffix).lower()


def _print_items(title: str, items: list[MenuItem]) -> None:
    print()
    print(title)
    for idx, item in enumerate(items, start=1):
        print(f"  {idx}. {item.label}")
    if not items:
        print("  (no items)")


def _dashboard_row(
    idx: int,
    item: MenuItem,
    *,
    tree_depth: int | None = None,
    disclosure: str | None = None,
) -> menu.MenuRow:
    if item.task is None:
        return menu.MenuRow(idx, "ORG", item.label)
    priority = f" [#{item.task.priority}]" if item.task.priority else ""
    if tree_depth is None:
        indent = "  " * max(item.task.level - 2, 0)
        text = f"{indent}{item.task.id}{priority} {item.task.text}"
        return menu.MenuRow(idx, item.task.state, text)
    text = f"{item.task.id}{priority} {item.task.text}"
    return menu.MenuRow(
        idx,
        item.task.state,
        text,
        tree_depth=tree_depth,
        disclosure=disclosure,
    )


def _print_dashboard(title: str, org_file: Path, items: list[MenuItem]) -> None:
    rows = [_dashboard_row(idx, item) for idx, item in enumerate(items, start=1)]
    menu.print_task_dashboard(title, org_file, rows)


def _context_lines(
    buf: OrgBuffer,
    item: MenuItem,
    limit: int = DETAIL_LINE_LIMIT,
) -> list[str]:
    if item.task:
        items = core.parse_org(buf.read())
        lines: list[str] = []
        selected = item.task
        prefix = selected.id + "."
        for task in items:
            if task.id == selected.id or task.id.startswith(prefix):
                lines.append(core.build_org_heading(task))
                lines.extend(task.body_lines)
        display_lines = lines[:limit]
        if len(lines) > limit:
            display_lines.append(
                f"... truncated {len(lines) - limit} more line(s)"
            )
        return display_lines

    lines = buf.read().splitlines()
    if item.line_num is None:
        return []
    start = item.line_num
    base_level = len(lines[start].split(" ", 1)[0])
    display_lines = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("*"):
            level = len(line.split(" ", 1)[0])
            if level <= base_level:
                break
        display_lines.append(line)
    result = display_lines[:limit]
    if len(display_lines) > limit:
        result.append(
            f"... truncated {len(display_lines) - limit} more line(s)"
        )
    return result


def _show_context(buf: OrgBuffer, item: MenuItem) -> None:
    print()
    for line in _context_lines(buf, item):
        print(line)


def _direct_subtasks(buf: OrgBuffer, item: MenuItem) -> list[MenuItem]:
    if item.task is None:
        return []
    return [
        child
        for child in _descendant_subtasks(buf, item)
        if child.task is not None
        and child.task.level == item.task.level + 1
    ]


def _descendant_subtasks(buf: OrgBuffer, item: MenuItem) -> list[MenuItem]:
    if item.task is None:
        return []
    selected = item.task
    text = buf.read()
    lines = text.splitlines()
    subtree_end = len(lines)
    for line_num in range(selected.line_num + 1, len(lines)):
        line = lines[line_num]
        if not core.ORG_HEADING_RE.match(line):
            continue
        level = len(line.split(None, 1)[0])
        if level <= selected.level:
            subtree_end = line_num
            break
    children = [
        task
        for task in core.parse_org(text)
        if selected.line_num < task.line_num < subtree_end
        and task.level > selected.level
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


def _workspace_subtask_fragments(
    parent: core.TodoItem,
    subtasks: list[MenuItem],
    selected_index: int,
    *,
    active: bool,
) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    if not subtasks:
        return [("class:dim", "  (none)\n")]
    selected_index = min(max(selected_index, 0), len(subtasks) - 1)
    for index, subtask in enumerate(subtasks):
        assert subtask.task is not None
        depth = max(subtask.task.level - parent.level - 1, 0)
        selected = index == selected_index
        state_style = (
            "class:status.todo"
            if subtask.task.state == "TODO"
            else (
                "class:status.done"
                if subtask.task.state in core.TERMINAL_STATES
                else "class:status.other"
            )
        )
        cursor = "▶ " if selected else "  "
        line = (
            f"{cursor}{subtask.task.state:<6}  {'  ' * depth}"
            f"{subtask.task.id} {subtask.task.text}\n"
        )
        if selected:
            fragments.append(("[SetCursorPosition]", ""))
        if selected and active:
            selected_style = (
                "class:selected.todo"
                if subtask.task.state == "TODO"
                else (
                    "class:selected.done"
                    if subtask.task.state == "DONE"
                    else "class:selected.other"
                )
            )
            fragments.append((selected_style, line))
            continue
        fragments.append(("", cursor))
        fragments.append((state_style, f"{subtask.task.state:<6}"))
        fragments.append(
            (
                "",
                f"  {'  ' * depth}{subtask.task.id} {subtask.task.text}\n",
            )
        )
    return fragments


def _open_editor(buf: OrgBuffer, line_num: int | None) -> None:
    argv = core.editor_argv(
        buf.path,
        None if line_num is None else line_num + 1,
    )
    if argv is None:
        print("VISUAL or EDITOR is not set")
        return
    # The external editor edits the real file, so flush any buffered changes
    # first, then re-read whatever it wrote back into the buffer.
    if buf.dirty:
        try:
            buf.save()
        except BufferChangedError as exc:
            print(exc)
            return
        print(f"saved pending changes to {buf.path.name} before opening the editor")
    subprocess.run(argv, check=False)
    buf.reload()


def _task_menu_instruction(filter_mode: str) -> str:
    return (
        f"{task_view(True, filter_mode).badge()} · ↑↓/jk move · Tab fold · "
        "←/→ tree · S-Tab all · ↵ open · C-g help · "
        "C-s save · C-/ undo · C-r redo · C-t filter · Esc/b/q back"
    )


_TOGGLE_MENU_ACTION = menu.MenuAction(
    "toggle", "Shift+←/→", "Cycle the highlighted task's state"
)
_RAISE_PRIORITY_ACTION = menu.MenuAction(
    "priority_up", "Shift+↑", "Raise the highlighted task's priority"
)
_LOWER_PRIORITY_ACTION = menu.MenuAction(
    "priority_down", "Shift+↓", "Lower the highlighted task's priority"
)
_PICK_PRIORITY_ACTION = menu.MenuAction(
    "priority", "p", "Choose the highlighted task's priority"
)
_EDIT_MENU_ACTION = menu.MenuAction(
    "edit", "e", "Open the highlighted task in the editor"
)
_FOLD_MENU_ACTION = menu.MenuAction(
    "fold", "Tab", "Expand or collapse the highlighted task"
)
_SAVE_MENU_ACTION = menu.MenuAction(
    "save", "C-s", "Save all buffered edits to the Org file"
)
_UNDO_MENU_ACTION = menu.MenuAction(
    "undo", "C-/", "Undo the most recent buffered task edit"
)
_REDO_MENU_ACTION = menu.MenuAction(
    "redo", "C-r", "Redo the most recently undone task edit"
)
TASK_MENU_ACTIONS = {
    "c-i": _FOLD_MENU_ACTION,
    "s-tab": menu.MenuAction(
        "fold_all", "Shift+Tab", "Expand all tasks or return to the overview"
    ),
    "right": menu.MenuAction(
        "tree_right", "Right", "Expand the task or move to its first subtask"
    ),
    "left": menu.MenuAction(
        "tree_left", "Left", "Collapse the task or move to its parent"
    ),
    "e": _EDIT_MENU_ACTION,
    "p": _PICK_PRIORITY_ACTION,
    "c-t": menu.MenuAction(
        "filter", "C-t", "Cycle visibility through all, TODO, and DONE+MOOT"
    ),
    "s-left": _TOGGLE_MENU_ACTION,
    "s-right": _TOGGLE_MENU_ACTION,
    "s-up": _RAISE_PRIORITY_ACTION,
    "s-down": _LOWER_PRIORITY_ACTION,
    "c-s": _SAVE_MENU_ACTION,
    "c-_": _UNDO_MENU_ACTION,
    "c-r": _REDO_MENU_ACTION,
}

def task_menu(
    project: Project,
    include_done: bool,
    *,
    dashboard: bool = True,
    registry: Path | None = None,
) -> None:
    org_file = canonical_org_file(project)
    if org_file is None:
        # A registered project need not have a task file yet; see
        # ``docs/projects.md``. There is simply nothing to open.
        print(f"{project.name}: no task file")
        return
    buf = OrgBuffer(
        org_file,
        project=project.name if registry is not None else None,
        registry=registry,
    )
    if menu.interactive_select_available():
        _interactive_task_menu(project, buf, include_done)
        return

    _maybe_recover(buf)
    while True:
        _numbered_task_menu(project, buf, include_done, dashboard=dashboard)
        # The menu loop returned, so the user is leaving this file's context.
        # If they Esc the save prompt, stay and re-enter the menu unsaved.
        if _resolve_buffer(buf):
            return


def recovery_text(buf: OrgBuffer) -> str | None:
    """Return distinct auto-save content and clean up an identical stale copy."""
    if not buf.autosave_path.exists():
        return None
    try:
        recovered = buf.autosave_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if recovered == buf.read():
        buf.discard()  # stale but identical -> nothing to recover, clean it up
        return None
    return recovered


def _maybe_recover(buf: OrgBuffer) -> None:
    """Offer plain-mode recovery while preserving the safe three-way choice."""
    recovered = recovery_text(buf)
    if recovered is None:
        return
    name = buf.path.name
    auto = buf.autosave_path.name
    try:
        answer = menu.prompt_text(
            f"found unsaved changes for {name} in {auto}; "
            f"recover [y], discard [n], or keep for later [Enter]?"
        ).lower()
    except menu.ContextCancelled:
        answer = ""  # Esc -> keep for later
    if answer in ("y", "yes", "r", "recover"):
        buf.recover(recovered)
        print(f"recovered unsaved changes into the {name} buffer (not yet saved)")
    elif answer in ("n", "no", "d", "discard"):
        buf.discard()
        print(f"discarded the recovery data in {auto}")
    else:
        print(f"keeping {auto} for later (not recovered)")


def _resolve_buffer(buf: OrgBuffer) -> bool:
    """Prompt to save when leaving a file's editing context.

    Returns ``True`` when the exit may proceed (saved, discarded, or nothing was
    pending). Returns ``False`` when the user pressed ``Esc`` to stay in the
    still-running context without saving. Only an explicit ``n``/``no`` discards
    the pending edits; the default (``Enter``/``y``/``yes``) saves.
    """
    if not buf.dirty:
        return True
    name = buf.path.name
    try:
        answer = menu.prompt_text(f"{name} has been modified; save {name}? [Y/n]").lower()
    except menu.ContextCancelled:
        print(f"continuing to edit {name} (changes not saved)")
        return False
    if answer in ("n", "no"):
        buf.discard()
        print(f"discarded changes to {name}")
        return True
    try:
        buf.save()
    except BufferChangedError as exc:
        print(exc)
        return False
    print(f"saved {name}")
    return True


def _toggle_state(buf: OrgBuffer, item: MenuItem) -> None:
    """Cycle the selected task's keyword in the TODO/DONE ring (Emacs-style)."""
    if item.task is None:
        print("not an ortask task; cannot change state")
        return
    target = tasks.next_state(item.task.state)
    try:
        new_lines = tasks.change_state(buf.read(), item.task.id, target)
    except tasks.TaskNotFound:
        new_lines = None
    if new_lines is not None:
        buf.apply(
            new_lines,
            description=f"Set {item.task.id} state to {target}",
        )


def _refresh_item(buf: OrgBuffer, item: MenuItem) -> MenuItem:
    """Reload one task-backed menu item after an in-memory edit."""
    if item.task is None:
        return item
    current = core.find_by_id(core.parse_org(buf.read()), item.task.id)
    if current is None:
        return item
    return MenuItem(
        label=f"[{current.state}] {current.id} {current.text}",
        detail=item.detail,
        task=current,
        line_num=current.line_num,
    )


def _set_priority(buf: OrgBuffer, item: MenuItem, priority: str | None) -> None:
    if item.task is None:
        print("not an ortask task; cannot change priority")
        return
    try:
        new_lines = tasks.change_priority(buf.read(), item.task.id, priority)
    except tasks.TaskNotFound:
        new_lines = None
    if new_lines is not None:
        description = (
            f"Set {item.task.id} priority to {priority}"
            if priority is not None
            else f"Clear {item.task.id} priority"
        )
        buf.apply(new_lines, description=description)


def _shift_priority(buf: OrgBuffer, item: MenuItem, direction: int) -> None:
    if item.task is None:
        print("not an ortask task; cannot change priority")
        return
    target = tasks.shift_priority(item.task.priority, direction)
    _set_priority(buf, item, target)


PRIORITY_OPTIONS = (
    ("A", "Highest priority"),
    ("B", "Medium priority"),
    ("C", "Lowest explicit priority"),
    (None, "No explicit priority"),
)
WORKSPACE_STATE_OPTIONS = tuple(
    state for state in core.TASK_STATES if state != "SUPERSEDED"
)


def _priority_picker(buf: OrgBuffer, item: MenuItem) -> None:
    if item.task is None:
        print("not an ortask task; cannot change priority")
        return
    current = item.task.priority
    while True:
        try:
            answer = menu.prompt_text(
                f"priority for {item.task.id} [A/B/C/none; current {current or 'none'}]"
            ).strip().lower()
        except menu.ContextCancelled:
            return
        if answer in {"b", "q", ""}:
            return
        if answer in {"none", "-", "clear"}:
            _set_priority(buf, item, None)
            return
        if answer.upper() in tasks.PRIORITIES:
            _set_priority(buf, item, answer.upper())
            return
        print("priority must be A, B, C, or none")


def _mark_done(buf: OrgBuffer, item: MenuItem) -> None:
    if item.task is None:
        return
    try:
        confirm = menu.prompt_text(f"mark {item.task.id} DONE? [y/N]").lower()
    except menu.ContextCancelled:
        print("cancelled")
        return
    if confirm != "y":
        return
    try:
        new_lines = tasks.change_state(buf.read(), item.task.id, "DONE")
    except tasks.TaskNotFound:
        new_lines = None
    if new_lines is not None:
        buf.apply(
            new_lines,
            description=f"Set {item.task.id} state to DONE",
        )
    print(f"marked {item.task.id} DONE")


def _anchor_index(items: list[MenuItem], selected_id: str | None, fallback: int) -> int:
    """Index of the task with ``selected_id``; clamped ``fallback`` if it's gone.

    Keeps the highlight on the same task across reloads (e.g. after a toggle),
    so the bar does not drift to a neighbor (t0007).
    """
    if selected_id is not None:
        for i, item in enumerate(items):
            if item.task is not None and item.task.id == selected_id:
                return i
    if not items:
        return 0
    return min(max(fallback, 0), len(items) - 1)


class InteractiveTaskController:
    """Build ortask views for one persistent bounded menu application."""

    def __init__(
        self,
        project: Project,
        buf: OrgBuffer,
        include_done: bool,
    ) -> None:
        self.project = project
        self.buf = buf
        self.filter_mode = _task_filter_mode(include_done)
        self.expanded_task_ids: set[str] = set()
        self.session: menu.InlineMenuSession | None = None

    @staticmethod
    def action_keys() -> tuple[str, ...]:
        return tuple(TASK_MENU_ACTIONS)

    def run(self) -> None:
        session = menu.InlineMenuSession(
            self.initial_view(),
            action_keys=self.action_keys(),
            final_message=f"No changes to {self.buf.path.name}",
        )
        self.session = session
        session.run()
        if session.error:
            print(session.error, file=sys.stderr)

    def attach(self, session: menu.InlineMenuSession) -> None:
        """Push this task context onto an existing bounded session."""
        self.session = session
        session.push_view(self.initial_view())

    def _buffer_status(self) -> str:
        return buffer_status(self.buf)

    def _file_context(self) -> str:
        return f"File: {friendly_path(self.buf.path.resolve())}"

    def initial_view(self) -> menu.MenuView:
        recovered = recovery_text(self.buf)
        if recovered is None:
            return self._task_view()
        return self._recovery_view(recovered)

    def _recovery_view(self, recovered: str) -> menu.MenuView:
        name = self.buf.path.name
        auto = self.buf.autosave_path.name
        rows = [
            menu.MenuRow(1, "KEEP", f"Open {name} and preserve {auto} for later"),
            menu.MenuRow(2, "RECOVER", f"Load {auto} into the editing buffer"),
            menu.MenuRow(3, "DISCARD", f"Delete {auto} and open the saved file"),
        ]

        def finish(session: menu.InlineMenuSession, message: str) -> None:
            session.replace_view(self._task_view())
            session.set_outcome(message)

        def keep(session: menu.InlineMenuSession) -> None:
            finish(session, f"Keeping {auto} for later")

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            if result.index == 1:
                self.buf.recover(recovered)
                finish(session, f"Recovered {auto} into the buffer (not yet saved)")
            elif result.index == 2:
                self.buf.discard()
                finish(session, f"Discarded recovery data in {auto}")
            else:
                keep(session)

        def back(session: menu.InlineMenuSession) -> bool:
            keep(session)
            return False

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"Unsaved changes found for {name}",
            title_right=self._file_context(),
            summary=f"Recovery file: {auto}",
            instruction="↑↓/jk · ↵ choose · Esc/b/q keep for later",
            select_help="Choose how to handle the recovery data",
            back_help="Keep recovery data and open the saved task list",
            on_back=back,
        )

    def _task_view(
        self,
        selected_id: str | None = None,
        fallback_index: int = 0,
    ) -> menu.MenuView:
        parent_ids: dict[str, str] = {}
        child_ids: dict[str, list[str]] = {}
        included_ids: set[str] = set()
        try:
            all_items = load_menu_items(
                self.buf,
                filter_mode="all",
                sort_key=_stable_sort_key,
            )
            matched_items = _filter_menu_items(all_items, self.filter_mode)
            parent_ids, child_ids, depths = _task_tree(
                all_items,
                self.buf.read(),
            )
            included_ids = _include_task_ancestors(matched_items, parent_ids)
            items = _visible_task_items(
                all_items,
                included_ids,
                self.expanded_task_ids,
                parent_ids,
            )
        except ValueError as exc:
            items = []
            matched_items = []
            rows = [menu.MenuRow(1, "ERROR", str(exc))]
        else:
            rows = []
            for index, item in enumerate(items, start=1):
                if item.task is None:
                    rows.append(_dashboard_row(index, item))
                    continue
                task_id = item.task.id
                expandable = any(
                    child_id in included_ids
                    for child_id in child_ids.get(task_id, [])
                )
                disclosure = (
                    "▾" if task_id in self.expanded_task_ids else "▸"
                ) if expandable else " "
                rows.append(
                    _dashboard_row(
                        index,
                        item,
                        tree_depth=depths.get(task_id, 0),
                        disclosure=disclosure,
                    )
                )
        count_rows = [
            _dashboard_row(index, item)
            for index, item in enumerate(matched_items, start=1)
        ]
        todo, done, total = menu.count_statuses(count_rows)
        visible_ids = {
            item.task.id for item in items if item.task is not None
        }
        anchor_id = _nearest_visible_task_id(
            selected_id,
            visible_ids,
            parent_ids,
        )
        start_index = _anchor_index(items, anchor_id, fallback_index)

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            self._handle_task_result(
                session,
                items,
                parent_ids,
                child_ids,
                included_ids,
                result,
            )

        def resume(session: menu.InlineMenuSession) -> None:
            old_view = session.current_view
            index = old_view.selected_index
            task_id = self._task_id_at(items, index)
            session.replace_view(self._task_view(task_id, index))

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"{self.project.name} tasks",
            title_right=self._file_context(),
            summary=f"Open: {todo}  Done: {done}  Total: {total}",
            instruction=_task_menu_instruction(self.filter_mode),
            actions=TASK_MENU_ACTIONS,
            select_help="Open the highlighted task's details",
            selected_index=start_index,
            on_resume=resume,
            on_back=self._handle_task_back,
            status_text=self._buffer_status,
        )

    def _handle_task_back(self, session: menu.InlineMenuSession) -> bool:
        if not self.buf.dirty:
            return True
        session.push_view(self._save_view())
        return False

    def _save_view(self) -> menu.MenuView:
        name = self.buf.path.name
        rows = [
            menu.MenuRow(1, "SAVE", f"Write pending edits to {name}"),
            menu.MenuRow(2, "DISCARD", "Revert edits and remove the auto-save"),
            menu.MenuRow(3, "CONTINUE", "Return to the task list without leaving"),
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
                try:
                    self.buf.save()
                except BufferChangedError as exc:
                    session.set_transient_message(str(exc))
                    return
                message = f"Saved changes to {name}"
            else:
                self.buf.discard()
                message = f"Discarded changes to {name}"
            session.pop_view()
            session.pop_view(message=message)

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"Save changes to {name}?",
            title_right=self._file_context(),
            summary="The Org file has buffered edits",
            instruction="↑↓/jk · ↵ choose · Esc/b/q continue editing",
            select_help="Choose how to resolve the buffered edits",
            back_help="Continue editing without saving or discarding",
        )

    @staticmethod
    def _task_id_at(items: list[MenuItem], index: int | None) -> str | None:
        if index is None or not 0 <= index < len(items):
            return None
        task = items[index].task
        return task.id if task is not None else None

    def _handle_task_result(
        self,
        session: menu.InlineMenuSession,
        items: list[MenuItem],
        parent_ids: dict[str, str],
        child_ids: dict[str, list[str]],
        included_ids: set[str],
        result: menu.MenuResult,
    ) -> None:
        index = result.index
        item = items[index] if index is not None and index < len(items) else None
        selected_id = self._task_id_at(items, index)
        fallback = index or 0

        if result.action == "save":
            changed = self.buf.dirty
            try:
                self.buf.save()
            except BufferChangedError as exc:
                session.set_transient_message(str(exc))
                return
            session.replace_view(self._task_view(selected_id, fallback))
            message = (
                f"Saved changes to {self.buf.path.name}"
                if changed
                else f"Saved {self.buf.path.name} (unchanged)"
            )
            session.set_outcome(message)
            return
        if result.action in {"undo", "redo"}:
            description = (
                self.buf.undo()
                if result.action == "undo"
                else self.buf.redo()
            )
            if description is None:
                session.set_transient_message(f"Nothing to {result.action}")
                return
            session.replace_view(self._task_view(selected_id, fallback))
            verb = "Undid" if result.action == "undo" else "Redid"
            session.set_transient_message(f"{verb}: {description}")
            return

        if result.action == "fold_all":
            expandable_ids = set(child_ids)
            if not expandable_ids:
                session.set_transient_message("No task subtrees to expand")
                return
            if expandable_ids <= self.expanded_task_ids:
                self.expanded_task_ids.clear()
            else:
                self.expanded_task_ids.update(expandable_ids)
            session.replace_view(self._task_view(selected_id, fallback))
            return
        if result.action == "edit":
            line_num = item.line_num if item is not None else None
            self._suspend_for_editor(
                session,
                line_num,
                lambda: session.replace_view(
                    self._task_view(selected_id, fallback)
                ),
            )
            return
        if result.action == "filter":
            self.filter_mode = _next_task_filter(self.filter_mode)
            session.replace_view(self._task_view(selected_id, fallback))
            return
        if item is None:
            return
        if result.action in {"fold", "tree_left", "tree_right"}:
            if item.task is None:
                session.set_transient_message(
                    "This Org heading has no task subtree"
                )
                return
            task_id = item.task.id
            children = [
                child_id
                for child_id in child_ids.get(task_id, [])
                if child_id in included_ids
            ]
            if result.action == "fold":
                if not children:
                    session.set_transient_message(
                        f"{task_id} has no visible subtasks"
                    )
                    return
                if task_id in self.expanded_task_ids:
                    self.expanded_task_ids.remove(task_id)
                else:
                    self.expanded_task_ids.add(task_id)
                session.replace_view(self._task_view(task_id, fallback))
                return
            if result.action == "tree_right":
                if not children:
                    session.set_transient_message(
                        f"{task_id} has no visible subtasks"
                    )
                    return
                if task_id not in self.expanded_task_ids:
                    self.expanded_task_ids.add(task_id)
                    target_id = task_id
                else:
                    target_id = children[0]
                session.replace_view(self._task_view(target_id, fallback))
                return
            if task_id in self.expanded_task_ids and children:
                self.expanded_task_ids.remove(task_id)
                target_id = task_id
            else:
                target_id = parent_ids.get(task_id)
            if target_id is None:
                session.set_transient_message(
                    f"{task_id} is already at the tree root"
                )
                return
            session.replace_view(self._task_view(target_id, fallback))
            return
        if result.action == "toggle":
            _toggle_state(self.buf, item)
            session.replace_view(self._task_view(selected_id, fallback))
        elif result.action == "priority_up":
            _shift_priority(self.buf, item, 1)
            session.replace_view(self._task_view(selected_id, fallback))
        elif result.action == "priority_down":
            _shift_priority(self.buf, item, -1)
            session.replace_view(self._task_view(selected_id, fallback))
        elif result.action == "priority" and item.task is not None:
            session.push_view(self._priority_view(item))
        elif result.action == "select":
            session.push_view(self._focus_view(item))

    def _focus_view(
        self,
        item: MenuItem,
    ) -> menu.InlineView:
        item = _refresh_item(self.buf, item)
        if item.task is None:
            rows = [menu.MenuRow(1, "EDIT", "Open heading in external editor")]

            def handle_heading(
                session: menu.InlineMenuSession,
                result: menu.MenuResult,
            ) -> None:
                if result.action not in {"select", "edit"}:
                    return
                self._suspend_for_editor(
                    session,
                    item.line_num,
                    lambda: session.replace_view(self._focus_view(item)),
                )

            return menu.MenuView(
                rows=rows,
                on_result=handle_heading,
                title="Org heading",
                title_right=self._file_context(),
                summary=item.label,
                instruction="↵/e editor · C-g help · Esc/b/q back",
                actions={"e": _EDIT_MENU_ACTION},
                select_help="Open this heading in the external editor",
            )

        assert all(
            dependency is not None
            for dependency in (
                FormattedText,
                FormattedTextControl,
                HSplit,
                ScrollOffsets,
                Window,
                VSplit,
                TextArea,
                Document,
                Condition,
            )
        )
        task_id = item.task.id
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

        title_area = TextArea(
            text=item.task.text,
            multiline=False,
            wrap_lines=False,
            height=1,
            dont_extend_height=True,
            read_only=Condition(lambda: not field_is_editing(2)),
        )
        title_area.window.always_hide_cursor = Condition(
            lambda: not field_is_editing(2)
        )
        title_area.window.style = lambda: field_style(2)
        title_area.buffer.cursor_position = len(title_area.text)
        body_area = TextArea(
            text="\n".join(item.task.body_lines),
            multiline=True,
            wrap_lines=False,
            scrollbar=True,
            read_only=Condition(lambda: not field_is_editing(3)),
        )
        body_area.window.always_hide_cursor = Condition(
            lambda: not field_is_editing(3)
        )
        body_area.window.style = lambda: field_style(3)
        body_area.buffer.cursor_position = len(body_area.text)
        draft: dict[str, str | None] = {
            "state": item.task.state,
            "priority": item.task.priority,
        }
        baseline = {
            "title": title_area.text,
            "body": body_area.text,
            "state": draft["state"],
            "priority": draft["priority"],
        }
        descendant_subtasks = _descendant_subtasks(self.buf, item)
        subtask_focus_index = 4 if descendant_subtasks else None
        editor_focus_index = 5 if descendant_subtasks else 4
        selected_subtask_id = [
            descendant_subtasks[0].task.id
            if descendant_subtasks and descendant_subtasks[0].task is not None
            else None
        ]
        selected_subtask_fallback = [0]

        def current_subtasks() -> list[MenuItem]:
            return descendant_subtasks

        def selected_subtask_index(subtasks: list[MenuItem]) -> int:
            if not subtasks:
                selected_subtask_id[0] = None
                selected_subtask_fallback[0] = 0
                return 0
            for index, subtask in enumerate(subtasks):
                if (
                    subtask.task is not None
                    and subtask.task.id == selected_subtask_id[0]
                ):
                    selected_subtask_fallback[0] = index
                    return index
            index = min(selected_subtask_fallback[0], len(subtasks) - 1)
            assert subtasks[index].task is not None
            selected_subtask_id[0] = subtasks[index].task.id
            selected_subtask_fallback[0] = index
            return index

        def set_selected_subtask(subtasks: list[MenuItem], index: int) -> None:
            if not subtasks:
                return
            index = min(max(index, 0), len(subtasks) - 1)
            assert subtasks[index].task is not None
            selected_subtask_id[0] = subtasks[index].task.id
            selected_subtask_fallback[0] = index

        def is_dirty() -> bool:
            return (
                title_area.text != baseline["title"]
                or body_area.text != baseline["body"]
                or draft["state"] != baseline["state"]
                or draft["priority"] != baseline["priority"]
            )

        def reset_control(control, text: str) -> None:
            control.buffer.reset(
                Document(text, cursor_position=len(text))
            )

        def reset_workspace_undo() -> None:
            reset_control(title_area, title_area.text)
            reset_control(body_area, body_area.text)

        def discard_workspace_edits() -> None:
            reset_control(title_area, baseline["title"])
            reset_control(body_area, baseline["body"])
            draft["state"] = baseline["state"]
            draft["priority"] = baseline["priority"]

        def label(text: str):
            return Window(
                FormattedTextControl(
                    FormattedText([("class:field.label", text)])
                ),
                height=1,
                always_hide_cursor=True,
            )

        def compact_choice(
            label_text: str,
            key: str,
            focus_index: int,
            width: int,
        ):
            def render():
                value = draft[key] or "none"
                text = f" {label_text} [{value}] "
                focused = (
                    workspace is not None
                    and workspace.focused_index == focus_index
                )
                if focused:
                    style = (
                        "class:field.editing"
                        if field_is_editing(focus_index)
                        else "class:choice.focused"
                    )
                    fragments = [(style, text)]
                else:
                    fragments = [
                        ("class:field.label", f" {label_text} "),
                        ("", f"[{value}] "),
                    ]
                return FormattedText(
                    [
                        ("[SetCursorPosition]", ""),
                        *fragments,
                    ]
                )

            return Window(
                FormattedTextControl(
                    render,
                    focusable=True,
                    show_cursor=False,
                ),
                width=width,
                height=1,
                dont_extend_width=True,
                always_hide_cursor=True,
            )

        def compact_button(label_text: str, focus_index: int, width: int):
            def render():
                text = f"[ {label_text} ]"
                focused = (
                    workspace is not None
                    and workspace.focused_index == focus_index
                )
                style = (
                    "class:choice.focused"
                    if focused
                    else "class:field.label"
                )
                return FormattedText(
                    [
                        ("[SetCursorPosition]", ""),
                        (style, text),
                    ]
                )

            return Window(
                FormattedTextControl(
                    render,
                    focusable=True,
                    show_cursor=False,
                ),
                width=width,
                height=1,
                dont_extend_width=True,
                always_hide_cursor=True,
            )

        def subtask_panel():
            def render():
                subtasks = current_subtasks()
                selected_index = selected_subtask_index(subtasks)
                active = (
                    workspace is not None
                    and workspace.focused_index == subtask_focus_index
                )
                return FormattedText(
                    _workspace_subtask_fragments(
                        item.task,
                        subtasks,
                        selected_index,
                        active=active,
                    )
                )

            return Window(
                FormattedTextControl(
                    render,
                    focusable=bool(descendant_subtasks),
                    show_cursor=False,
                ),
                height=lambda: min(
                    max(len(current_subtasks()), 1), WORKSPACE_SUBTASK_HEIGHT
                ),
                wrap_lines=False,
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                always_hide_cursor=True,
            )

        state_control = compact_choice("State", "state", 0, 22)
        priority_control = compact_choice("Priority", "priority", 1, 20)
        subtask_control = subtask_panel()
        editor_control = compact_button(
            "Open in external editor",
            editor_focus_index,
            27,
        )
        compact_controls = VSplit(
            [state_control, Window(width=1), priority_control],
            height=1,
        )

        container = HSplit(
            [
                compact_controls,
                label("Title"),
                title_area,
                label("Body"),
                body_area,
                label("Subtasks"),
                subtask_control,
                editor_control,
            ]
        )

        def save_workspace(session: menu.InlineMenuSession) -> bool:
            source = self.buf.read()
            changed_fields: list[str] = []
            try:
                new_lines = tasks.change_text(source, task_id, title_area.text)
                if new_lines is not None:
                    source = core.lines_to_text(new_lines)
                    changed_fields.append("title")
                new_lines = tasks.change_body(source, task_id, body_area.text)
                if new_lines is not None:
                    source = core.lines_to_text(new_lines)
                    changed_fields.append("body")
                target_state = draft["state"]
                assert target_state is not None
                new_lines = tasks.change_state(
                    source,
                    task_id,
                    target_state,
                )
                if new_lines is not None:
                    source = core.lines_to_text(new_lines)
                    changed_fields.append("state")
                new_lines = tasks.change_priority(
                    source,
                    task_id,
                    draft["priority"],
                )
                if new_lines is not None:
                    source = core.lines_to_text(new_lines)
                    changed_fields.append("priority")
            except tasks.TaskNotFound:
                session.set_transient_message(
                    f"task {task_id} no longer exists"
                )
                return False
            except ValueError as exc:
                session.set_transient_message(str(exc))
                return False

            if changed_fields:
                fields = " and ".join(changed_fields)
                self.buf.apply(
                    source.splitlines(),
                    description=f"Edit {task_id} {fields}",
                )
            changed = self.buf.dirty
            try:
                self.buf.save()
            except BufferChangedError as exc:
                session.set_transient_message(str(exc))
                return
            if changed:
                message = f"Saved changes to {self.buf.path.name}"
            else:
                message = f"Saved {self.buf.path.name} (unchanged)"
            baseline["title"] = title_area.text
            baseline["body"] = body_area.text
            baseline["state"] = draft["state"]
            baseline["priority"] = draft["priority"]
            reset_workspace_undo()
            session.set_outcome(message)
            return True

        def launch_editor(session: menu.InlineMenuSession) -> None:
            self._suspend_for_editor(
                session,
                item.line_num,
                lambda: session.replace_view(self._focus_view(item)),
            )

        def dirty_resolution_view(
            *,
            summary: str,
            save_detail: str,
            discard_detail: str,
            on_saved: Callable[[menu.InlineMenuSession], None],
            on_discarded: Callable[[menu.InlineMenuSession], None],
        ) -> menu.MenuView:
            rows = [
                menu.MenuRow(1, "SAVE", save_detail),
                menu.MenuRow(2, "CONTINUE", "Return to the task workspace"),
                menu.MenuRow(3, "DISCARD", discard_detail),
            ]

            def handle_resolution(
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
                    on_discarded(session)
                    return

                session.pop_view()
                if save_workspace(session):
                    on_saved(session)

            return menu.MenuView(
                rows=rows,
                on_result=handle_resolution,
                title=f"Unsaved task edits: {task_id}",
                title_right=self._file_context(),
                summary=summary,
                instruction=(
                    "↑↓/jk · ↵ choose · Esc/b/q continue editing"
                ),
                select_help="Choose how to handle unsaved task edits",
                back_help="Continue editing without resolving changes",
                selected_index=1,
            )

        def exit_warning_view() -> menu.MenuView:
            def finish_saved_exit(session: menu.InlineMenuSession) -> None:
                session.pop_view(
                    message=f"Saved changes to {self.buf.path.name}"
                )

            def finish_discarded_exit(session: menu.InlineMenuSession) -> None:
                session.pop_view(
                    message=f"Discarded unsaved edits to {task_id}"
                )

            return dirty_resolution_view(
                summary=(
                    "One or more task fields differ from the workspace baseline"
                ),
                save_detail=(
                    f"Save all changes to {self.buf.path.name} and return"
                ),
                discard_detail="Discard unsaved task-field edits and return",
                on_saved=finish_saved_exit,
                on_discarded=finish_discarded_exit,
            )

        def back(session: menu.InlineMenuSession) -> bool:
            if not is_dirty():
                return True
            session.push_view(exit_warning_view())
            return False

        def editor_warning_view() -> menu.MenuView:
            return dirty_resolution_view(
                summary="Resolve workspace edits before opening the editor",
                save_detail=(
                    f"Save all changes to {self.buf.path.name} and open editor"
                ),
                discard_detail="Discard workspace edits and open editor",
                on_saved=launch_editor,
                on_discarded=launch_editor,
            )

        def change_choice(
            session: menu.InlineMenuSession,
            focus_index: int,
            direction: int,
        ) -> None:
            if focus_index == 0:
                current = draft["state"]
                assert current is not None
                canonical = "MOOT" if current == "SUPERSEDED" else current
                index = WORKSPACE_STATE_OPTIONS.index(canonical)
                draft["state"] = WORKSPACE_STATE_OPTIONS[
                    (index + direction) % len(WORKSPACE_STATE_OPTIONS)
                ]
            else:
                draft["priority"] = tasks.shift_priority(
                    draft["priority"],
                    direction,
                )
            session.set_message(None)

        def move_subtask_list(
            session: menu.InlineMenuSession,
            focus_index: int,
            direction: int,
        ) -> None:
            if focus_index != subtask_focus_index:
                return
            subtasks = current_subtasks()
            selected = selected_subtask_index(subtasks)
            set_selected_subtask(subtasks, selected + direction)
            session.set_message(None)

        def activate_control(
            session: menu.InlineMenuSession,
            focus_index: int,
        ) -> None:
            if focus_index == subtask_focus_index:
                subtasks = current_subtasks()
                if not subtasks:
                    session.set_transient_message(
                        f"{task_id} has no subtasks to open"
                    )
                    return
                selected = selected_subtask_index(subtasks)
                session.push_view(self._focus_view(subtasks[selected]))
                return
            if focus_index != editor_focus_index:
                return
            if is_dirty():
                session.push_view(editor_warning_view())
            else:
                launch_editor(session)

        tags = item.task.tags or "none"
        subtask_count = len(descendant_subtasks)

        def summary(count: int) -> str:
            return (
                f"Tags: {tags} · Subtasks: {count} · "
                f"Line: {item.task.line_num + 1}"
            )

        def refresh_subtasks(_session: menu.InlineMenuSession) -> None:
            nonlocal descendant_subtasks
            descendant_subtasks = _descendant_subtasks(
                self.buf,
                _refresh_item(self.buf, item),
            )
            selected_subtask_index(descendant_subtasks)
            if workspace is not None:
                workspace.summary = summary(len(descendant_subtasks))

        focus_targets = [
            state_control,
            priority_control,
            title_area,
            body_area,
        ]
        if subtask_focus_index is not None:
            focus_targets.append(subtask_control)
        focus_targets.append(editor_control)
        activate_focus_indices = {editor_focus_index}
        edit_focus_indices = {0, 1, 2, 3}
        if subtask_focus_index is not None:
            activate_focus_indices.add(subtask_focus_index)
            edit_focus_indices.add(subtask_focus_index)
        workspace = menu.WorkspaceView(
            container=container,
            focus_targets=focus_targets,
            on_save=save_workspace,
            title=f"Edit {task_id}",
            title_right=self._file_context(),
            summary=summary(subtask_count),
            instruction=(
                "↑↓←→ fields · Enter edit/open · "
                "Ctrl-S save · C-g help · Esc back"
            ),
            editing_instruction=(
                "EDITING FIELD · arrows edit · Esc finish · "
                "Ctrl-S save · C-g help"
            ),
            help_entries=[
                ("Arrow keys", "Move among fields and actions"),
                ("Tab/Shift-Tab", "Move among fields and actions"),
                ("Enter (field)", "Begin editing the focused field"),
                ("Typing (editing)", "Edit the title or body"),
                ("Left/Right (editing)", "Change state or priority"),
                ("Enter (title/choice)", "Finish editing the field"),
                ("Enter (body)", "Insert a newline"),
                ("Up/Down (subtasks)", "Move after entering the subtask list"),
                ("Page Up/Down", "Move five subtasks at a time"),
                ("Enter (subtask)", "Open the highlighted child workspace"),
                ("Enter (button)", "Open this task in the external editor"),
                ("Ctrl-S", f"Save the entire {self.buf.path.name} file"),
                ("Esc", "Finish editing; from navigation, return"),
                ("C-g", "Show or close this help"),
            ],
            edit_focus_indices=frozenset(edit_focus_indices),
            multiline_edit_focus_indices=frozenset({3}),
            focused_index=2,
            is_dirty=is_dirty,
            on_back=back,
            on_resume=refresh_subtasks,
            status_text=self._buffer_status,
            choice_focus_indices=frozenset({0, 1}),
            on_choice_change=change_choice,
            list_focus_indices=(
                frozenset({subtask_focus_index})
                if subtask_focus_index is not None
                else frozenset()
            ),
            on_list_move=move_subtask_list,
            activate_focus_indices=frozenset(activate_focus_indices),
            on_activate=activate_control,
        )

        def clear_saved_message(_buffer) -> None:
            if self.session is not None and is_dirty():
                self.session.set_message(None)

        title_area.buffer.on_text_changed += clear_saved_message
        body_area.buffer.on_text_changed += clear_saved_message
        return workspace

    def _priority_view(self, item: MenuItem) -> menu.MenuView:
        assert item.task is not None
        current = item.task.priority
        rows = [
            menu.MenuRow(index, priority or "NONE", description)
            for index, (priority, description) in enumerate(
                PRIORITY_OPTIONS,
                start=1,
            )
        ]
        start_index = next(
            index
            for index, (priority, _) in enumerate(PRIORITY_OPTIONS)
            if priority == current
        )

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            _set_priority(self.buf, item, PRIORITY_OPTIONS[result.index][0])
            session.pop_view()

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"Set priority for {item.task.id}",
            title_right=self._file_context(),
            summary=f"Current priority: {current or 'none'}",
            instruction="↑↓/jk · ↵ set · C-g help · Esc/b/q cancel",
            select_help="Set the highlighted priority",
            selected_index=start_index,
            status_text=self._buffer_status,
        )

    def _suspend_for_editor(
        self,
        session: menu.InlineMenuSession,
        line_num: int | None,
        on_done: Callable[[], None],
    ) -> None:
        session.suspend(
            lambda: _open_editor(self.buf, line_num),
            on_done=on_done,
        )


def _interactive_task_menu(project: Project, buf: OrgBuffer, include_done: bool) -> None:
    InteractiveTaskController(project, buf, include_done).run()


def _numbered_task_menu(
    project: Project, buf: OrgBuffer, include_done: bool, *, dashboard: bool = True
) -> None:
    filter_mode = _task_filter_mode(include_done)
    while True:
        try:
            items = load_menu_items(buf, filter_mode=filter_mode)
        except ValueError as exc:
            print(exc)
            return
        if dashboard:
            _print_dashboard(f"{project.name} tasks", buf.path, items)
        else:
            title = f"{project.name} tasks ({buf.path})"
            _print_items(title, items)
        try:
            choice = _prompt_choice(len(items), allow_editor=True, allow_filter=True)
        except menu.ContextCancelled:
            return
        if choice in {"b", "q"}:
            return
        if choice == "e":
            _open_editor(buf, None)
            continue
        if choice in {"\x14", "c-t"}:
            filter_mode = _next_task_filter(filter_mode)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            print("invalid choice")
            continue

        item = items[int(choice) - 1]
        focus_menu(buf, item)


def local_file_menu(org_file: Path, include_done: bool = True) -> int:
    project_path = org_file.parent.resolve()
    project = Project(
        name=project_path.name or str(project_path),
        path=project_path,
        org_file=org_file,
    )
    task_menu(project, include_done, dashboard=True)
    return 0


def _numbered_focus_menu(buf: OrgBuffer, item: MenuItem) -> None:
    _show_context(buf, item)
    while True:
        item = _refresh_item(buf, item)
        subtasks = _direct_subtasks(buf, item)
        print()
        if subtasks:
            print("Subtasks:")
            for idx, subtask in enumerate(subtasks, start=1):
                print(f"  {idx}. {subtask.label}")
        print("Actions:")
        if item.task:
            print("  d. mark DONE")
            print(f"  p. set priority (current: {item.task.priority or 'none'})")
        print("  e. open in editor")
        print("  Esc/b/q. back one level")
        try:
            choice = menu.prompt_text("number, d/p/e, Esc/b/q").lower()
        except menu.ContextCancelled:
            return
        if choice in {"b", "q"}:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(subtasks):
            focus_menu(buf, subtasks[int(choice) - 1])
            _show_context(buf, item)
        elif choice == "d" and item.task:
            _mark_done(buf, item)
            _show_context(buf, _refresh_item(buf, item))
        elif choice == "p" and item.task:
            _priority_picker(buf, item)
            _show_context(buf, _refresh_item(buf, item))
        elif choice == "e":
            _open_editor(buf, item.line_num)
        else:
            print("invalid choice")


def focus_menu(buf: OrgBuffer, item: MenuItem) -> None:
    _numbered_focus_menu(buf, item)
