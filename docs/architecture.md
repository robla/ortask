# Architecture

This document describes the split between the user-facing scripts and the
shared `ortasklib` package. The goal was to move reusable parsing, discovery,
and project-management behavior out of the top-level scripts so features can be
added without growing three coupled scripts.

**Status:** implemented. There are two scripts, neither importing the other;
both import from `ortasklib`. The `docs/testing.md` suite runs unchanged as the
refactor gate (see *Test compatibility* below). The `projmgr.py` verbs (see
`docs/projmgr.md`) are implemented over `manager`: the project registry is a
directory of per-project symlink subdirectories, and `ortask.ini` records where
that registry lives. Both scripts resolve it through
`manager.resolve_registry()` (`--registry` > `[projects] registry` >
`~/Projects`). `docs/projects.md` is the model `manager` implements.

`projtui.py` no longer exists. It was 2103 lines imported by both scripts —
library code with a script name — and became `ortasklib/taskui.py`, with its
project browser folded into `projmgr.py`.

## Package Layout

```text
orglib/
  __init__.py
  syntax.py
ortasklib/
  __init__.py
  core.py
  log.py
  tasks.py
  manager.py
  menu.py
  taskui.py
ortask.py
projmgr.py
```

`ortasklib/` is the package name because a directory named `ortask/` would
collide with the existing `ortask.py` executable module. If the scripts are
later moved under `bin/` or renamed, the package can be renamed to `ortask/`.

## Dependency Graph

```text
orglib  (stdlib only, depends on nothing)
  ^
  |
core
  ^ ^ ^
  | | |
tasks manager menu
  ^     ^      ^
  |     |      |
  +--- taskui -+
        ^   ^
        |   |
 ortask.py  projmgr.py
```

No script imports another script. `orglib` sits at the bottom and imports
nothing at all — not `ortasklib`, not any third-party package. `core` depends
only on `orglib`; `tasks` and `manager` depend on `core`; `taskui` composes
`core`, `tasks`, `menu`, and the `Project` record from `manager`.

`manager` and `projmgr.py` also import `orglib` directly, for the read paths
already routed through the `Document` boundary. That is the direction new
callers should follow: reach for `orglib` rather than `core` when the need is
parsing rather than files.

`log` composes registry resolution from `manager`, task snapshots from
`orglib`, and ID canonicalization from `core`. The scripts and `taskui` call it
only after successful writes; no lower-level text mutation helper imports it.

## Script Responsibilities

The top-level scripts are thin command/front-end layers. They own argument
parsing, user prompts, process exit codes, and human-readable output. Shared
modules never call `sys.exit()` or parse CLI arguments.

- `ortask.py` — local task commands (`add`, `apply`, `archive`, `done`, `init`,
  `list`, `log`, `open`, `repair`, `show`). Each `cmd_*` reads or initializes the file,
  calls a `tasks`/`core` helper,
  translates the result (and `TaskNotFound`) into output and an exit code, and
  uses the shared atomic writers for replacements; `init` creates a missing
  path exclusively so it cannot overwrite a concurrent file.
- `projmgr.py` — project-layer commands (`add`, `cdproj`, `doctor`, `init`,
  `list`, `log`, `migrate`, `rm`, `set-dirs`) plus `-i`; `projadd` is a deprecated
  alias. `cmd_list` calls `manager.summarize_projects()` and formats the
  records. It owns the project list itself: `_project_rows`,
  `_project_location`, `_anchor_index`, and `_project_view` are shared by the
  navigator (`_ProjectBrowser`), `cdproj` (`_CdprojSession`), and both numbered
  fallbacks, so only what `Enter` does differs between them.

## `core.py`

Pure, shared building blocks that operate on in-memory strings and individual
files:

- task-file discovery constants
- low-level query helpers `filter_items()` / `find_by_id()`
- `normalize_id()` / `canonical_id()`
- `build_org_heading()` — render a `TodoItem` back to one heading line
- `atomic_write()` / `write_lines()`
- `resolve_org_file()` — local task-file discovery, including explicit
  overrides and the upward task-file-name search described in `docs/format.md`

`core.py` has no command names or argument parsing. Ambiguous task-file
discovery raises `OrgFileDiscoveryError`; CLI front-ends decide how to report it.

The `TodoItem` model, the heading regexes, `find_tasks_range()`, `parse_org()`,
and `parse_directories()` moved to `orglib.syntax` on 2026-08-21. `core.py`
re-exports all of them, so `core.parse_org` and `core.TodoItem` still resolve
and no caller had to change.

## `orglib/`

A peer package, not part of `ortasklib`. It holds Org syntax and nothing else:
`syntax.py` has the regexes, the `TodoItem` model, and the text parsers;
`__init__.py` adds the `parse(text) -> Document` boundary that callers use.

It imports nothing outside the standard library — no `ortasklib`, no third-party
package. The dependency runs `ortasklib` → `orglib` only, which is what allows a
different Org backend to be substituted later without `ortasklib` knowing.
`tests/test_ortask_suite.py::test_orglib_imports_without_ortasklib` enforces the
direction by importing `orglib` in a subprocess with `ortasklib` blocked.

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
  workflow tools that mark recurring task trees terminally `MOOT` while still
  accepting `SUPERSEDED` as a compatibility alias;
  `add_task()` only creates `* Tasks` when the caller explicitly enables that
  bootstrap path
- validation: `find_repair_problems()`
- `TaskNotFound` — raised by `show_lines()`, `add_task()`, `change_state()`,
  `change_text()`, and `change_priority()` when an ID (or parent ID) does not
  resolve, so the helpers stay free of printing and exit codes while
  `ortask.py` decides how to report the failure

Write helpers return a new line list and let `ortask.py` perform the atomic
write, preserving surrounding prose and avoiding whole-file reserialization.
`change_state()`, `change_text()`, and `change_priority()` return `None` when
the requested value is already present, so the caller can skip the write
entirely.

## `manager.py`

Behavior used by `projmgr.py` and by `taskui`:

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

`projmgr.py`'s `init` adapter writes the registry into `ortask.ini`; its `add`
adapter creates the per-project symlink subdirectory, using
`manager.project_root_for()` to find the project root and
`core.discover_org_file()` for local task-file discovery. Every project surface
resolves the registry through `manager.resolve_registry()` and enumerates it
through `manager.discover_projects()`, so the interactive and non-interactive
paths agree on the same project list.

`manager.read_project_entry()` holds the marker rule: a registry entry is a
project when it points outward (a symlink to a directory, or failing that to an
Org file). A task file is optional, and broken or ambiguous entries carry a
`warning` rather than disappearing.

## `log.py`

Best-effort event logging and read-side activity queries:

- schema-1 event construction with local offset timestamps and command sessions
- opt-in `[log] enabled` plus environment overrides
- one-call `O_APPEND` JSON Lines writes capped at 4096 bytes
- registry project inference for local task files
- `WHEN` parsing, half-open range filtering, project/file scoping, and limits
- unchanged JSON passthrough plus plain and Org renderers
- saved-buffer task diffs for TUI `edit` events

The log is derived and disposable. Every exception in the write and TUI-diff
paths is contained so logging cannot change a command's result. CLI adapters
create events after their authoritative write succeeds; `tasks.py` remains
side-effect free. See `docs/logging.md`.

## `menu.py`

Shared rendering and bounded-interaction primitives for interactive tools:

- `MenuRow` — stable row shape for numbered dashboard/menu displays
- `ProjectRow` — stable row shape for registry project displays
- `count_statuses()` — open/done/total summary counts
- `print_task_dashboard()` — Rich table rendering with a plain text fallback
- `print_project_dashboard()` — numbered project-list rendering for `projmgr.py`
- `MenuView` / `TextInputView` / `InlineMenuSession` — a persistent, bounded
  20-row application shell with scrolling menus, focused single-line input,
  context-filtered bindings, contextual Help, view-stack transitions, resize
  clamping, factual final outcomes, and external-command suspension
- `prompt_text()` / `ContextCancelled` — prompt_toolkit Esc cancellation with
  plain `input()` fallback

The shared layer owns presentation and interaction mechanics, not workflow
behavior. `taskui.InteractiveTaskController` decides which task rows to show,
what each action does, whether recovery data requires an initial choice view,
whether Back should push its bounded save/discard view, and how accepted task
text is validated and buffered.
`projmgr._ProjectBrowser` supplies the project root, attaches task
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

0. (2026-08-21, after the fact) Extracted the Org syntax from
   `ortasklib/core.py` into the standalone `orglib/` package — see the
   `orglib/` section above. `core.py` re-exports the moved names, so the steps
   below still describe what the code does, just not where all of it lives.

1. Created `ortasklib/core.py` with the parser, model, ID, discovery, and
   atomic-write helpers.
2. Moved local formatting, `show`, ID allocation, edit, and validation logic
   into `ortasklib/tasks.py`, leaving `ortask.py` as argument parsing, dispatch,
   and thin `cmd_*` adapters.
3. Moved project discovery/config and the project-list summary into
   `ortasklib/manager.py`.
4. Updated the scripts to import from `manager` instead of from
   each other (and from `ortask.py`).
5. Kept the existing tests green at each step via the `ortask.py` re-exports.

## Future work

- Split `menu.py`: it still holds rendering, session lifecycle, and plain-text
  dashboards in one module. See the Risks section of `docs/roadmap.md`.
- Consider whether `taskui.py` should divide further now that it is a library
  module rather than a script: the buffer, the task list, and the issue
  workspace are three concerns in one file.
- Consider repointing the test suite to import from `ortasklib` directly and
  retiring the `ortask.py` compatibility re-exports.

The registry-of-symlinks model and every `projmgr.py` verb are done, with
config-isolation tests (a temp `XDG_CONFIG_HOME`) in `tests/test_ortask_suite.py`.
