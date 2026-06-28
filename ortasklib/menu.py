"""Shared menu rendering helpers for interactive ortask tools."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional interactive dependency
    Console = None
    Table = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.output.defaults import create_output
    from prompt_toolkit.styles import Style
except ImportError:  # pragma: no cover - optional interactive dependency
    PromptSession = None
    FormattedText = None
    KeyBindings = None
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


def count_statuses(rows: list[MenuRow]) -> tuple[int, int, int]:
    todo = sum(1 for row in rows if row.status == "TODO")
    done = sum(1 for row in rows if row.status == "DONE")
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
