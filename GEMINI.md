# GEMINI.md

This file provides instructional context for Gemini when working in the `ortask` repository.

## Project Overview

**ortask** is a lightweight Python CLI tool designed to query and edit TODO tasks within a `* Tasks` subtree of an [org-mode](https://orgmode.org/) file (defaulting to `README.org`). It prioritizes stable task IDs (e.g., `T0001`) to ensure permalinks and references remain valid even as tasks are moved or renamed.

- **Origins:** The project evolved from `status.py`, a simple Org-mode checkbox viewer, into a robust task manager that treats Org-mode as its primary database.
- **Naming:** The name combines **OR**g-mode and **TASK**. It was chosen for its unique namespace and clarity of purpose.
- **Primary Language:** Python 3.10+ (Standard library only; no external dependencies).
- **Core Architecture:** A single-file script (`ortask.py`) containing the parser, data model, and CLI logic.
- **Current State:** The tool is transitioning from a legacy checkbox-based parser (`[ ]` / `[X]`) to a formal org-mode `TODO`/`DONE` keyword parser with support for priorities, IDs, and tags.

## The "Ortask Way" (Core Design Principles)

1.  **Org-mode as Source of Truth:** The `.org` file is the database. No external stores (SQLite, JSON, etc.) are used.
2.  **Stable, Hierarchical IDs:** IDs are human-readable, stable, and placed directly in the heading.
    - **Top-level:** `T0001`
    - **Subtasks:** `T0001.1`, `T0001.2`
    - **Nested:** `T0001.1.1`
3.  **LLM-First Design:** The tool is optimized for AI agent consumption. The planned `context` subcommand provides a compact state summary for prompt context.
4.  **Surgical Persistence:** Modifications must preserve all surrounding prose, property drawers, and non-task content. Never reformat the entire file.
5.  **Human-Readable & Greppable:** IDs make the file easy for humans to read and for simple tools like `grep` to parse.

## Key Files

- `ortask.py`: The main executable script.
- `README.org`: The default data file and project quick-start guide.
- `docs/ortask.md`: The man-page style reference; consider this the **source of truth** for planned subcommand behavior.
- `AGENTS.md`: General repository guidelines for AI agents.
- `CLAUDE.md`: Implementation-specific guidance (useful for cross-referencing).
- `docs/*.org`: Design notes and architectural decisions.

## Building and Running

Since this project uses only the Python standard library, there is no build or install step.

- **List Tasks:** `./ortask.py` (Current implementation uses a legacy parser).
- **Limit Output:** `./ortask.py --items 5`.
- **Custom File:** `./ortask.py --file path/to/file.org`.
- **Syntax Check:** `python3 -m py_compile ortask.py`.
- **Testing:** `python3 -m pytest` (Expected command once a test suite is implemented).

### Planned Subcommands

| Subcommand | Description |
| :--- | :--- |
| `list` | Print tasks (filter by state, limit N, multiple formats). |
| `show <id>` | Detailed view of a task including body and properties. |
| `add <title>` | Append a new task with the next available ID. |
| `done <id>` | Mark a task as DONE. |
| `todo <id>` | Reopen a DONE task (also `open`). |
| `rename <id>`| Change a task's title while preserving ID and state. |
| `context` | **LLM Special**: Output a compact summary for prompt context. |
| `repair` | Validate ID integrity and tree structure. |
| `next` | Print the next ID that `add` would assign. |

## Development Conventions

### Task Heading Format (Target)
The parser should aim to match the following structure:
`** TODO [#A] T0005 Task title :tag1:tag2:`

- **Stars:** One or more (level).
- **State:** `TODO` or `DONE`.
- **Priority:** Optional `[#A]`, `[#B]`, or `[#C]`.
- **ID:** Stable identifier matching `T\d{4}(\.\d+)*`.
- **Tags:** Optional colon-separated list at the end of the line.

### Modification Principles
- **Surgical Edits:** Only modify the specific heading lines being updated. Preserve all other content.
- **No Global Reformatting:** Do not re-indent or re-format the entire file.
- **Idempotency:** Operations like `done` or `open` should be safe to run multiple times.
- **Atomic Writes:** Write to a temporary file, then rename to prevent data loss.

## Roadmap & Next Steps
1.  Refactor the current regex-based parser to support the new `T0001` ID format and `TODO`/`DONE` keywords.
2.  Implement the subcommand architecture using `argparse` subparsers.
3.  Implement safe in-place file modification for `add` and `done` commands.
4.  Create the `context` subcommand to aid future AI-driven development.
