"""ortasklib — shared library for the ortask suite.

Splits reusable behavior out of the top-level scripts:

- ``core``    — Org parsing, the ``TodoItem`` model, ID helpers, single-directory
                file discovery, and atomic writes.
- ``tasks``   — local task formatting, ``show`` expansion, ID allocation, and the
                line-level edit/validation helpers used by ``ortask.py``.
- ``manager`` — project config/discovery and multi-project summaries used by
                ``ortask.py`` and ``projmgr.py``.
- ``log``     — disposable append-only activity events and read-side filters.
- ``menu``    — dashboard row models, Rich/plain rendering helpers, and shared
                prompt cancellation for interactive tools.

``orglib`` is a peer package, not a member of this one: it holds Org syntax and
the boundary callers parse through. It imports nothing outside the standard
library, and ``core`` re-exports its names. See ``docs/orglib.md``.

See ``docs/architecture.md`` for the design.
"""
