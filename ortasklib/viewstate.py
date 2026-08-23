"""What an interactive list shows, and in what order.

One model behind two controls: the quick-toggle keys (``C-t`` filter, ``s``
sort) and the ``v`` view screen. A surface declares the positions it offers, so
neither control has to know which list it is driving and the screen never
presents an option the surface cannot honor.

Pure: no I/O, no ``prompt_toolkit``, no file reading. ortask's task-state names
live here because they are ortask policy — which is exactly what keeps this
module out of ``orglib``. See the sort-and-filter section of
``docs/interactive.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping, Sequence, TypeVar

from . import core

T = TypeVar("T")

FILTER_ALL = "all"
FILTER_TODO = "todo"
FILTER_DONE = "done"
FILTER_OPEN = "open"

SORT_FILE = "file"
SORT_PRIORITY = "priority"
SORT_ALPHABETICAL = "alphabetical"
SORT_MODIFIED = "modified"

TASK_FILTERS = (FILTER_ALL, FILTER_TODO, FILTER_DONE)
PROJECT_FILTERS = (FILTER_ALL, FILTER_OPEN)

#: ``t0039.5`` adds sibling-scoped orders here. Until then the task list has a
#: sort axis with one position, which is not a choice and so is not offered.
TASK_SORTS = (SORT_FILE,)
PROJECT_SORTS = (SORT_PRIORITY, SORT_ALPHABETICAL, SORT_MODIFIED)

_TASK_FILTER_LABELS = {
    FILTER_ALL: "all",
    FILTER_TODO: "TODO",
    # The third position matches every terminal state, so it cannot be called
    # DONE alone: a MOOT task appears here too.
    FILTER_DONE: "DONE+MOOT",
}
_PROJECT_FILTER_LABELS = {FILTER_ALL: "", FILTER_OPEN: "open only"}
_TASK_SORT_LABELS = {SORT_FILE: ""}
_PROJECT_SORT_LABELS = {
    SORT_PRIORITY: "Priority",
    SORT_ALPHABETICAL: "Alphabetical",
    SORT_MODIFIED: "Modified",
}


def next_position(positions: Sequence[str], current: str) -> str:
    """The next position on one axis, wrapping."""
    try:
        index = positions.index(current)
    except ValueError as exc:
        raise ValueError(f"unknown view position: {current}") from exc
    return positions[(index + 1) % len(positions)]


@dataclass(frozen=True)
class ViewAxes:
    """The positions one surface offers on each axis, and what to call them.

    An axis with a single position is not a choice: its key is not offered and
    it stays out of the badge.
    """

    filters: tuple[str, ...]
    sorts: tuple[str, ...]
    filter_labels: Mapping[str, str]
    sort_labels: Mapping[str, str]
    #: Appended to the sort label in the badge, so ``ptui`` can read
    #: "Priority sort" where the task list would only ever say "all".
    sort_noun: str = ""

    @property
    def cycles_filter(self) -> bool:
        return len(self.filters) > 1

    @property
    def cycles_sort(self) -> bool:
        return len(self.sorts) > 1

    def initial(
        self, filter: str | None = None, sort: str | None = None
    ) -> "ViewState":
        """The starting view, defaulting to the first position on each axis."""
        return ViewState(
            self,
            filter if filter is not None else self.filters[0],
            sort if sort is not None else self.sorts[0],
        )


TASK_VIEW_AXES = ViewAxes(
    TASK_FILTERS, TASK_SORTS, _TASK_FILTER_LABELS, _TASK_SORT_LABELS
)
PROJECT_VIEW_AXES = ViewAxes(
    PROJECT_FILTERS,
    PROJECT_SORTS,
    _PROJECT_FILTER_LABELS,
    _PROJECT_SORT_LABELS,
    sort_noun=" sort",
)


@dataclass(frozen=True)
class ViewState:
    """One surface's current filter, order, and direction.

    Immutable: every control returns a new state, so a view transition can
    never half-apply and a caller can compare against what it last rendered.
    """

    axes: ViewAxes
    filter: str
    sort: str
    reverse: bool = False

    def __post_init__(self) -> None:
        if self.filter not in self.axes.filters:
            raise ValueError(f"unknown filter position: {self.filter}")
        if self.sort not in self.axes.sorts:
            raise ValueError(f"unknown sort position: {self.sort}")

    def with_filter(self, position: str) -> "ViewState":
        return replace(self, filter=position)

    def with_sort(self, position: str) -> "ViewState":
        return replace(self, sort=position)

    def with_reverse(self, reverse: bool) -> "ViewState":
        return replace(self, reverse=bool(reverse))

    def next_filter(self) -> "ViewState":
        return self.with_filter(next_position(self.axes.filters, self.filter))

    def next_sort(self) -> "ViewState":
        return self.with_sort(next_position(self.axes.sorts, self.sort))

    def toggle_reverse(self) -> "ViewState":
        return self.with_reverse(not self.reverse)

    @property
    def filter_label(self) -> str:
        return self.axes.filter_labels.get(self.filter, self.filter)

    @property
    def sort_label(self) -> str:
        return self.axes.sort_labels.get(self.sort, self.sort)

    def badge(self) -> str:
        """What is shown, then how it is ordered — omitting axes with no choice.

        Always present in the instruction line rather than only when the view
        is non-default: a reader who cannot see why a row is missing has no way
        to get it back.
        """
        parts: list[str] = []
        if self.axes.cycles_filter and self.filter_label:
            parts.append(self.filter_label)
        if self.axes.cycles_sort and self.sort_label:
            arrow = " ↓" if self.reverse else ""
            parts.append(f"{self.sort_label}{self.axes.sort_noun}{arrow}")
        return " · ".join(parts)


def order_by(
    items: Iterable[T],
    *,
    primary: Callable[[T], object],
    tiebreak: Callable[[T], object],
    unavailable: Callable[[T], object] | None = None,
    reverse: bool = False,
) -> list[T]:
    """Sort with only the primary axis inverted.

    Three stable passes, weakest key first. Inversion has to stay off the
    tie-break and off ``unavailable``: ``sorted(..., reverse=True)`` would flip
    both, so asking for the oldest project first would also hoist every project
    whose task file cannot be read to the top of the list.
    """
    ordered = sorted(items, key=tiebreak)
    ordered.sort(key=primary, reverse=reverse)
    if unavailable is not None:
        ordered.sort(key=unavailable)
    return ordered


def task_state_matches(state: str, position: str) -> bool:
    """Whether one task state belongs in a filtered task list."""
    if position == FILTER_TODO:
        return state == "TODO"
    if position == FILTER_DONE:
        return state in core.TERMINAL_STATES
    if position == FILTER_ALL:
        return True
    raise ValueError(f"unknown filter position: {position}")
