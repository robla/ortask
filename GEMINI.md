# GEMINI.md

This file provides instructional context for Gemini when working in the `ortask` repository.

## Project Overview

**ortask** is a lightweight Python CLI tool designed to query and edit TODO tasks within a `* Tasks` subtree of an [org-mode](https://orgmode.org/) file. It prioritizes stable task IDs (e.g., `t0001` or week-based `tw26W24`) to ensure permalinks and references remain valid even as tasks are moved or renamed.

- **Origins:** The project evolved from `status.py`, a simple Org-mode checkbox viewer, into a robust task manager that treats Org-mode as its primary database.
- **Naming:** The name combines **OR**g-mode and **TASK**. It was chosen for its unique namespace and clarity of purpose.
- **Primary Language:** Python 3.10+ (Standard library only; no external dependencies).
- **Core Architecture:** Two CLI scripts — `ortask.py` (one task file) and `projmgr.py` (the project registry) — over the shared `ortasklib/` package.
- **Current State:** The parser supports a formal org-mode `TODO`/`DONE` keyword parser with support for priorities, stable IDs (both numeric `t0001` and week-based `tw26W24`), and tags.

## The "Ortask Way" (Core Design Principles)

1.  **Org-mode as Source of Truth:** The `.org` file is the database. No external stores (SQLite, JSON, etc.) are used.
2.  **Stable, Hierarchical IDs:** IDs are human-readable, stable, and placed directly in the heading.
    - **Top-level:** `t0001` (numeric) or week-based `tw26W24` (for weekly recurring work)
    - **Subtasks:** `t0001.1`, `t0001.2`, `tw26W24.1`
    - **Nested:** `t0001.1.1`
3.  **LLM-First Design:** The tool is optimized for AI agent consumption. The planned `context` subcommand provides a compact state summary for prompt context.
4.  **Surgical Persistence:** Modifications must preserve all surrounding prose, property drawers, and non-task content. Never reformat the entire file.
5.  **Human-Readable & Greppable:** IDs make the file easy for humans to read and for simple tools like `grep` to parse.

## Key Files

- `ortask.py`: The main executable script.
- `projmgr.py`: The project-layer command; `projmgr.py -i` (`ptui`) is the project navigator. Task UI lives in `ortasklib/taskui.py`.
- `task.org` / `*.task.org`: Preferred task-file names; legacy `TODO.org`, `todo.org`, and `tasks.org` remain fallbacks.
- `README.org`: Fallback data file and project quick-start guide.
- `docs/ortask.md`: The man-page style reference; the **source of truth** for planned subcommand behavior.
- `docs/projmgr.md`: Command reference for `projmgr.py`; `docs/projects.md` is the registry model.
- `docs/format.md`: Reference for task formatting and file discovery conventions.
- `docs/interactive.md`: Spec for the terminal menu interface.
- `AGENTS.md`: General repository guidelines for AI agents.
- `CLAUDE.md`: Implementation-specific guidance (useful for cross-referencing).
- `docs/*.org`: Design notes and architectural decisions.

## Building and Running

Since this project uses only the Python standard library, there is no build or install step.

- **List Tasks:** `./ortask.py` or `./ortask.py list`.
- **Limit Output:** `./ortask.py --items 5`.
- **Custom File:** `./ortask.py --file path/to/file.org`.
- **Run TUI:** `./projmgr.py -i` or `./projmgr.py --registry ~/Projects -i` to launch the project navigator.
- **Syntax Check:** `python3 -m py_compile ortask.py projmgr.py ortasklib/*.py`.
- **Testing:** `python3 -m pytest` (Expected command once a test suite is implemented).

### Subcommands (`ortask.py`)

Keep registered subcommands, dispatch/completion tables, help output, and this
reference alphabetized by subcommand name.

| Subcommand | Status | Description |
| :--- | :--- | :--- |
| `add <title>` | Implemented | Append a new task with the next available ID; supports `--parent <id>`. |
| `apply` | Implemented | Instantiate the weekly `* Template` subtree as new tasks. |
| `archive [<id>]` | Implemented | Move all DONE subtrees, or one selected subtree, to the adjacent `.org_archive` file. |
| `context` | Planned | **LLM Special**: Output a compact summary for prompt context. |
| `done <id>` | Implemented | Mark a task as DONE. |
| `help` | Implemented | Show command help. |
| `list` | Implemented | Print tasks (filter by state, limit N, multiple formats). Default when no subcommand is specified. |
| `next` | Planned | Print the next ID that `add` would assign. |
| `open <id>` | Implemented | Reopen a DONE task (sets state back to TODO). |
| `rename <id>`| Planned | Change a task's title while preserving ID and state. |
| `repair` | Implemented | Validate and fix ID integrity and tree structure (supports `--dry-run`). |
| `show <id>` | Implemented | Detailed view of a task including body, properties, and subtasks. |

## Development Conventions

### Task Heading Format (Target)
The parser matches the following structure:
`** TODO [#A] t0005 Task title :tag1:tag2:`
`** TODO tw26W24 Weekly task title :tag1:`

- **Stars:** One or more (level).
- **State:** `TODO` or `DONE`.
- **Priority:** Optional `[#A]`, `[#B]`, or `[#C]`.
- **ID:** Stable identifier matching `t\d{4}(\.\d+)*` or week-based `tw\d{2,4}[wW]\d{2}(\.\d+)*`.
- **Tags:** Optional colon-separated list at the end of the line.

### Modification Principles
- **Surgical Edits:** Only modify the specific heading lines being updated. Preserve all other content.
- **No Global Reformatting:** Do not re-indent or re-format the entire file.
- **Idempotency:** Operations like `done` or `open` should be safe to run multiple times.
- **Atomic Writes:** Write to a temporary file, then rename to prevent data loss.

## Roadmap & Next Steps
1. Implement the planned subcommands: `context`, `next`, and `rename`.
2. `projmgr.py` implements the project layer (`add`, `cdproj`, `doctor`, `init`, `list`, `rm`).
3. Add a test suite (`tests/`) using `pytest` to cover file parsing, filters, ID allocation, and editing safety.
