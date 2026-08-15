#!/usr/bin/env python3
"""Minimal project task menu for org-backed workspaces.

This script owns the interactive terminal UI. Project discovery, config
resolution, and task parsing/editing come from ``ortasklib``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Make ``ortasklib`` importable regardless of the working directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ortasklib import core, menu, tasks
from ortasklib.manager import (
    Project,
    canonical_org_file,
    discover_projects,
    resolve_registry,
)


DETAIL_LINE_LIMIT = 20
INLINE_DETAIL_LINE_LIMIT = 8


@dataclass(frozen=True)
class MenuItem:
    label: str
    detail: str
    task: core.TodoItem | None = None
    line_num: int | None = None


def autosave_path_for(path: Path) -> Path:
    """Emacs-style auto-save sibling: ``todo.org`` -> ``#todo.org#``."""
    return path.parent / f"#{path.name}#"


class OrgBuffer:
    """In-memory editing buffer for one Org file, with Emacs-style auto-save.

    Reads come from the in-memory text; :meth:`apply` updates it and mirrors the
    new content to the auto-save sibling (``#name#``) for crash recovery. The
    real file is written only by :meth:`save`. :meth:`discard` drops the pending
    changes (and the auto-save file) without touching the real file. This is the
    interactive editing model (t0006); the one-shot CLI still writes immediately.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.autosave_path = autosave_path_for(path)
        self._saved_text = path.read_text(encoding="utf-8")
        self._text = self._saved_text
        self.dirty = False

    def read(self) -> str:
        return self._text

    def apply(self, new_lines: list[str]) -> None:
        """Replace the buffer with ``new_lines`` and refresh the auto-save file."""
        self._set_text(core.lines_to_text(new_lines))

    def recover(self, text: str) -> None:
        """Adopt recovered auto-save ``text`` as the (dirty) buffer contents."""
        self._set_text(text)

    def _set_text(self, text: str) -> None:
        self._text = text
        self.dirty = text != self._saved_text
        if self.dirty:
            core.atomic_write(self.autosave_path, text)
        else:
            self._remove_autosave()

    def save(self) -> None:
        """Atomically write the real file and clear the auto-save."""
        core.atomic_write(self.path, self._text)
        self._saved_text = self._text
        self.dirty = False
        self._remove_autosave()

    def discard(self) -> None:
        """Drop pending changes and the auto-save; leave the real file as-is."""
        self._text = self._saved_text
        self.dirty = False
        self._remove_autosave()

    def reload(self) -> None:
        """Re-read the real file (e.g. after an external editor) as clean."""
        self._saved_text = self.path.read_text(encoding="utf-8")
        self._text = self._saved_text
        self.dirty = False
        self._remove_autosave()

    def _remove_autosave(self) -> None:
        try:
            self.autosave_path.unlink()
        except FileNotFoundError:
            pass


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
        if mode == "done":
            filtered = [task for task in task_items if task.state in core.TERMINAL_STATES]
        elif mode == "todo":
            filtered = [task for task in task_items if task.state == "TODO"]
        else:
            filtered = task_items
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


TASK_FILTERS = ("all", "todo", "done")


def _task_filter_mode(include_done: bool, filter_mode: str | None = None) -> str:
    if filter_mode is None:
        return "all" if include_done else "todo"
    normalized = filter_mode.lower()
    if normalized not in TASK_FILTERS:
        raise ValueError(f"unknown task filter: {filter_mode}")
    return normalized


def _next_task_filter(filter_mode: str) -> str:
    index = TASK_FILTERS.index(_task_filter_mode(True, filter_mode))
    return TASK_FILTERS[(index + 1) % len(TASK_FILTERS)]


def _task_filter_label(filter_mode: str) -> str:
    return {"all": "all", "todo": "TODO", "done": "DONE"}[
        _task_filter_mode(True, filter_mode)
    ]


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


def _dashboard_row(idx: int, item: MenuItem) -> menu.MenuRow:
    if item.task is None:
        return menu.MenuRow(idx, "ORG", item.label)
    priority = f" [#{item.task.priority}]" if item.task.priority else ""
    indent = "  " * max(item.task.level - 2, 0)
    return menu.MenuRow(idx, item.task.state, f"{indent}{item.task.id}{priority} {item.task.text}")


def _print_dashboard(title: str, org_file: Path, items: list[MenuItem]) -> None:
    rows = [_dashboard_row(idx, item) for idx, item in enumerate(items, start=1)]
    menu.print_task_dashboard(title, org_file, rows)


def _project_rows(workspace: Path, projects: list[Project]) -> list[menu.ProjectRow]:
    rows: list[menu.ProjectRow] = []
    for idx, project in enumerate(projects, start=1):
        try:
            org_file = str(project.org_file.relative_to(workspace))
        except ValueError:
            org_file = str(project.org_file)
        rows.append(menu.ProjectRow(idx, project.name, org_file))
    return rows


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
    selected = item.task
    prefix = selected.id + "."
    items = core.parse_org(buf.read())
    children = [
        task for task in items
        if task.id.startswith(prefix) and task.level == selected.level + 1
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


def _open_editor(buf: OrgBuffer, line_num: int | None) -> None:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        print("VISUAL or EDITOR is not set")
        return
    parts = shlex.split(editor)
    if not parts:
        print("VISUAL or EDITOR is empty")
        return
    # The external editor edits the real file, so flush any buffered changes
    # first, then re-read whatever it wrote back into the buffer.
    if buf.dirty:
        buf.save()
        print(f"saved pending changes to {buf.path.name} before opening the editor")
    org_file = buf.path
    editor_name = Path(parts[0]).name
    line = None if line_num is None else line_num + 1
    if line is not None and editor_name in {"vi", "vim", "nvim", "less"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
    elif line is not None and editor_name in {"emacs", "emacsclient"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
    elif line is not None and editor_name in {"nano", "pico"}:
        subprocess.run(parts + [f"+{line}", str(org_file)], check=False)
    elif line is not None and editor_name in {"code", "codium"}:
        subprocess.run(parts + ["--goto", f"{org_file}:{line}"], check=False)
    else:
        subprocess.run(parts + [str(org_file)], check=False)
    buf.reload()


def _task_menu_instruction(filter_mode: str) -> str:
    return (
        f"{_task_filter_label(filter_mode)} · ↑↓/jk · ↵ open · "
        "C-g help · Shift+←/→ state · Shift+↑/↓ priority · "
        "C-t filter · p priority · e edit · Esc/b/q back"
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
TASK_MENU_ACTIONS = {
    "e": _EDIT_MENU_ACTION,
    "p": _PICK_PRIORITY_ACTION,
    "c-t": menu.MenuAction(
        "filter", "C-t", "Cycle visibility through all, TODO, and DONE"
    ),
    "s-left": _TOGGLE_MENU_ACTION,
    "s-right": _TOGGLE_MENU_ACTION,
    "s-up": _RAISE_PRIORITY_ACTION,
    "s-down": _LOWER_PRIORITY_ACTION,
}

FOCUS_MENU_ACTIONS = {
    "e": menu.MenuAction("edit", "e", "Open the current task in the editor"),
    "p": menu.MenuAction("priority", "p", "Choose the current task's priority"),
    "d": menu.MenuAction("done", "d", "Mark the current task DONE"),
    "s-left": menu.MenuAction(
        "toggle", "Shift+←/→", "Cycle the current task's state"
    ),
    "s-right": menu.MenuAction(
        "toggle", "Shift+←/→", "Cycle the current task's state"
    ),
    "s-up": menu.MenuAction(
        "priority_up", "Shift+↑", "Raise the current task's priority"
    ),
    "s-down": menu.MenuAction(
        "priority_down", "Shift+↓", "Lower the current task's priority"
    ),
}


def task_menu(project: Project, include_done: bool, *, dashboard: bool = True) -> None:
    org_file = canonical_org_file(project)
    buf = OrgBuffer(org_file)
    _maybe_recover(buf)
    if menu.interactive_select_available():
        _interactive_task_menu(project, buf, include_done)
        return

    while True:
        _numbered_task_menu(project, buf, include_done, dashboard=dashboard)
        # The menu loop returned, so the user is leaving this file's context.
        # If they Esc the save prompt, stay and re-enter the menu unsaved.
        if _resolve_buffer(buf):
            return


def _maybe_recover(buf: OrgBuffer) -> None:
    """Offer to recover auto-save data left over from a previous session.

    Three outcomes: recover (load it into the buffer), discard (delete the
    auto-save), or keep for later (leave the ``#name#`` file untouched and decide
    next time). Both ``Esc`` and the default keep it, since only an explicit
    ``n``/``no`` should throw away leftover recovery data.
    """
    if not buf.autosave_path.exists():
        return
    try:
        recovered = buf.autosave_path.read_text(encoding="utf-8")
    except OSError:
        return
    if recovered == buf.read():
        buf.discard()  # stale but identical -> nothing to recover, clean it up
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
    buf.save()
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
        buf.apply(new_lines)


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
        buf.apply(new_lines)


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
        buf.apply(new_lines)
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
        self.session: menu.InlineMenuSession | None = None

    def run(self) -> None:
        action_keys = tuple(
            dict.fromkeys([*TASK_MENU_ACTIONS, *FOCUS_MENU_ACTIONS])
        )
        session = menu.InlineMenuSession(
            self._task_view(),
            action_keys=action_keys,
        )
        self.session = session
        session.run()
        if session.error:
            print(session.error, file=sys.stderr)

    def _task_view(
        self,
        selected_id: str | None = None,
        fallback_index: int = 0,
    ) -> menu.MenuView:
        try:
            items = load_menu_items(
                self.buf,
                filter_mode=self.filter_mode,
                sort_key=_stable_sort_key,
            )
        except ValueError as exc:
            items = []
            rows = [menu.MenuRow(1, "ERROR", str(exc))]
        else:
            rows = [
                _dashboard_row(index, item)
                for index, item in enumerate(items, start=1)
            ]
        todo, done, total = menu.count_statuses(rows)
        start_index = _anchor_index(items, selected_id, fallback_index)

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            self._handle_task_result(session, items, result)

        def resume(session: menu.InlineMenuSession) -> None:
            old_view = session.current_view
            index = old_view.selected_index
            task_id = self._task_id_at(items, index)
            session.replace_view(self._task_view(task_id, index))

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"{self.project.name} tasks",
            summary=f"Open: {todo}  Done: {done}  Total: {total}",
            instruction=_task_menu_instruction(self.filter_mode),
            actions=TASK_MENU_ACTIONS,
            select_help="Open the highlighted task's details",
            selected_index=start_index,
            on_resume=resume,
            on_back=self._handle_task_back,
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
                self.buf.save()
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
        result: menu.MenuResult,
    ) -> None:
        index = result.index
        item = items[index] if index is not None and index < len(items) else None
        selected_id = self._task_id_at(items, index)
        fallback = index or 0

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
        selected_index: int = 0,
    ) -> menu.MenuView:
        item = _refresh_item(self.buf, item)
        subtasks = _direct_subtasks(self.buf, item)
        rows, targets = _focus_rows(item, subtasks)
        if item.task is not None:
            title = f"Edit {item.task.id}: {item.task.text}"
            summary = (
                f"State: {item.task.state} · Priority: "
                f"{item.task.priority or 'none'} · Line: {item.task.line_num + 1}"
            )
            actions = FOCUS_MENU_ACTIONS
        else:
            title = "Org heading"
            summary = item.label
            actions = {"e": _EDIT_MENU_ACTION}

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            self._handle_focus_result(session, item, targets, result)

        def resume(session: menu.InlineMenuSession) -> None:
            index = session.current_view.selected_index
            session.replace_view(self._focus_view(item, index))

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=title,
            summary=summary,
            preamble="\n".join(
                _context_lines(
                    self.buf,
                    item,
                    limit=INLINE_DETAIL_LINE_LIMIT,
                )
            ),
            instruction=(
                "↑↓/jk · ↵ choose · C-g help · Shift+←/→ state · "
                "Shift+↑/↓ priority · p priority · e editor · Esc/b/q back"
            ),
            actions=actions,
            select_help="Edit the highlighted field or open the subtask",
            selected_index=selected_index,
            on_resume=resume,
        )

    def _handle_focus_result(
        self,
        session: menu.InlineMenuSession,
        item: MenuItem,
        targets: list[tuple[str, MenuItem | None]],
        result: menu.MenuResult,
    ) -> None:
        selected_index = result.index or 0
        if result.action == "edit":
            self._suspend_for_editor(
                session,
                item.line_num,
                lambda: session.replace_view(
                    self._focus_view(item, selected_index)
                ),
            )
            return
        if item.task is None:
            return
        if result.action == "toggle":
            _toggle_state(self.buf, item)
            session.replace_view(self._focus_view(item, selected_index))
            return
        if result.action == "priority_up":
            _shift_priority(self.buf, item, 1)
            session.replace_view(self._focus_view(item, selected_index))
            return
        if result.action == "priority_down":
            _shift_priority(self.buf, item, -1)
            session.replace_view(self._focus_view(item, selected_index))
            return
        if result.action == "priority":
            session.push_view(self._priority_view(item))
            return
        if result.action == "done":
            session.push_view(self._done_confirmation_view(item))
            return
        if result.action != "select" or result.index is None:
            return

        action, target = targets[result.index]
        if action == "state":
            _toggle_state(self.buf, item)
            session.replace_view(self._focus_view(item, result.index))
        elif action == "priority":
            session.push_view(self._priority_view(item))
        elif action == "edit":
            self._suspend_for_editor(
                session,
                item.line_num,
                lambda: session.replace_view(
                    self._focus_view(item, result.index or 0)
                ),
            )
        elif action == "subtask" and target is not None:
            session.push_view(self._focus_view(target))

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
            summary=f"Current priority: {current or 'none'}",
            instruction="↑↓/jk · ↵ set · C-g help · Esc/b/q cancel",
            select_help="Set the highlighted priority",
            selected_index=start_index,
        )

    def _done_confirmation_view(self, item: MenuItem) -> menu.MenuView:
        assert item.task is not None
        rows = [
            menu.MenuRow(1, "YES", "Mark the task DONE"),
            menu.MenuRow(2, "NO", "Cancel and return to the task"),
        ]

        def handle(
            session: menu.InlineMenuSession,
            result: menu.MenuResult,
        ) -> None:
            if result.action != "select" or result.index is None:
                return
            if result.index == 0:
                try:
                    new_lines = tasks.change_state(
                        self.buf.read(),
                        item.task.id,
                        "DONE",
                    )
                except tasks.TaskNotFound:
                    new_lines = None
                if new_lines is not None:
                    self.buf.apply(new_lines)
            session.pop_view()

        return menu.MenuView(
            rows=rows,
            on_result=handle,
            title=f"Mark {item.task.id} DONE?",
            summary=item.task.text,
            instruction="↑↓/jk · ↵ choose · Esc/b/q cancel",
            select_help="Confirm or cancel the state change",
            selected_index=1,
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


def _focus_rows(
    item: MenuItem, subtasks: list[MenuItem]
) -> tuple[list[menu.MenuRow], list[tuple[str, MenuItem | None]]]:
    rows: list[menu.MenuRow] = []
    targets: list[tuple[str, MenuItem | None]] = []

    def add(
        status: str,
        text: str,
        action: str,
        target: MenuItem | None = None,
    ) -> None:
        rows.append(menu.MenuRow(len(rows) + 1, status, text))
        targets.append((action, target))

    if item.task is not None:
        add("STATE", item.task.state, "state")
        add("PRIOR", item.task.priority or "none", "priority")
    add("EDIT", "Open task in external editor", "edit")
    for subtask in subtasks:
        assert subtask.task is not None
        priority = f" [#{subtask.task.priority}]" if subtask.task.priority else ""
        add(
            subtask.task.state,
            f"{subtask.task.id}{priority} {subtask.task.text}",
            "subtask",
            subtask,
        )
    return rows, targets


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


PROJECT_MENU_INSTRUCTION = "↑↓/jk · ↵ open · C-g help · Esc/b/q exit"


def project_menu(workspace: Path, include_done: bool) -> int:
    while True:
        projects = discover_projects(workspace)
        rows = _project_rows(workspace, projects)
        if menu.interactive_select_available():
            result = menu.select_project_menu(
                rows,
                title="Projects",
                summary=f"Registry: {workspace}",
                instruction=PROJECT_MENU_INSTRUCTION,
            )
            if result.action == "back":
                return 0
            if result.index is not None and projects:
                task_menu(projects[result.index], include_done)
            continue

        menu.print_project_dashboard("Projects", workspace, rows)
        try:
            choice = _prompt_choice(len(projects), allow_back=False)
        except menu.ContextCancelled:
            return 0
        if choice in {"b", "q"}:
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(projects):
            print("invalid choice")
            continue
        task_menu(projects[int(choice) - 1], include_done)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal project menu for org task files.",
    )
    parser.add_argument(
        "--registry",
        dest="registry",
        default=None,
        help="project registry directory containing project subdirectories",
    )
    parser.add_argument(
        "--todo-only",
        action="store_true",
        help="start task views with only TODO tasks visible",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    workspace, display_path = resolve_registry(args.registry)
    print(f"Finding project in {display_path}")
    if not workspace.is_dir():
        print(f"project directory not found: {workspace}", file=sys.stderr)
        return 1
    return project_menu(workspace, include_done=not args.todo_only)


if __name__ == "__main__":
    raise SystemExit(main())
