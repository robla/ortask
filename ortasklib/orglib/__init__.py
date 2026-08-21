"""orglib — the boundary between ortask and whatever parses its Org files.

The point of this package is that callers stop naming an implementation. Today
there is exactly one backend, the bespoke stdlib parser in ``core``, and
``parse()`` returns a document backed by it. If a second backend is added later
(see ``docs/orglib.md``), callers that already went through here do not change.

Two contract rules, both deliberate:

- **Text in, text out.** ``parse()`` takes a string and ``render()`` returns a
  string. File discovery, symlink resolution, and atomic writes stay with the
  caller, so a backend cannot impose its own I/O policy.
- **Backend-neutral values.** ``tasks()`` returns ``core.TodoItem``, never a
  node object belonging to some parsing library.

This is intentionally small. It covers the read path only, because that is what
the callers routed through it so far actually need. Mutation, backend selection,
and a fidelity declaration are described in ``docs/orglib.md`` and should be
added when a caller needs them, not in advance.
"""

from __future__ import annotations

from .. import core
from ..core import TodoItem

__all__ = ["Document", "parse"]


class Document:
    """One parsed Org document.

    Holds the source text and answers questions about it. Instances are
    read-only: there is no mutation API yet, so ``render()`` always reproduces
    the source exactly.
    """

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    def tasks(self) -> list[TodoItem]:
        """Task headings, scoped to ``* Tasks`` when the document has one."""
        return core.parse_org(self._text)

    def render(self) -> str:
        """The document as text.

        For a read-only document this is the source, byte for byte. That
        equality is the fidelity property ortask relies on, and it is worth
        asserting against any future backend rather than assuming it.
        """
        return self._text


def parse(text: str) -> Document:
    """Parse Org text into a :class:`Document`."""
    return Document(text)
