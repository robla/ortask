"""The ``v`` view options screen: one prompt_toolkit form over view state.

Deliberately its own module. ``viewstate`` stays stdlib-only, ``menu`` stays the
general selector, and the glue that turns axes into focusable fields lives here
where neither has to grow a branch for it. Provisional: if a second kind of
settings screen appears, this is where the shared shape would be found.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import menu, viewstate


@dataclass(frozen=True)
class ChoiceField:
    """One labeled ring of positions in a :func:`choice_screen`.

    Generic on purpose: the field knows its positions and how to spell them,
    and nothing about what choosing one means. ``label_for`` is a callable so a
    field's words can depend on another field's value.
    """

    key: str
    label: str
    options: tuple[str, ...]
    label_for: "Callable[[str], str] | None" = None

    def display(self, value: str) -> str:
        shown = self.label_for(value) if self.label_for is not None else value
        return shown or value

    def cycled(self, value: str, direction: int) -> str:
        index = self.options.index(value)
        return self.options[(index + direction) % len(self.options)]


def choice_screen(
    *,
    title: str,
    fields: "list[ChoiceField] | tuple[ChoiceField, ...]",
    values: dict[str, str],
    on_change: Callable[["menu.InlineMenuSession", ChoiceField, str], None],
    on_save: Callable[["menu.InlineMenuSession"], None],
    summary: Callable[[], str] | None = None,
    title_right: str = "",
    instruction: str = (
        "↑↓ field · Enter change · ←/→ choose · C-s save · C-g help · Esc back"
    ),
    editing_instruction: str = (
        "CHANGING · ←/→ choose · Esc finish · C-s save · C-g help"
    ),
    help_entries: "list[tuple[str, str]] | None" = None,
    back_help: str = "Return to the list",
    save_help: str = "Save the file",
) -> menu.WorkspaceView:
    """A stack of choice fields, each applied the moment it changes.

    There is no apply key. ``values`` is mutated in place and ``on_change``
    runs immediately, so ``C-s`` keeps the one meaning it has everywhere else
    in the suite — write the file — instead of being redefined per screen.
    """
    if not fields:
        raise ValueError("a choice screen needs at least one field")
    width = max(len(one.label) for one in fields)
    workspace: menu.WorkspaceView | None = None

    def field_row(index: int, spec: ChoiceField):
        def render():
            value = spec.display(values[spec.key])
            focused = workspace is not None and workspace.focused_index == index
            editing = (
                workspace is not None and workspace.editing_index == index
            )
            arrows = "◀ %s ▶" % value if editing else f"[{value}]"
            text = f" {spec.label:<{width}}  {arrows} "
            if focused:
                style = (
                    "class:field.editing" if editing else "class:choice.focused"
                )
                fragments = [(style, text)]
            else:
                fragments = [
                    ("class:field.label", f" {spec.label:<{width}}  "),
                    ("", f"{arrows} "),
                ]
            return menu.FormattedText([("[SetCursorPosition]", ""), *fragments])

        return menu.Window(
            menu.FormattedTextControl(render, focusable=True),
            height=1,
            always_hide_cursor=True,
        )

    controls = [field_row(index, spec) for index, spec in enumerate(fields)]

    def change_choice(
        session: "menu.InlineMenuSession", focus_index: int, direction: int
    ) -> None:
        spec = fields[focus_index]
        values[spec.key] = spec.cycled(values[spec.key], direction)
        on_change(session, spec, values[spec.key])

    every_field = frozenset(range(len(fields)))
    workspace = menu.WorkspaceView(
        container=menu.HSplit(controls),
        focus_targets=controls,
        on_save=on_save,
        title=title,
        title_right=title_right,
        summary=summary() if summary is not None else "",
        instruction=instruction,
        editing_instruction=editing_instruction,
        help_entries=help_entries
        or [
            ("Up/Down", "Move among fields"),
            ("Enter (field)", "Begin changing the focused field"),
            ("Left/Right (changing)", "Choose the previous or next value"),
            ("Enter/Esc (changing)", "Finish changing the field"),
            ("Ctrl-S", save_help),
            ("Esc", back_help),
            ("C-g", "Show or close this help"),
        ],
        edit_focus_indices=every_field,
        choice_focus_indices=every_field,
        on_choice_change=change_choice,
        status_text=summary,
    )
    return workspace


def view_options_screen(
    current: viewstate.ViewState,
    apply: "Callable[[menu.InlineMenuSession, viewstate.ViewState], None]",
    *,
    title: str,
    save: "Callable[[menu.InlineMenuSession], None]",
    save_help: str,
    title_right: str = "",
) -> menu.WorkspaceView:
    """The ``v`` screen, shared by the task list and the project navigator.

    Every change applies the moment it is made — there is nothing to commit,
    because view state is session-only and never written to disk. ``C-s``
    therefore keeps meaning what it means in every other context: save the
    file. Redefining it here would punish the one reflex a user has.
    """
    live = {"view": current}
    choices = current.choices()
    def live_label(key: str, value: str) -> str:
        """Spell a position the way the *current* view would spell it.

        Field words can depend on another field: a direction reads "A-Z" under
        one order and "newest first" under another. Looking the label up
        against the live state keeps the screen from showing the words that
        were true when it opened.
        """
        for choice in live["view"].choices():
            if choice.key == key:
                return choice.label_for(value)
        return value

    fields = [
        ChoiceField(
            choice.key,
            choice.label,
            choice.options,
            lambda value, key=choice.key: live_label(key, value),
        )
        for choice in choices
    ]
    values = {choice.key: choice.value for choice in choices}

    def summary() -> str:
        return f"Showing: {live['view'].badge()}"

    def changed(
        session: menu.InlineMenuSession,
        spec: ChoiceField,
        value: str,
    ) -> None:
        live["view"] = live["view"].with_choice(spec.key, value)
        # The state is the authority, not the field. A choice it refuses — a
        # direction under an order that has none — must not leave the form
        # showing a setting that is not in effect.
        for choice in live["view"].choices():
            values[choice.key] = choice.value
        screen.summary = summary()
        apply(session, live["view"])

    screen = choice_screen(
        title=title,
        fields=fields,
        values=values,
        on_change=changed,
        on_save=save,
        summary=summary,
        title_right=title_right,
        back_help="Return to the list",
        save_help=save_help,
    )
    return screen
