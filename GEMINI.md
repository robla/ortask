# GEMINI.md

This file provides guidance and repository context for Gemini when working in the `ortask` repository.

## Project Overview

**ortask** is a lightweight Python CLI toolset designed to query and edit TODO tasks within a `* Tasks` subtree of an [org-mode](https://orgmode.org/) file. It prioritizes stable, human-readable task IDs (e.g., `t0001` or week-based `tw26W24`) to ensure permalinks and references remain valid even as tasks are moved, reorganized, or renamed.

- **Philosophy:** The "wiki way" (plain text that humans can read and edit, with minimal convention; see `docs/format.md`). The Org file is always the primary database.
- **Language & Runtime:** Python 3.10+ (Standard library only; zero external runtime dependencies).
- **Core Architecture:** Two CLI scripts — `ortask.py` (local task manager) and `projmgr.py` (project registry manager) — over the shared `ortasklib/` package and the standalone, stdlib-only `orglib/` package.
- **Interactive UI:** Optional `prompt_toolkit` and Rich enhance the bounded TUI (`orti` / `ptui`), with robust fallback behavior when absent.

## The "Ortask Way" (Core Design Principles)

1. **Org-mode as Source of Truth:** The `.org` file is the database. No external SQLite, JSON, or sidecar state stores are used.
2. **Stable, Hierarchical IDs:** IDs are placed directly in the heading:
   - **Top-level:** `t0001` (numeric) or `tw26W24` / `tw26w24` (ISO-like week recurring work)
   - **Subtasks:** `t0001.1`, `t0001.2`, `tw26W24.1`
   - **Nested:** `t0001.1.1`
3. **Surgical Line-Level Persistence:** Modifications must preserve surrounding prose, property drawers, blank lines, and non-task content. Never reformat or normalize the whole file.
4. **LLM-First Design:** Optimized for AI agent consumption and transparent collaboration across models (`docs/llm-assessments.md`, `docs/llm-log.org`).
5. **Zero-Dependency Runtime:** The core CLI depends strictly on Python's standard library.

## Key Files & Directory Layout

- `ortask.py` (`ort`): Single task file CLI (the core tool).
- `projmgr.py` (`pmgr`): Multi-project registry manager (`~/Projects`) and directory stack coordinator. `-i` opens the `ptui` project navigator.
- `orglib/`: Standalone, stdlib-only Org syntax and parsing package (`syntax.py`, `__init__.py`).
- `ortasklib/`: Shared support package: `core.py` (discovery, IDs, atomic writes), `tasks.py` (queries, validation, surgical edits), `manager.py` (registry discovery, config, index), `menu.py` (bounded inline TUI), `viewstate.py` (stdlib-only filter/sort/direction model), `viewui.py` (the `v` view options screen), and `taskui.py` (interactive task workspace).
- `misc/`: Shell integration (`cdproj.func.sh`, `ortask-completion.bash`, `projmgr.aliases.sh`).
- `todo.org`: The repository's active task tracking file.
- `tests/test_ortask_suite.py`: Comprehensive pytest test suite and refactor gate.
- `docs/`: Specs, design references, and documentation (`docs/ortask.md`, `docs/projmgr.md`, `docs/projects.md`, `docs/config.md`, `docs/cdproj.md`, `docs/orglib.md`, `docs/testing.md`).
- `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`: Sibling instruction files kept aligned across agents.

## Running & Testing

```sh
./ortask.py                        # List open tasks
./ortask.py list --todo            # Open tasks only
./ortask.py --file /path/to/file.org list
./projmgr.py list                  # Overview of all registered projects
./projmgr.py -i                    # Launch interactive project navigator (ptui)
python3 -m py_compile ortask.py projmgr.py ortasklib/*.py orglib/*.py  # Syntax check
python3 -m pytest tests/           # Run full regression test suite (the refactor gate)
```

## Default File Resolution Ladder (`ortask.py`)

1. `--file FILE` command-line argument (wins over everything)
2. `ORTASK_FILE` environment variable
3. Upward walk from the current working directory, probing in each directory:
   `tasks.org` $\rightarrow$ `task.org` $\rightarrow$ exactly one `*.task.org` $\rightarrow$ `TODO.org` $\rightarrow$ exactly one `TODO*.org` $\rightarrow$ `todo.org`
4. Exactly one generic `*.org` file in the *original* starting directory (last resort)

Ambiguous matches at any step produce an error. `projmgr.py` uses stricter non-walking discovery (`discover_org_file` / `preferred_task_file_in`).

## Subcommands

Keep registered subcommands, dispatch tables, and help documentation alphabetized.

### `ortask.py` Subcommands

| Subcommand | Status | Description |
| :--- | :--- | :--- |
| `add <title>` | Implemented | Append a new task with the next available ID; supports `--parent <id>`. |
| `apply` | Implemented | Instantiate weekly `* Template` subtree as new tasks. |
| `archive [<id>]` | Implemented | Move DONE subtrees to adjacent `.org_archive` file. |
| `context` | Planned | Output compact summary for prompt context. |
| `done <id>` | Implemented | Mark task as DONE. |
| `help` | Implemented | Show command help. |
| `info` | Implemented | Report task file metadata and database status. |
| `list` | Implemented | Print tasks (filter by state, limit N, multiple formats). Default action. |
| `next` | Planned | Print next ID that `add` would assign. |
| `open <id>` | Implemented | Reopen a DONE or MOOT task (sets state back to TODO). |
| `rename <id>` | Planned | Change task title while preserving ID and state. |
| `repair` | Implemented | Validate ID integrity and tree structure (supports `--dry-run`). |
| `show <id>` | Implemented | Detailed view of task body, properties, and subtasks. |

### `projmgr.py` Subcommands

| Subcommand | Status | Description |
| :--- | :--- | :--- |
| `add [DIR]` | Implemented | Register a project directory into the registry. |
| `cdproj [PROJ]` | Implemented | Resolve and output directory stack for `cdproj` shell function. |
| `help` | Implemented | Show command help. |
| `info [PROJ]` | Implemented | Display resolved project context and registry metadata. |
| `init` | Implemented | Initialize `ortask.ini` pointing to the registry (`~/Projects`). |
| `list` | Implemented | Overview of registered projects and open tasks. |
| `migrate` | Implemented | Migrate per-entry `directories-private.org` to central `projects.org`. |
| `repair` | Implemented | Check registry health, report broken symlinks or missing task files. |
| `rm <project>` | Implemented | Remove a project entry from the registry. |
| `set-dirs [PROJ]`| Implemented | Persist directory stack to `projects.org` (`cdproj -s` backend). |

## Task Heading & State Conventions

Target heading format:
`** TODO [#A] t0005 Task title :tag1:tag2:`
`** TODO tw26W24 Weekly task title :tag1:`

- **Stars:** Level (`*`, `**`, `***`).
- **State:** `TODO`, `DONE`, `MOOT` (terminal), `SUPERSEDED` (compatibility alias).
- **Priority:** Optional `[#A]`, `[#B]`, or `[#C]`.
- **ID:** Stable numeric `t\d{4}(\.\d+)*` or weekly `tw\d{2,4}[wW]\d{2}(\.\d+)*`.
- **Tags:** Optional colon-separated tags at the end of the line.

## Testing & Data Safety Rules

1. **Refactor Gate:** Run `python3 -m pytest tests/` before and after changes.
2. **Never Mutate Real Data:** Do not run mutating commands (`ortask.py add`, `done`, `archive`, `repair`, or `projmgr.py add`, `rm`, `set-dirs`, `migrate`) against `todo.org` or real user registries merely to test code. Use hermetic temporary fixtures and test suites.
3. **Atomic File Writes:** Mutations write to a temp file in the same directory and rename over target.
4. **LLM Log & Assessments:** Append user-requested repository changes to `docs/llm-log.org` in the same turn. Maintain standing assessments in `docs/llm-assessments.md`.
