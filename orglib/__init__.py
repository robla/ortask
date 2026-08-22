"""orglib — the boundary between ortask and whatever parses its Org files.

The point of this package is that callers stop naming an implementation. Today
there is exactly one backend, the bespoke stdlib parser in ``orglib.syntax``,
and ``parse()`` returns a document backed by it. If a second backend is added
later (see ``docs/orglib.md``), callers that already went through here do not
change.

Two contract rules, both deliberate:

- **Text in, text out.** ``parse()`` takes a string and ``render()`` returns a
  string. File discovery, symlink resolution, and atomic writes stay with the
  caller, so a backend cannot impose its own I/O policy.
- **Backend-neutral values.** ``tasks()`` returns ``orglib.TodoItem`` and
  ``directories()`` returns source spans and immutable values, never node
  objects belonging to some parsing library.

This is intentionally small. It covers the read path only, because that is what
the callers routed through it so far actually need. Mutation, backend selection,
and a fidelity declaration are described in ``docs/orglib.md`` and should be
added when a caller needs them, not in advance.

``orglib`` imports nothing outside the standard library. ``ortasklib`` depends
on it, not the other way round; ``ortasklib.core`` re-exports the names in
:mod:`orglib.syntax` so that callers predating the split keep working.
"""

from __future__ import annotations

# Boundary guard (t0038): generic Org regions and pure region merging may live
# here. File watching, auto-save, undo history, atomic I/O, and TUI policy may
# not; orglib remains text-in/text-out and independent of ortasklib.
from . import syntax
from .syntax import (
    DirectoriesSection,
    OrgStructureError,
    ProjectDirectories,
    ProjectSection,
    SourceSpan,
    TodoItem,
)

__all__ = [
    "DirectoriesSection",
    "Document",
    "OrgStructureError",
    "ProjectDirectories",
    "ProjectSection",
    "SourceSpan",
    "TodoItem",
    "parse",
]


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
        return syntax.parse_org(self._text)

    def directories(self, project: str) -> ProjectDirectories:
        """Look up one project's source-backed registry directory section."""
        return syntax.parse_project_directories(self._text, project)

    def project(self, name: str) -> ProjectSection | None:
        """Look up one project's source-backed section and its metadata."""
        return syntax.parse_project_section(self._text, name)

    def projects(self) -> tuple[ProjectSection, ...]:
        """Every top-level section, in source order, as it appears."""
        return syntax.parse_project_sections(self._text)

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
