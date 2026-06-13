# Architecture

This document describes the split between the user-facing scripts and the
shared `ortasklib` package. The goal was to move reusable parsing, discovery,
and project-management behavior out of the top-level scripts so features can be
added without growing three coupled scripts.

**Status:** implemented. The scripts no longer import each other; they import
from `ortasklib`. The `docs/testing.md` suite runs unchanged as the refactor
gate (see *Test compatibility* below). Project **registry** read/write
(`orgmgr.py projadd`/`migrate`, see `docs/orgmgr.md`) is still future work; the
`manager` module currently implements the legacy `projdir` workspace model.

## Package Layout

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

`ortasklib/` is the package name because a directory named `ortask/` would
collide with the existing `ortask.py` executable module. If the scripts are
later moved under `bin/` or renamed, the package can be renamed to `ortask/`.

## Dependency Graph

```text
core    (stdlib only)
  ^ ^
  | |
tasks  manager
  ^      ^
  |      |
ortask.py   orgmgr.py        projtui.py
(core,tasks) (manager)       (core, tasks, manager)
```

No script imports another script. `core` has no intra-package dependencies;
`tasks` and `manager` depend only on `core`.

## Script Responsibilities

The top-level scripts are thin command/front-end layers. They own argument
parsing, user prompts, process exit codes, and human-readable output. Shared
modules never call `sys.exit()` or parse CLI arguments.

- `ortask.py` — local task commands (`list`, `show`, `add`, `done`, `open`,
  `repair`). Each `cmd_*` reads the file, calls a `tasks`/`core` helper,
  translates the result (and `TaskNotFound`) into output and an exit code, and
  writes via `core.write_lines`.
- `orgmgr.py` — global multi-project commands. `cmd_list` calls
  `manager.summarize_projects()` and formats the records. Future `projadd`,
  `projrm`, `migrate`, and registry maintenance also belong here.
- `projtui.py` — the interactive terminal UI. Menu rendering, prompting, and
  editor launch stay here; it imports project discovery from `manager` and
  parsing/edit helpers from `core`/`tasks`.

## `core.py`

Pure, shared building blocks that operate on in-memory strings and individual
files:

- the `TodoItem` model
- Org heading regexes, the `TASK_ID_PATTERN`, and `PROBE_NAMES`
- `find_tasks_range()` — `* Tasks` subtree detection
- `parse_org()` and the low-level query helpers `filter_items()` / `find_by_id()`
- `normalize_id()` / `canonical_id()`
- `build_org_heading()` — render a `TodoItem` back to one heading line
- `atomic_write()` / `write_lines()`
- `resolve_org_file()` — single-directory task-file discovery

`core.py` has no command names or argument parsing. It may print a discovery
warning to stderr (e.g. multiple candidate `*.org` files), but nothing else.

## `tasks.py`

Behavior used by `ortask.py`. These functions take Org text (or parsed items)
and return data or new line lists — they never read/write files, print, or call
`sys.exit`:

- formatters: `format_plain()`, `format_org()`, `format_json()`
- `show_lines()` — heading, body, and descendants for a selected task
- ID allocation: `next_toplevel_id()`, `next_subtask_id()`
- line-level edits: `add_task()`, `change_state()` (powers `done`/`open`)
- validation: `find_repair_problems()`
- `TaskNotFound` — raised by `show_lines`/`add_task`/`change_state` when an ID
  (or parent ID) does not resolve, so the helpers stay free of printing and exit
  codes while `ortask.py` decides how to report the failure

Write helpers return a new line list and let `ortask.py` perform the atomic
write, preserving surrounding prose and avoiding whole-file reserialization.
`change_state()` returns `None` when the task is already in the target state, so
the caller can skip the write entirely.

## `manager.py`

Behavior used by `orgmgr.py` and shared with `projtui.py`:

- the `Project` record
- config-path resolution (`default_config_path()`, honoring `XDG_CONFIG_HOME`)
- legacy `projdir` workspace handling (`read_config_projdir()`,
  `resolve_projdir()`)
- project discovery (`discover_projects()`) and per-project Org-file selection
  (`choose_org_file()`)
- `summarize_projects()` — JSON-ready, top-level task summaries per project,
  attaching a `warning` (instead of raising) for unreadable files, missing
  `* Tasks` sections, or duplicate IDs

Both `orgmgr.py list` and `projtui.py` use `manager` for project discovery, so
the interactive and non-interactive tools agree. The shared **registry** read/
write described in `docs/orgmgr.md` (a `[projects]` section in `ortask.ini`) is
not yet implemented; until then `manager` resolves projects from a `projdir`
workspace.

## Test compatibility

`docs/testing.md`'s suite imports the scripts and calls names like
`ortask.parse_org`, `ortask.cmd_done`, and `ortask._find_repair_problems`. To
let that suite run unchanged before and after the move (the "refactor gate"),
`ortask.py` re-exports the moved names from `ortasklib` — including the
historical private aliases `_find_tasks_range`, `_build_org_heading`,
`_write_lines`, and `_find_repair_problems`. New code should import from
`ortasklib` directly; the re-exports exist for compatibility.

## How the refactor was sequenced

1. Created `ortasklib/core.py` with the parser, model, ID, discovery, and
   atomic-write helpers.
2. Moved local formatting, `show`, ID allocation, edit, and validation logic
   into `ortasklib/tasks.py`, leaving `ortask.py` as argument parsing, dispatch,
   and thin `cmd_*` adapters.
3. Moved project discovery/config and the `orgmgr list` summary into
   `ortasklib/manager.py`.
4. Updated `orgmgr.py` and `projtui.py` to import from `manager` instead of from
   each other (and from `ortask.py`).
5. Kept the existing tests green at each step via the `ortask.py` re-exports.

## Future work

- Implement the shared project registry (`projadd`/`migrate`) in `manager`, per
  `docs/orgmgr.md`, and add tests for config isolation (a temp `XDG_CONFIG_HOME`)
  and registry read/write.
- Once the registry lands, `projtui.py` and `orgmgr.py` should prefer it over the
  `projdir` workspace model.
- Consider repointing the test suite to import from `ortasklib` directly and
  retiring the `ortask.py` compatibility re-exports.
