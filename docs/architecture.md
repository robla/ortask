# Architecture Refactoring Plan

This document describes the intended split between the user-facing scripts and
the shared ortask library. The goal is to move reusable parsing, discovery, and
project-management behavior out of the top-level scripts before adding more
features.

## Package Layout

Use a dedicated package directory for shared code:

```text
ortasklib/
  __init__.py
  core.py
  tasks.py
  manager.py
ortask.py
orgmgr.py
projtui.py
```

`ortasklib/` is the proposed package name because a directory named `ortask/`
would collide with the existing `ortask.py` executable module. If the scripts
are later moved under `bin/` or renamed, the package can be renamed to
`ortask/`.

## Script Responsibilities

The top-level scripts should become thin command/front-end layers:

- `ortask.py` handles local task-list commands such as `list`, `show`, `add`,
  `done`, `open`, and `repair`.
- `orgmgr.py` handles global multi-project commands such as `list`, future
  `projadd`, `projrm`, `migrate`, and database/index maintenance.
- `projtui.py` remains the interactive terminal UI. Most menu rendering,
  prompting, and editor-launch logic should stay there, but it should import
  discovery and task helpers from the shared library.

Scripts should own argument parsing, user prompts, process exit codes, and
human-readable command output. Shared modules should not call `sys.exit()` or
parse CLI arguments.

## `core.py`

`ortasklib/core.py` contains code shared by both local and global tools:

- Org task data models such as `TodoItem`
- Org heading regexes and ID normalization/canonicalization
- `* Tasks` subtree detection
- `parse_org()` and low-level task filtering helpers
- safe file helpers such as atomic writes
- task-file discovery rules used inside a single directory

Keep `core.py` free of command names and UI assumptions. It should expose small
functions that are easy to test with in-memory strings and temporary files.

## `tasks.py`

`ortasklib/tasks.py` contains behavior mainly used by `ortask.py`:

- local task listing and formatting helpers
- `show` expansion for a selected task and descendants
- ID allocation for top-level tasks and subtasks
- line-level edit helpers for `add`, `done`, `open`, and future repair work
- validation functions for duplicate IDs and malformed task headings

`ortask.py` should call these helpers and translate results into CLI output.
Write operations should continue to preserve surrounding prose and avoid
reserializing whole Org files when a line-level edit is enough.

## `manager.py`

`ortasklib/manager.py` contains behavior mainly used by `orgmgr.py` and shared
with `projtui.py`:

- project config path resolution
- project registry read/write logic
- legacy `projdir` compatibility during migration
- project discovery from a workspace or registry
- Org file selection for a project
- top-level task summaries for project listings
- JSON-ready project/task records

`orgmgr.py list` should eventually use `manager.py` instead of importing
`projtui.py`. `projtui.py` should also use `manager.py` for project discovery
so the interactive and non-interactive tools agree.

## Refactoring Order

1. Create `ortasklib/core.py` and move pure parser, model, ID, discovery, and
   atomic-write helpers there.
2. Move local command helpers from `ortask.py` into `ortasklib/tasks.py`, leaving
   `ortask.py` as argument parsing plus dispatch.
3. Move project discovery/config helpers from `projtui.py` and `orgmgr.py` into
   `ortasklib/manager.py`.
4. Update `orgmgr.py` and `projtui.py` to import from `manager.py` rather than
   from each other.
5. Add tests around `core.py`, then task edits, then manager discovery and
   registry behavior.

This order keeps behavior stable while removing the current script-to-script
coupling.
