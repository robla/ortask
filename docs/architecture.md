# Architecture

This document describes the split between the user-facing scripts and the
shared `ortasklib` package. The goal was to move reusable parsing, discovery,
and project-management behavior out of the top-level scripts so features can be
added without growing three coupled scripts.

**Status:** implemented. The scripts no longer import each other; they import
from `ortasklib`. The `docs/testing.md` suite runs unchanged as the refactor
gate (see *Test compatibility* below). The `orgmgr.py list`/`migrate`/`projadd`
verbs (see `docs/orgmgr.md`) are implemented in `manager`: the project registry
is a directory of per-project symlink subdirectories, and `ortask.ini` records
where that registry lives. All three scripts resolve it through
`manager.resolve_registry()` (`--registry` > `[projects] registry` >
`~/Projects`).

## Package Layout

```text
ortasklib/
  __init__.py
  core.py
  tasks.py
  manager.py
  menu.py
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
tasks  manager     menu
  ^      ^          ^
  |      |          |
ortask.py   orgmgr.py        projtui.py
(core,tasks) (manager)       (core, tasks, manager, menu)
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
  `manager.summarize_projects()` and formats the records. `cmd_interactive`
  launches the registry-scoped project browser. Future `projrm` and registry
  maintenance also belong here.
- `projtui.py` — the current project-browser implementation shim. Menu
  rendering, prompting, and editor launch stay here for now; it imports project
  discovery from `manager` and parsing/edit helpers from `core`/`tasks`.

## `core.py`

Pure, shared building blocks that operate on in-memory strings and individual
files:

- the `TodoItem` model
- Org heading regexes, the `TASK_ID_PATTERN`, and task-file discovery constants
- `find_tasks_range()` — `* Tasks` subtree detection
- `parse_org()` — parses the `* Tasks` subtree when present, otherwise valid
  task headings across the whole file
- low-level query helpers `filter_items()` / `find_by_id()`
- `normalize_id()` / `canonical_id()`
- `build_org_heading()` — render a `TodoItem` back to one heading line
- `atomic_write()` / `write_lines()`
- `resolve_org_file()` — local task-file discovery, including explicit
  overrides and the upward task-file-name search described in `docs/format.md`

`core.py` has no command names or argument parsing. Ambiguous task-file
discovery raises `OrgFileDiscoveryError`; CLI front-ends decide how to report it.

## `tasks.py`

Behavior used by `ortask.py`. These functions take Org text (or parsed items)
and return data or new line lists — they never read/write files, print, or call
`sys.exit`:

- formatters: `format_plain()`, `format_org()`, `format_json()`
- `show_lines()` — heading, body, and descendants for a selected task
- ID allocation: `next_toplevel_id()`, `next_subtask_id()`
- line-level edits: `add_task()`, `change_state()` (powers `done`/`open`),
  `change_text()`, and `change_priority()` plus `shift_priority()` for buffered
  interactive editing; `ensure_terminal_keyword()` and
  `change_subtree_state()` support
  workflow tools that terminally supersede recurring task trees;
  `add_task()` only creates `* Tasks` when the caller explicitly enables that
  bootstrap path
- validation: `find_repair_problems()`
- `TaskNotFound` — raised by `show_lines()`, `add_task()`, `change_state()`, and
  `change_priority()` when an ID (or parent ID) does not resolve, so the helpers
  stay free of printing and exit codes while `ortask.py` decides how to report
  the failure

Write helpers return a new line list and let `ortask.py` perform the atomic
write, preserving surrounding prose and avoiding whole-file reserialization.
`change_state()` and `change_priority()` return `None` when the task is already
in the target state, so the caller can skip the write entirely.

## `manager.py`

Behavior used by `orgmgr.py` and shared with `projtui.py`:

- the `Project` record
- registry resolution — `ortask_config_path()` (`ortask.ini`) honors
  `XDG_CONFIG_HOME`; `read_ortask_registry()` / `write_ortask_registry()`; and
  `resolve_registry()` with precedence `--registry` > `[projects] registry` >
  `~/Projects`
- project discovery (`discover_projects()`) over the registry's per-project
  subdirectories and per-project Org-file selection (`choose_org_file()`, which
  transparently follows the project/task symlinks)
- `summarize_projects()` — JSON-ready, top-level task summaries per project,
  attaching a `warning` (instead of raising) for unreadable files, files with no
  parseable task headings, or duplicate IDs

`orgmgr.py`'s `migrate` adapter writes the registry into `ortask.ini`; its
`projadd` adapter creates the per-project symlink subdirectory, using
`core.discover_org_file()` for local task-file discovery. Both
`orgmgr.py list` and `projtui.py` resolve the registry through
`manager.resolve_registry()`, so the interactive and non-interactive tools agree
on the same project list.

## `menu.py`

Small shared rendering primitives for interactive tools:

- `MenuRow` — stable row shape for numbered dashboard/menu displays
- `ProjectRow` — stable row shape for registry project displays
- `count_statuses()` — open/done/total summary counts
- `print_task_dashboard()` — Rich table rendering with a plain text fallback
- `print_project_dashboard()` — shared project-list rendering for `projtui.py`
- `select_menu()` / `select_project_menu()` — inline prompt_toolkit
  one-shot highlight-bar selectors retained for compatibility
- `MenuView` / `InlineMenuSession` — a persistent, bounded 20-row application
  shell with scrolling, command dispatch, contextual Help, view-stack
  transitions, resize clamping, factual final outcomes, and external-command
  suspension
- `prompt_text()` / `ContextCancelled` — prompt_toolkit Esc cancellation with
  plain `input()` fallback

The shared layer owns presentation and interaction mechanics, not workflow
behavior. `projtui.InteractiveTaskController` decides which task rows to show,
what each action does, whether recovery data requires an initial choice view,
and whether Back should push its bounded save/discard view.
`projtui.InteractiveProjectController` supplies the project root, attaches task
controllers beneath it, and restores stable project selection on return.
Numbered fallbacks retain their plain prompts by policy.

`InlineMenuSession` initializes prompt_toolkit with `erase_when_done=True` so
uncontrolled exits do not retain a misleading frame. Only a controlled root
pop switches erasure off after placing the latest save/discard/recovery/no-op
outcome in the footer. Prompt_toolkit's final render restores terminal modes
and leaves the next shell prompt below that one retained bounded frame.

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

- Add `projrm` (and possibly `scan`/`doctor`) per `docs/orgmgr.md`.
- Consider repointing the test suite to import from `ortasklib` directly and
  retiring the `ortask.py` compatibility re-exports.

`list`, `migrate`, and `projadd` (the registry-of-symlinks model) are done, with
config-isolation tests (a temp `XDG_CONFIG_HOME`) in `tests/test_ortask_suite.py`.
