"""ortasklib — shared library for the ortask suite.

Splits reusable behavior out of the top-level scripts:

- ``core``    — Org parsing, the ``TodoItem`` model, ID helpers, single-directory
                file discovery, and atomic writes.
- ``tasks``   — local task formatting, ``show`` expansion, ID allocation, and the
                line-level edit/validation helpers used by ``ortask.py``.
- ``manager`` — project config/discovery and multi-project summaries used by
                ``orgmgr.py`` and ``projtui.py``.
- ``menu``    — dashboard row models and Rich/plain rendering helpers for
                interactive tools.

See ``docs/architecture.md`` for the design.
"""
