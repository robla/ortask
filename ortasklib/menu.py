"""Shared menu rendering helpers for interactive ortask tools."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional interactive dependency
    Console = None
    Table = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        FormattedTextControl,
        HSplit,
        Layout,
        ScrollOffsets,
        Window,
    )
    from prompt_toolkit.output.defaults import create_output
    from prompt_toolkit.styles import Style
except ImportError:  # pragma: no cover - optional interactive dependency
    PromptSession = None
    Application = None
    FormattedText = None
    KeyBindings = None
    FormattedTextControl = None
    HSplit = None
    Layout = None
    ScrollOffsets = None
    Window = None
    create_output = None
    Style = None


@dataclass(frozen=True)
class MenuRow:
    number: int
    status: str
    text: str


@dataclass(frozen=True)
class ProjectRow:
    number: int
    name: str
    org_file: str


@dataclass(frozen=True)
class MenuResult:
    """Outcome of a :func:`select_menu` interaction.

    ``action`` is one of ``"select"``, ``"quit"``, ``"back"``, or a custom
    action name supplied by the caller (e.g. ``"edit"``, ``"toggle"``).
    ``index`` is the 0-based highlighted row index, or ``None`` for
    ``"quit"``/``"back"``.
    """

    action: str
    index: int | None


class ContextCancelled(Exception):
    """Raised when Esc cancels the current menu context."""


RICH_CONSOLE = Console() if Console is not None else None
PROMPT_STYLE = (
    Style.from_dict(
        {
            "field.label": "ansicyan bold",
            "field.separator": "ansibrightblack",
        }
    )
    if Style is not None
    else None
)
SELECT_STYLE = (
    Style.from_dict(
        {
            "title": "bold",
            "summary": "ansibrightblack",
            # Selected-row highlight bar. The whole line takes a bright, solid
            # state hue with black text, so the selection reads as one
            # continuous bar (gold for TODO, green for DONE) and every part of
            # the line — the label included — stays legible, rather than the
            # label sitting same-color-on-same-color. Hex values are exact
            # xterm-256 palette entries (214/34/250/37) so they map cleanly at
            # 256 colors.
            "selected.todo": "bg:#ffaf00 fg:#000000",
            "selected.done": "bg:#00af00 fg:#000000",
            "selected.other": "bg:#bcbcbc fg:#000000",
            "selected.project": "bg:#00afaf fg:#000000",
            "status.todo": "ansiyellow",
            "status.done": "ansigreen",
            "status.other": "ansibrightblack",
            "project.name": "ansicyan",
            "hint": "ansibrightblack",
            "dim": "ansibrightblack",
        }
    )
    if Style is not None
    else None
)


def _prompt_available() -> bool:
    return (
        PromptSession is not None
        and FormattedText is not None
        and KeyBindings is not None
        and create_output is not None
        and sys.stdin.isatty()
    )


def _prompt_session() -> PromptSession:
    output = create_output()
    if hasattr(output, "enable_cpr"):
        output.enable_cpr = False
    session = PromptSession(style=PROMPT_STYLE, output=output)
    session.app.ttimeoutlen = 0.01
    return session


def _cancel_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("escape", eager=True)
    def _cancel(event) -> None:
        event.app.exit(exception=ContextCancelled())

    return bindings


def _message(label: str):
    return FormattedText(
        [
            ("class:field.label", label),
            ("class:field.separator", "> "),
        ]
    )


def prompt_text(label: str) -> str:
    """Prompt for one line; Esc cancels the current context when available."""
    if _prompt_available():
        try:
            return _prompt_session().prompt(
                _message(label),
                key_bindings=_cancel_bindings(),
                complete_while_typing=False,
                enable_history_search=True,
            ).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ContextCancelled() from exc

    answer = input(f"{label}> ").strip()
    if answer == "\x1b":
        raise ContextCancelled()
    return answer


def interactive_select_available() -> bool:
    """True when the prompt_toolkit highlight-bar selector can run.

    Requires prompt_toolkit and an interactive terminal on both stdin and
    stdout. Callers fall back to the plain numbered menu otherwise.
    """
    return (
        Application is not None
        and KeyBindings is not None
        and FormattedTextControl is not None
        and HSplit is not None
        and Window is not None
        and Layout is not None
        and ScrollOffsets is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _status_class(status: str) -> str:
    if status == "TODO":
        return "class:status.todo"
    if status == "DONE":
        return "class:status.done"
    return "class:status.other"


def _selected_bar(status: str) -> str:
    """Selection-bar class for a highlighted row, keyed to its task state.

    Mirrors :func:`_status_class`: TODO rows get the gold bar, DONE rows the
    green bar, anything else the neutral gray bar. The bar color (plus black
    text) carries the task state on the selected row, so the per-label
    ``status.*`` foreground is dropped there.
    """
    if status == "TODO":
        return "selected.todo"
    if status == "DONE":
        return "selected.done"
    return "selected.other"


def _run_selector(
    row_count: int,
    render: Callable[[int], FormattedText],
    *,
    title: str | None = None,
    summary: str | None = None,
    instruction: str | None = None,
    actions: dict[str, str] | None = None,
    start_index: int = 0,
) -> MenuResult:
    actions = actions or {}
    state = {
        "index": min(max(start_index, 0), row_count - 1) if row_count else 0
    }

    bindings = KeyBindings()

    def _move(delta: int) -> None:
        if row_count:
            state["index"] = (state["index"] + delta) % row_count

    @bindings.add("up")
    @bindings.add("k")
    def _up(event) -> None:
        _move(-1)

    @bindings.add("down")
    @bindings.add("j")
    def _down(event) -> None:
        _move(1)

    @bindings.add("enter")
    def _select(event) -> None:
        if row_count:
            event.app.exit(result=MenuResult("select", state["index"]))

    @bindings.add("q")
    def _quit(event) -> None:
        event.app.exit(result=MenuResult("quit", None))

    # ``escape`` is intentionally non-eager so arrow-key escape sequences are
    # not swallowed; prompt_toolkit disambiguates with its key timeout.
    @bindings.add("b")
    @bindings.add("escape")
    def _back(event) -> None:
        event.app.exit(result=MenuResult("back", None))

    def _make_action(action_name: str):
        def handler(event) -> None:
            event.app.exit(
                result=MenuResult(action_name, state["index"] if row_count else None)
            )
        return handler

    for key, action_name in actions.items():
        bindings.add(key)(_make_action(action_name))

    body_control = FormattedTextControl(
        lambda: render(state["index"]), focusable=True, show_cursor=False
    )
    body_window = Window(
        body_control,
        always_hide_cursor=True,
        scroll_offsets=ScrollOffsets(top=1, bottom=1),
        wrap_lines=False,
    )
    containers = []
    if title or summary:
        header: list[tuple[str, str]] = []
        header_height = 1  # Blank line between the heading and rows.
        if title:
            header.append(("class:title", title + "\n"))
            header_height += 1
        if summary:
            header.append(("class:summary", summary + "\n"))
            header_height += 1
        header.append(("", "\n"))
        containers.append(
            Window(
                FormattedTextControl(FormattedText(header)),
                height=header_height,
                always_hide_cursor=True,
            )
        )
    containers.append(body_window)
    if instruction:
        footer = FormattedText(
            [("", "\n"), ("class:hint", instruction)]
        )
        containers.append(
            Window(
                FormattedTextControl(footer),
                height=2,
                always_hide_cursor=True,
            )
        )
    app = Application(
        layout=Layout(HSplit(containers), focused_element=body_control),
        key_bindings=bindings,
        style=SELECT_STYLE,
        full_screen=False,
        mouse_support=False,
    )
    try:
        result = app.run()
    except (KeyboardInterrupt, EOFError):
        return MenuResult("quit", None)
    if result is None:
        return MenuResult("back", None)
    return result


def select_menu(
    rows: list[MenuRow],
    *,
    title: str | None = None,
    summary: str | None = None,
    instruction: str | None = None,
    actions: dict[str, str] | None = None,
    start_index: int = 0,
) -> MenuResult:
    """Run an inline highlight-bar selector and return a :class:`MenuResult`.

    Navigation is Up/Down or ``k``/``j`` (wrapping). ``Enter`` selects the
    highlighted row (``"select"``); ``q`` returns ``"quit"``; ``b`` or ``Esc``
    return ``"back"``. Each key in ``actions`` maps to a custom action name
    returned for the highlighted row, e.g. ``{"e": "edit", "s-right": "toggle"}``.
    Reserved keys (arrows, ``k``/``j``, ``Enter``, ``q``, ``b``, ``Esc``) should
    not be reused as action keys.

    Assumes :func:`interactive_select_available` is true; callers use the plain
    numbered menu otherwise. The application renders inline (not full screen),
    so it erases itself on exit and preserves scrollback.
    """
    def render(selected_index: int) -> FormattedText:
        fragments: list[tuple[str, str]] = []
        if not rows:
            fragments.append(("class:dim", "  (no tasks)\n"))
        for i, row in enumerate(rows):
            selected = i == selected_index
            cursor = "▶ " if selected else "  "
            if selected:
                bar = _selected_bar(row.status)
                line = f"{cursor}{row.number:>2}  {row.status:<6}  {row.text}\n"
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append((f"class:{bar}", line))
            else:
                fragments.append(("", f"{cursor}{row.number:>2}  "))
                fragments.append((_status_class(row.status), f"{row.status:<6}"))
                fragments.append(("", f"  {row.text}\n"))
        return FormattedText(fragments)

    return _run_selector(
        len(rows),
        render,
        title=title,
        summary=summary,
        instruction=instruction,
        actions=actions,
        start_index=start_index,
    )


def select_project_menu(
    rows: list[ProjectRow],
    *,
    title: str | None = None,
    summary: str | None = None,
    instruction: str | None = None,
    start_index: int = 0,
) -> MenuResult:
    """Run an inline highlight-bar selector for project rows."""
    def render(selected_index: int) -> FormattedText:
        fragments: list[tuple[str, str]] = []
        if not rows:
            fragments.append(("class:dim", "  (no projects)\n"))
        for i, row in enumerate(rows):
            selected = i == selected_index
            cursor = "▶ " if selected else "  "
            if selected:
                line = f"{cursor}{row.number:>2}  {row.name:<12}  {row.org_file}\n"
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:selected.project", line))
            else:
                fragments.append(("", f"{cursor}{row.number:>2}  "))
                fragments.append(("class:project.name", f"{row.name:<12}"))
                fragments.append(("", f"  {row.org_file}\n"))
        return FormattedText(fragments)

    return _run_selector(
        len(rows),
        render,
        title=title,
        summary=summary,
        instruction=instruction,
        start_index=start_index,
    )


def count_statuses(rows: list[MenuRow]) -> tuple[int, int, int]:
    todo = sum(1 for row in rows if row.status == "TODO")
    done = sum(1 for row in rows if row.status in {"DONE", "SUPERSEDED"})
    total = todo + done
    return todo, done, total


def print_task_dashboard(title: str, source: Path, rows: list[MenuRow]) -> None:
    todo, done, total = count_statuses(rows)
    print()
    print(f"ortask — reading {source}")
    print(f"Open: {todo}  Done: {done}  Total: {total}")
    if RICH_CONSOLE is not None and Table is not None:
        table = Table(title=title)
        table.add_column("#", justify="right")
        table.add_column("Status")
        table.add_column("Task")
        for row in rows:
            if row.status == "TODO":
                rendered_status = "[yellow]TODO[/]"
            elif row.status == "DONE":
                rendered_status = "[green]DONE[/]"
            else:
                rendered_status = f"[dim]{row.status}[/]"
            table.add_row(str(row.number), rendered_status, row.text)
        RICH_CONSOLE.print(table)
        if not rows:
            RICH_CONSOLE.print("[dim](no items)[/]")
        return

    print(title)
    print("  #  Status  Task")
    for row in rows:
        print(f"  {row.number:>2}  {row.status:<6}  {row.text}")
    if not rows:
        print("  (no items)")


def print_project_dashboard(title: str, source: Path, rows: list[ProjectRow]) -> None:
    print()
    print(f"Projects in {source}")
    if RICH_CONSOLE is not None and Table is not None:
        table = Table(title=title)
        table.add_column("#", justify="right")
        table.add_column("Project")
        table.add_column("Task file")
        for row in rows:
            table.add_row(str(row.number), row.name, row.org_file)
        RICH_CONSOLE.print(table)
        if not rows:
            RICH_CONSOLE.print("[dim](no project org files found)[/]")
        return

    print(title)
    print("  #  Project  Task file")
    for row in rows:
        print(f"  {row.number:>2}  {row.name:<8}  {row.org_file}")
    if not rows:
        print("  (no project org files found)")
