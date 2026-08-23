"""What an interactive list shows, and in what order.

One model behind two controls: the quick-toggle keys (``C-t`` filter, ``s``
sort) and the ``v`` view screen. A surface declares the positions it offers, so
neither control has to know which list it is driving, and the screen never
presents an option the surface cannot honor.

Generic on purpose. This module imports nothing but the standard library — no
Org, no task states, no registry, no ``prompt_toolkit``, not even
``ortasklib.core``. Task predicates and task axes live beside task
presentation; project axes live beside project presentation. Keeping the state
machine free of both is what lets one screen drive either list.

See the sort-and-filter section of ``docs/interactive.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping, Sequence, TypeVar

T = TypeVar("T")

AXIS_FILTER = "filter"
AXIS_SORT = "sort"
AXIS_DIRECTION = "direction"

DIRECTION_FORWARD = "forward"
DIRECTION_REVERSE = "reverse"
DIRECTIONS = (DIRECTION_FORWARD, DIRECTION_REVERSE)

#: Shown for the direction of a sort that has no meaningful opposite.
NOT_REVERSIBLE = "n/a"


def next_position(positions: Sequence[str], current: str) -> str:
    """The next position on one axis, wrapping."""
    try:
        index = positions.index(current)
    except ValueError as exc:
        raise ValueError(f"unknown view position: {current}") from exc
    return positions[(index + 1) % len(positions)]


@dataclass(frozen=True)
class AxisChoice:
    """One axis as the ``v`` screen shows it: positions, spellings, current.

    ``label_for`` is a callable rather than a mapping because a direction's
    words depend on which sort is selected: the same position reads "A-Z" under
    one order and "newest first" under another.
    """

    key: str
    label: str
    options: tuple[str, ...]
    label_for: Callable[[str], str]
    value: str


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
    #: Sort position -> (forward words, reverse words). A sort absent from this
    #: mapping cannot be reversed, which is how file order stays intact: the
    #: task tree depends on it, so no control may invert it.
    directions: Mapping[str, tuple[str, str]] = None  # type: ignore[assignment]
    #: Appended to the sort label in the badge, so ``ptui`` can read
    #: "Priority sort" where the task list would only ever say "all".
    sort_noun: str = ""

    def __post_init__(self) -> None:
        if self.directions is None:
            object.__setattr__(self, "directions", {})

    @property
    def cycles_filter(self) -> bool:
        return len(self.filters) > 1

    @property
    def cycles_sort(self) -> bool:
        return len(self.sorts) > 1

    @property
    def has_directions(self) -> bool:
        return any(sort in self.directions for sort in self.sorts)

    def reversible(self, sort: str) -> bool:
        return sort in self.directions

    def direction_labels(self, sort: str) -> tuple[str, str]:
        return self.directions.get(sort, (NOT_REVERSIBLE, NOT_REVERSIBLE))

    def initial(
        self, filter: str | None = None, sort: str | None = None
    ) -> "ViewState":
        """The starting view, defaulting to the first position on each axis."""
        return ViewState(
            self,
            filter if filter is not None else self.filters[0],
            sort if sort is not None else self.sorts[0],
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
        if self.reverse and not self.axes.reversible(self.sort):
            raise ValueError(f"{self.sort} order cannot be reversed")

    @property
    def reversible(self) -> bool:
        return self.axes.reversible(self.sort)

    def with_filter(self, position: str) -> "ViewState":
        return replace(self, filter=position)

    def with_sort(self, position: str) -> "ViewState":
        """Change the order, dropping a direction the new order cannot hold."""
        keep = self.reverse and self.axes.reversible(position)
        return replace(self, sort=position, reverse=keep)

    def with_reverse(self, reverse: bool) -> "ViewState":
        if reverse and not self.reversible:
            return self
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

    @property
    def direction(self) -> str:
        return DIRECTION_REVERSE if self.reverse else DIRECTION_FORWARD

    @property
    def direction_label(self) -> str:
        forward, backward = self.axes.direction_labels(self.sort)
        return backward if self.reverse else forward

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
            order = f"{self.sort_label}{self.axes.sort_noun}"
            if self.reverse:
                order = f"{order} ({self.direction_label})"
            parts.append(order)
        return " · ".join(parts)

    def choices(self) -> tuple[AxisChoice, ...]:
        """The axes this surface offers, for the ``v`` screen.

        An axis with one position is not a choice and is left out, which is why
        the screen never shows a control that cannot do anything. The direction
        axis is offered whenever *some* order can be reversed; under an order
        that cannot, it reads as unavailable rather than vanishing, so the
        field list does not reshuffle underneath the cursor.
        """
        offered: list[AxisChoice] = []
        if self.axes.cycles_filter:
            offered.append(
                AxisChoice(
                    AXIS_FILTER,
                    "Show",
                    self.axes.filters,
                    lambda position: self.axes.filter_labels.get(position)
                    or position,
                    self.filter,
                )
            )
        if self.axes.cycles_sort:
            offered.append(
                AxisChoice(
                    AXIS_SORT,
                    "Order",
                    self.axes.sorts,
                    lambda position: self.axes.sort_labels.get(position)
                    or position,
                    self.sort,
                )
            )
        if self.axes.has_directions:
            forward, backward = self.axes.direction_labels(self.sort)
            offered.append(
                AxisChoice(
                    AXIS_DIRECTION,
                    "Direction",
                    DIRECTIONS,
                    lambda position: backward
                    if position == DIRECTION_REVERSE
                    else forward,
                    self.direction,
                )
            )
        return tuple(offered)

    def with_choice(self, axis: str, value: str) -> "ViewState":
        """Apply one screen choice, by axis name."""
        if axis == AXIS_FILTER:
            return self.with_filter(value)
        if axis == AXIS_SORT:
            return self.with_sort(value)
        if axis == AXIS_DIRECTION:
            return self.with_reverse(value == DIRECTION_REVERSE)
        raise ValueError(f"unknown view axis: {axis}")


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
