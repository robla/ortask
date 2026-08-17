"""Shared menu rendering helpers for interactive ortask tools."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .core import TERMINAL_STATES

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional interactive dependency
    Console = None
    Table = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application, run_in_terminal
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        DynamicContainer,
        FormattedTextControl,
        HSplit,
        Layout,
        ScrollOffsets,
        Window,
    )
    from prompt_toolkit.output.defaults import create_output
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea
except ImportError:  # pragma: no cover - optional interactive dependency
    PromptSession = None
    Application = None
    run_in_terminal = None
    Document = None
    Condition = None
    FormattedText = None
    KeyBindings = None
    DynamicContainer = None
    FormattedTextControl = None
    HSplit = None
    Layout = None
    ScrollOffsets = None
    Window = None
    create_output = None
    Style = None
    TextArea = None


@dataclass(frozen=True)
class MenuRow:
    number: int
    status: str
    text: str
    tree_depth: int = 0
    disclosure: str | None = None


@dataclass(frozen=True)
class ProjectRow:
    number: int
    name: str
    org_file: str


@dataclass(frozen=True)
class MenuResult:
    """Outcome of a :func:`select_menu` interaction.

    ``action`` is ``"select"``, ``"back"``, or a custom action name supplied by
    the caller (e.g. ``"edit"``, ``"toggle"``). ``index`` is the 0-based
    highlighted row index, or ``None`` for ``"back"``.
    """

    action: str
    index: int | None


@dataclass(frozen=True)
class MenuAction:
    """One selector action, including its discoverable help description."""

    name: str
    key_label: str
    description: str


@dataclass
class MenuView:
    """One view rendered inside a persistent :class:`InlineMenuSession`."""

    rows: list[MenuRow]
    on_result: Callable[["InlineMenuSession", MenuResult], None]
    title: str = ""
    summary: str = ""
    preamble: str = ""
    instruction: str = ""
    empty_text: str = "(no tasks)"
    actions: dict[str, MenuAction] = field(default_factory=dict)
    select_help: str = "Open the highlighted item"
    back_help: str = "Back one level (exit at the top level)"
    selected_index: int = 0
    on_resume: Callable[["InlineMenuSession"], None] | None = None
    on_back: Callable[["InlineMenuSession"], bool] | None = None

    def clamp_selection(self) -> None:
        if not self.rows:
            self.selected_index = 0
            return
        self.selected_index = min(max(self.selected_index, 0), len(self.rows) - 1)


@dataclass
class TextInputView:
    """One focused single-line input view in an :class:`InlineMenuSession`."""

    text: str
    on_accept: Callable[["InlineMenuSession", str], None]
    title: str = ""
    summary: str = ""
    prompt: str = "Text> "
    instruction: str = "Enter accept · Esc cancel · C-g help"
    accept_help: str = "Accept the edited text"
    cancel_help: str = "Cancel without changing the task"


@dataclass
class MultilineInputView:
    """One focused multiline input view in an :class:`InlineMenuSession`."""

    text: str
    on_accept: Callable[["InlineMenuSession", str], None]
    title: str = ""
    summary: str = ""
    instruction: str = "Enter newline · Ctrl-S apply · Esc cancel · C-g help"
    accept_help: str = "Apply the edited text"
    cancel_help: str = "Cancel without changing the task"


@dataclass
class WorkspaceView:
    """A persistent view composed from application-owned focusable controls."""

    container: Any
    focus_targets: list[Any]
    on_save: Callable[["InlineMenuSession"], None]
    title: str = ""
    summary: str = ""
    instruction: str = "Tab fields · Ctrl-S save · Esc back · C-g help"
    help_entries: list[tuple[str, str]] = field(default_factory=list)
    enter_moves_focus: frozenset[int] = frozenset()
    focused_index: int = 0
    is_dirty: Callable[[], bool] | None = None
    on_back: Callable[["InlineMenuSession"], bool] | None = None

    def clamp_focus(self) -> None:
        if not self.focus_targets:
            raise ValueError("workspace view requires at least one focus target")
        self.focused_index %= len(self.focus_targets)


InlineView = MenuView | TextInputView | MultilineInputView | WorkspaceView


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
            "help.heading": "bold",
            "help.key": "ansicyan bold",
            "field.label": "ansicyan bold",
            "input.prompt": "ansicyan bold",
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
        and DynamicContainer is not None
        and HSplit is not None
        and Window is not None
        and Layout is not None
        and ScrollOffsets is not None
        and Condition is not None
        and TextArea is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _status_class(status: str) -> str:
    if status == "TODO":
        return "class:status.todo"
    if status == "DONE":
        return "class:status.done"
    if status == "PROJECT":
        return "class:project.name"
    return "class:status.other"


def _selected_bar(status: str) -> str:
    """Selection-bar class for a highlighted row, keyed to its row status.

    Mirrors :func:`_status_class`: TODO rows get the gold bar, DONE rows the
    green bar, PROJECT rows cyan, and anything else the neutral gray bar. The
    bar color (plus black text) carries the row type on the selection, so the
    per-label foreground is dropped there.
    """
    if status == "TODO":
        return "selected.todo"
    if status == "DONE":
        return "selected.done"
    if status == "PROJECT":
        return "selected.project"
    return "selected.other"


def _selector_help(
    actions: dict[str, MenuAction],
    *,
    select_help: str,
    back_help: str,
) -> FormattedText:
    entries = [
        ("↑/↓, j/k", "Move the highlight"),
        ("Enter", select_help),
    ]
    seen_actions: set[tuple[str, str]] = set()
    for action in actions.values():
        entry = (action.key_label, action.description)
        if entry not in seen_actions:
            entries.append(entry)
            seen_actions.add(entry)
    entries.extend(
        [
            ("C-g", "Show or close this help"),
            ("Esc/b/q", back_help),
        ]
    )
    return _command_help(entries)


def _text_input_help(
    view: TextInputView | MultilineInputView,
) -> FormattedText:
    edit_help = "Edit the field"
    accept_key = "Enter"
    entries: list[tuple[str, str]] = []
    if isinstance(view, MultilineInputView):
        edit_help = "Edit the task body"
        accept_key = "Ctrl-S"
        entries.append(("Enter", "Insert a newline"))
    return _command_help(
        [
            ("Typing", edit_help),
            *entries,
            (accept_key, view.accept_help),
            ("Esc", view.cancel_help),
            ("C-g", "Show or close this help"),
        ]
    )


def _workspace_help(view: WorkspaceView) -> FormattedText:
    entries = view.help_entries or [
        ("Typing", "Edit the focused field"),
        ("Tab/Shift-Tab", "Move between fields"),
        ("Ctrl-S", "Save workspace edits"),
        ("Esc", "Return, warning first if edits are unsaved"),
        ("C-g", "Show or close this help"),
    ]
    return _command_help(entries)


def _command_help(entries: list[tuple[str, str]]) -> FormattedText:
    key_width = max(len(key) for key, _ in entries)
    fragments: list[tuple[str, str]] = [
        ("[SetCursorPosition]", ""),
        ("class:help.heading", "Interactive help\n"),
        ("class:dim", "Commands available in this menu\n\n"),
    ]
    for key, description in entries:
        fragments.append(("class:help.key", f"  {key:<{key_width}}"))
        fragments.append(("", f"  {description}\n"))
    fragments.append(
        ("class:dim", "\n  While help is open, C-g/Esc/b/q/Enter closes it.\n")
    )
    return FormattedText(fragments)


def _render_menu_rows(
    rows: list[MenuRow],
    selected_index: int,
    *,
    empty_text: str = "(no tasks)",
) -> FormattedText:
    fragments: list[tuple[str, str]] = []
    if not rows:
        fragments.append(("class:dim", f"  {empty_text}\n"))
    for index, row in enumerate(rows):
        selected = index == selected_index
        cursor = "▶ " if selected else "  "
        tree_prefix = ""
        if row.disclosure is not None:
            tree_prefix = f"{'  ' * row.tree_depth}{row.disclosure} "
        if selected:
            bar = _selected_bar(row.status)
            line = (
                f"{cursor}{row.number:>2}  {row.status:<6}  "
                f"{tree_prefix}{row.text}\n"
            )
            fragments.append(("[SetCursorPosition]", ""))
            fragments.append((f"class:{bar}", line))
        else:
            fragments.append(("", f"{cursor}{row.number:>2}  "))
            fragments.append((_status_class(row.status), f"{row.status:<6}"))
            fragments.append(("", f"  {tree_prefix}{row.text}\n"))
    return FormattedText(fragments)


class InlineMenuSession:
    """Run a stack of menu views in one bounded prompt_toolkit application."""

    DEFAULT_HEIGHT = 20
    MINIMUM_HEIGHT = 6
    RESERVED_TERMINAL_ROWS = 1

    def __init__(
        self,
        initial_view: InlineView,
        *,
        action_keys: Iterable[str] = (),
        height: int = DEFAULT_HEIGHT,
        final_message: str = "Session closed",
        input: Any = None,
        output: Any = None,
    ) -> None:
        if Application is None:
            raise RuntimeError("prompt_toolkit is required for interactive menus")

        if isinstance(initial_view, MenuView):
            initial_view.clamp_selection()
        elif isinstance(initial_view, WorkspaceView):
            initial_view.clamp_focus()
        self.views: list[InlineView] = [initial_view]
        self.requested_height = max(height, self.MINIMUM_HEIGHT)
        self.effective_height = self.requested_height
        self.help_visible = False
        self.message: str | None = None
        self.final_message = final_message
        self.error: str | None = None

        bindings = KeyBindings()
        menu_active = Condition(
            lambda: not self.help_visible
            and isinstance(self.current_view, MenuView)
        )
        singleline_input_active = Condition(
            lambda: not self.help_visible
            and isinstance(self.current_view, TextInputView)
        )
        multiline_input_active = Condition(
            lambda: not self.help_visible
            and isinstance(self.current_view, MultilineInputView)
        )
        workspace_active = Condition(
            lambda: not self.help_visible
            and isinstance(self.current_view, WorkspaceView)
        )
        workspace_enter_active = Condition(
            lambda: not self.help_visible
            and isinstance(self.current_view, WorkspaceView)
            and self.current_view.focused_index
            in self.current_view.enter_moves_focus
        )
        input_active = Condition(
            lambda: not self.help_visible
            and isinstance(
                self.current_view,
                (TextInputView, MultilineInputView),
            )
        )
        help_active = Condition(lambda: self.help_visible)

        def move(delta: int) -> None:
            view = self.current_view
            assert isinstance(view, MenuView)
            if view.rows:
                view.selected_index = (
                    view.selected_index + delta
                ) % len(view.rows)

        @bindings.add("up", filter=menu_active)
        @bindings.add("k", filter=menu_active)
        def move_up(_event) -> None:
            move(-1)

        @bindings.add("down", filter=menu_active)
        @bindings.add("j", filter=menu_active)
        def move_down(_event) -> None:
            move(1)

        @bindings.add("enter", filter=menu_active)
        def select(_event) -> None:
            view = self.current_view
            assert isinstance(view, MenuView)
            index = view.selected_index if view.rows else None
            self._dispatch(MenuResult("select", index))

        @bindings.add("q", filter=menu_active)
        @bindings.add("b", filter=menu_active)
        @bindings.add("escape", filter=menu_active, eager=True)
        def back(_event) -> None:
            self.pop_view()

        @bindings.add("enter", filter=singleline_input_active, eager=True)
        def accept_text(_event) -> None:
            view = self.current_view
            assert isinstance(view, TextInputView)
            view.on_accept(self, self.text_area.text)

        @bindings.add("c-s", filter=multiline_input_active, eager=True)
        def accept_multiline_text(_event) -> None:
            view = self.current_view
            assert isinstance(view, MultilineInputView)
            view.on_accept(self, self.multiline_text_area.text)

        @bindings.add("escape", filter=input_active, eager=True)
        def cancel_text(_event) -> None:
            self.pop_view()

        def move_workspace_focus(delta: int) -> None:
            view = self.current_view
            assert isinstance(view, WorkspaceView)
            view.focused_index = (
                view.focused_index + delta
            ) % len(view.focus_targets)
            self._focus_current_view()

        @bindings.add("c-i", filter=workspace_active, eager=True)
        def next_workspace_field(_event) -> None:
            move_workspace_focus(1)

        @bindings.add("s-tab", filter=workspace_active, eager=True)
        def previous_workspace_field(_event) -> None:
            move_workspace_focus(-1)

        @bindings.add("enter", filter=workspace_enter_active, eager=True)
        def enter_workspace_field(_event) -> None:
            move_workspace_focus(1)

        @bindings.add("c-s", filter=workspace_active, eager=True)
        def save_workspace(_event) -> None:
            view = self.current_view
            assert isinstance(view, WorkspaceView)
            view.on_save(self)

        @bindings.add("escape", filter=workspace_active, eager=True)
        def leave_workspace(_event) -> None:
            self.pop_view()

        @bindings.add("q", filter=help_active, eager=True)
        @bindings.add("b", filter=help_active, eager=True)
        @bindings.add("escape", filter=help_active, eager=True)
        @bindings.add("enter", filter=help_active, eager=True)
        def close_help(_event) -> None:
            self.help_visible = False
            self._focus_current_view()
            self.application.invalidate()

        @bindings.add("c-g", eager=True)
        def help_view(_event) -> None:
            self.help_visible = not self.help_visible
            self._focus_current_view()
            self.application.invalidate()

        def make_action(key: str):
            def handler(_event) -> None:
                view = self.current_view
                assert isinstance(view, MenuView)
                action = view.actions.get(key)
                if action is None:
                    return
                index = (
                    view.selected_index
                    if view.rows
                    else None
                )
                self._dispatch(MenuResult(action.name, index))

            return handler

        for key in dict.fromkeys(action_keys):
            bindings.add(key, filter=menu_active)(make_action(key))

        body_control = FormattedTextControl(
            self._render_body,
            focusable=True,
            show_cursor=False,
        )
        body_window = Window(
            body_control,
            always_hide_cursor=True,
            scroll_offsets=ScrollOffsets(top=1, bottom=1),
            wrap_lines=False,
        )
        self.body_control = body_control
        self.body_window = body_window
        self.text_area = TextArea(
            multiline=False,
            wrap_lines=False,
            prompt=self._render_input_prompt,
        )
        self.text_area.buffer.on_text_changed += self._input_changed
        self.multiline_text_area = TextArea(
            multiline=True,
            wrap_lines=False,
            scrollbar=True,
        )
        self.multiline_text_area.buffer.on_text_changed += self._input_changed
        if isinstance(initial_view, (TextInputView, MultilineInputView)):
            self._load_input(initial_view)
        body = DynamicContainer(self._active_body)
        root = HSplit(
            [
                Window(
                    FormattedTextControl(self._render_header),
                    height=3,
                    always_hide_cursor=True,
                    wrap_lines=False,
                ),
                body,
                Window(
                    FormattedTextControl(self._render_footer),
                    height=2,
                    always_hide_cursor=True,
                    wrap_lines=False,
                ),
            ],
            height=lambda: self.effective_height,
        )
        focused_element = (
            self._input_area(initial_view)
            if isinstance(initial_view, (TextInputView, MultilineInputView))
            else (
                initial_view.focus_targets[initial_view.focused_index]
                if isinstance(initial_view, WorkspaceView)
                else body_control
            )
        )
        self.application = Application(
            layout=Layout(root, focused_element=focused_element),
            key_bindings=bindings,
            style=SELECT_STYLE,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            terminal_size_polling_interval=0.5,
            before_render=self._before_render,
            input=input,
            output=output,
        )

    @property
    def current_view(self) -> InlineView:
        return self.views[-1]

    def push_view(self, view: InlineView) -> None:
        if isinstance(view, MenuView):
            view.clamp_selection()
        elif isinstance(view, WorkspaceView):
            view.clamp_focus()
        self.views.append(view)
        self.help_visible = False
        self.message = None
        self._activate_current_view()
        self.application.invalidate()

    def replace_view(self, view: InlineView) -> None:
        if isinstance(view, MenuView):
            view.clamp_selection()
        elif isinstance(view, WorkspaceView):
            view.clamp_focus()
        self.views[-1] = view
        self.help_visible = False
        self._activate_current_view()
        self.application.invalidate()

    def pop_view(self, *, message: str | None = None) -> None:
        self.help_visible = False
        self.message = None
        active = self.current_view
        on_back = (
            active.on_back
            if isinstance(active, (MenuView, WorkspaceView))
            else None
        )
        if on_back is not None and not on_back(self):
            self.application.invalidate()
            return
        if message is not None:
            self.final_message = message
        if len(self.views) == 1:
            self.message = self.final_message
            self.application.erase_when_done = False
            self.application.exit(result=MenuResult("back", None))
            return
        self.views.pop()
        resumed = self.current_view
        if isinstance(resumed, MenuView) and resumed.on_resume is not None:
            resumed.on_resume(self)
        self.message = message
        self._activate_current_view()
        self.application.invalidate()

    def set_message(self, message: str | None) -> None:
        self.message = message
        self.application.invalidate()

    def set_outcome(self, message: str) -> None:
        """Record a factual final outcome and show it in the active footer."""
        self.final_message = message
        self.set_message(message)

    def suspend(
        self,
        func: Callable[[], Any],
        *,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Temporarily hand the terminal to ``func``, then repaint this view."""
        if run_in_terminal is None:
            self.set_message("prompt_toolkit terminal handoff is unavailable")
            return

        async def run() -> None:
            try:
                await run_in_terminal(func, in_executor=True)
            except OSError as exc:
                self.message = f"could not run external command: {exc}"
            else:
                if on_done is not None:
                    on_done()
            self.application.invalidate()

        self.application.create_background_task(run())

    def run(self) -> MenuResult:
        try:
            result = self.application.run()
        except (KeyboardInterrupt, EOFError):
            return MenuResult("back", None)
        return result or MenuResult("back", None)

    def _dispatch(self, result: MenuResult) -> None:
        view = self.current_view
        assert isinstance(view, MenuView)
        view.on_result(self, result)
        if self.views and isinstance(self.current_view, MenuView):
            self.current_view.clamp_selection()
        if not self.application.is_done:
            self.application.invalidate()

    def _render_header(self) -> FormattedText:
        view = self.current_view
        return FormattedText(
            [
                ("class:title", view.title + "\n"),
                ("class:summary", view.summary + "\n"),
                ("", "\n"),
            ]
        )

    def _render_body(self) -> FormattedText:
        view = self.current_view
        if self.help_visible:
            if isinstance(view, (TextInputView, MultilineInputView)):
                return _text_input_help(view)
            if isinstance(view, WorkspaceView):
                return _workspace_help(view)
            return _selector_help(
                view.actions,
                select_help=view.select_help,
                back_help=view.back_help,
            )
        assert isinstance(view, MenuView)
        rows = _render_menu_rows(
            view.rows,
            view.selected_index,
            empty_text=view.empty_text,
        )
        if not view.preamble:
            return rows
        return FormattedText(
            [("", view.preamble.rstrip("\n") + "\n\n"), *list(rows)]
        )

    def _render_input_prompt(self) -> FormattedText:
        view = self.current_view
        prompt = view.prompt if isinstance(view, TextInputView) else ""
        return FormattedText([("class:input.prompt", prompt)])

    def _render_footer(self) -> FormattedText:
        if self.help_visible:
            instruction = "C-g/Esc/b/q/Enter close help"
        else:
            instruction = self.message or self.current_view.instruction
            view = self.current_view
            if (
                self.message is None
                and isinstance(view, WorkspaceView)
                and view.is_dirty is not None
                and view.is_dirty()
            ):
                instruction = f"UNSAVED · {instruction}"
        return FormattedText([("", "\n"), ("class:hint", instruction)])

    def _active_body(self):
        if self.help_visible or isinstance(self.current_view, MenuView):
            return self.body_window
        if isinstance(self.current_view, WorkspaceView):
            return self.current_view.container
        return self._input_area(self.current_view)

    def _input_changed(self, buffer) -> None:
        view = self.current_view
        if isinstance(view, (TextInputView, MultilineInputView)):
            view.text = buffer.text
            self.message = None

    def _input_area(self, view: TextInputView | MultilineInputView):
        if isinstance(view, MultilineInputView):
            return self.multiline_text_area
        return self.text_area

    def _load_input(self, view: TextInputView | MultilineInputView) -> None:
        self._input_area(view).buffer.set_document(
            Document(view.text, cursor_position=len(view.text)),
            bypass_readonly=True,
        )

    def _activate_current_view(self) -> None:
        view = self.current_view
        if isinstance(view, (TextInputView, MultilineInputView)):
            self._load_input(view)
        self._focus_current_view()

    def _focus_current_view(self) -> None:
        if self.help_visible or isinstance(self.current_view, MenuView):
            self.application.layout.focus(self.body_control)
        elif isinstance(self.current_view, WorkspaceView):
            self.current_view.clamp_focus()
            self.application.layout.focus(
                self.current_view.focus_targets[self.current_view.focused_index]
            )
        else:
            self.application.layout.focus(self._input_area(self.current_view))

    def _before_render(self, application) -> None:
        if application.is_done:
            return
        rows = application.output.get_size().rows
        minimum_rows = self.MINIMUM_HEIGHT + self.RESERVED_TERMINAL_ROWS
        if rows < minimum_rows:
            self.error = (
                f"terminal has {rows} rows; at least {minimum_rows} are required"
            )
            application.erase_when_done = True
            application.exit(result=MenuResult("back", None))
            return
        self.effective_height = min(
            self.requested_height,
            rows - self.RESERVED_TERMINAL_ROWS,
        )


def _run_selector(
    row_count: int,
    render: Callable[[int], FormattedText],
    *,
    title: str | None = None,
    summary: str | None = None,
    preamble: str | None = None,
    instruction: str | None = None,
    actions: dict[str, str | MenuAction] | None = None,
    start_index: int = 0,
    select_help: str = "Open the highlighted item",
    back_help: str = "Back one level (exit at the top level)",
) -> MenuResult:
    normalized_actions = {
        key: (
            action
            if isinstance(action, MenuAction)
            else MenuAction(action, key, f"Run {action.replace('_', ' ')}")
        )
        for key, action in (actions or {}).items()
    }
    state = {
        "index": min(max(start_index, 0), row_count - 1) if row_count else 0
    }
    help_state = {"visible": False}

    bindings = KeyBindings()

    def _move(delta: int) -> None:
        if row_count and not help_state["visible"]:
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
        if help_state["visible"]:
            help_state["visible"] = False
            return
        if row_count:
            event.app.exit(result=MenuResult("select", state["index"]))

    # ``escape`` is intentionally non-eager so arrow-key escape sequences are
    # not swallowed; prompt_toolkit disambiguates with its key timeout.
    @bindings.add("q")
    @bindings.add("b")
    @bindings.add("escape")
    def _back(event) -> None:
        if help_state["visible"]:
            help_state["visible"] = False
            return
        event.app.exit(result=MenuResult("back", None))

    @bindings.add("c-g", eager=True)
    def _help(event) -> None:
        help_state["visible"] = not help_state["visible"]

    def _make_action(action_name: str):
        def handler(event) -> None:
            if help_state["visible"]:
                return
            event.app.exit(
                result=MenuResult(action_name, state["index"] if row_count else None)
            )
        return handler

    for key, action in normalized_actions.items():
        bindings.add(key)(_make_action(action.name))

    def render_body() -> FormattedText:
        if help_state["visible"]:
            return _selector_help(
                normalized_actions,
                select_help=select_help,
                back_help=back_help,
            )
        rows = render(state["index"])
        if preamble:
            return FormattedText(
                [("", preamble.rstrip("\n") + "\n\n"), *list(rows)]
            )
        return rows

    body_control = FormattedTextControl(
        render_body, focusable=True, show_cursor=False
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
        def render_footer() -> FormattedText:
            text = (
                "C-g/Esc/b/q/Enter close help"
                if help_state["visible"]
                else instruction
            )
            return FormattedText([("", "\n"), ("class:hint", text)])

        containers.append(
            Window(
                FormattedTextControl(render_footer),
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
        return MenuResult("back", None)
    if result is None:
        return MenuResult("back", None)
    return result


def select_menu(
    rows: list[MenuRow],
    *,
    title: str | None = None,
    summary: str | None = None,
    preamble: str | None = None,
    instruction: str | None = None,
    actions: dict[str, str | MenuAction] | None = None,
    start_index: int = 0,
    select_help: str | None = None,
) -> MenuResult:
    """Run an inline highlight-bar selector and return a :class:`MenuResult`.

    Navigation is Up/Down or ``k``/``j`` (wrapping). ``Enter`` selects the
    highlighted row (``"select"``); ``C-g`` toggles contextual help; and ``q``,
    ``b``, or ``Esc`` return ``"back"``. Each key in ``actions`` maps to a custom
    action. A :class:`MenuAction` supplies the key label and description shown in
    help; bare action-name strings remain supported. Reserved navigation, back,
    and help keys should not be reused.

    Assumes :func:`interactive_select_available` is true; callers use the plain
    numbered menu otherwise. This compatibility API runs one short-lived inline
    application and retains its final frame. New multi-view workflows should
    use :class:`InlineMenuSession` instead.
    """
    def render(selected_index: int) -> FormattedText:
        return _render_menu_rows(rows, selected_index)

    return _run_selector(
        len(rows),
        render,
        title=title,
        summary=summary,
        preamble=preamble,
        instruction=instruction,
        actions=actions,
        start_index=start_index,
        select_help=select_help or "Open the highlighted task's details",
        back_help="Back one level (exit at the top level)",
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
        select_help="Open the highlighted project",
        back_help="Back one level (exit at the top level)",
    )


def count_statuses(rows: list[MenuRow]) -> tuple[int, int, int]:
    todo = sum(1 for row in rows if row.status == "TODO")
    done = sum(1 for row in rows if row.status in TERMINAL_STATES)
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
