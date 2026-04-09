# Adjacent Task Trackers

## Top 5 Most Popular Trackers (by GitHub Stars)

1. **Taskbook** (~9.3k stars) — Modern, visual board-based (Kanban) structure.
2. **todo.txt-cli** (~6.0k stars) — The industry standard for minimalist plain-text tasks.
3. **Taskwarrior** (~5.7k stars) — The highly mature "power user" choice with a rich ecosystem.
4. **Taskell** (~1.8k stars) — An interactive terminal Kanban board written in Haskell.
5. **org-ql** (~1.6k stars) — The definitive search and query engine for Emacs Org-mode.

---

This document tracks nearby tools and workflows that overlap with the
goals of `status.py`: text-backed task tracking, command-line access,
and human-readable storage.

## Closest Org-Mode Match

### Org mode via Emacs batch commands
Org itself already supports command-line agenda extraction from `.org`
files. `org-batch-agenda` and `org-batch-agenda-csv` can print TODO
views to standard output.
- **Website:** https://orgmode.org/ and https://www.gnu.org/software/emacs/manual/html_node/org/Extracting-Agenda-Information.html
- **Format:** Org-mode files
- **CLI shape:** Emacs batch invocation, not a standalone task CLI
- **Relationship to status.py:** Closest existing match to "Org file as
  database", but not a focused tool for safe subtree edits

### org-cli
A newer Org-focused CLI that ships inside the `org-mcp-server` project.
- **Website:** https://github.com/szaffarano/org-mcp-server
- **Format:** Org-mode files
- **Strengths:** list/search/read/outline commands over Org trees
- **Relationship to status.py:** similar "Org files as data" direction,
  but broader in scope and less focused on one conservative task subtree

### org-ql
A query engine for Org files with agenda-like views and search commands.
- **Website:** https://github.com/alphapapa/org-ql
- **Format:** Org-mode files
- **Strengths:** powerful filtering and query language
- **Relationship to status.py:** strong reference for querying and
  filtering, but not primarily a standalone file-editing CLI

## Plain-Text and File-Backed CLI Trackers

### todo.txt-cli
A minimal shell CLI for managing a `todo.txt` file.
- **Website:** https://todotxt.org/ and https://github.com/todotxt/todo.txt-cli
- **Format:** single plain-text file
- **Strengths:** simple, hand-editable, mature ecosystem, add/list/do/archive
- **Relationship to status.py:** strongest example of "text file first,
  CLI second"

### Taskwarrior
A more ambitious CLI task manager with filtering, reports, recurrence,
and richer metadata.
- **Website:** https://taskwarrior.org/
- **Format:** local Taskwarrior data store, not raw text files
- **Strengths:** powerful querying, custom reports, mature workflow
- **Relationship to status.py:** good reference for subcommand design,
  but not for "edit the source file directly"

### dstask
A git-oriented terminal task manager with markdown notes per task.
- **Website:** https://github.com/naggie/dstask
- **Format:** task files plus markdown notes, designed for git sync
- **Strengths:** human-readable storage, context system, git-friendly sync
- **Relationship to status.py:** strong reference for file-backed tasks
  that remain friendly to version control

### TaskLite
A Haskell-based CLI task manager with a SQLite backend.
- **Website:** https://tasklite.org/
- **Format:** SQLite-backed local store
- **Strengths:** rich filtering and a simpler alternative to Taskwarrior
- **Relationship to status.py:** useful comparison point for command
  surface and query power, not for direct text-file editing

### Taskbook
A terminal task manager organized around boards and notes.
- **Website:** https://klaudiosinani.com/taskbook/
- **Format:** JSON storage
- **Strengths:** list/search/edit/archive operations, terminal-friendly UX
- **Relationship to status.py:** useful reference for concise command
  surfaces and multiple views

### Todoman
A standards-based CLI task manager for VTODO items.
- **Website:** https://todoman.readthedocs.io/ and https://github.com/pimutils/todoman
- **Format:** `.ics` files in a directory
- **Strengths:** file-backed storage, standards-based, create/list/edit todos
- **Relationship to status.py:** useful example of a CLI over editable
  files, though it targets calendar tasks rather than Org headings

### t
A small plain-text task manager with a very lightweight CLI.
- **Website:** https://github.com/sjl/t and https://hg.stevelosh.com/t/
- **Format:** plain-text files
- **Strengths:** minimal surface area, easy manual editing
- **Relationship to status.py:** close in spirit if the goal is a small,
  scriptable text task tool rather than a large task platform

### Taskell
An interactive CLI/TUI Kanban board that stores tasks in Markdown.
- **Website:** https://github.com/smallhadroncollider/taskell
- **Format:** Markdown
- **Strengths:** keyboard-driven board workflow, clean diffs, subtask support
- **Relationship to status.py:** useful reference for terminal UX and
  text-backed project views, though it is more interactive than scriptable

## Key Takeaway

There are many CLI task trackers that use local files, and Org itself
has batch-mode CLI entry points. The gap that `status.py` can fill is
more specific: a conservative command-line tool that treats one Org
task subtree as the source of truth, preserves surrounding prose, and
adds stable task IDs for scripting and LLM context generation.
