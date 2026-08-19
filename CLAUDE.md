# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ortask is a small suite of Python CLI tools for querying and editing TODO tasks
stored in org-mode files. Tasks live under a `* Tasks` subtree, using standard
`TODO`/`DONE` keywords and stable task IDs such as `t0001`, `t0001.1`, and
week-based `tw26W24`.

The guiding philosophy is the "wiki way" (see `docs/format.md`): plain text that
a human can read and edit, with only enough convention for tooling to help. The
tools are intended as training wheels for org-mode users who are not (yet) Emacs
power users — the Org file always remains the source of truth, and every edit
must stay valid even if the tools are never run again.

All scripts are stdlib-only (Python 3.10+, no external dependencies).

## The tools

- **`ortask.py`** — local CLI scoped to a single Org task file. Verbs: `list`,
  `show`, `add`, `done`, `open`, `repair`, `apply` (instantiate the `* Template`
  subtree as new weekly tasks; see `docs/templates.md`). This is the core tool.
  The intended shell alias is `ort`. Spec: `docs/ortask.md`.
- **`orgmgr.py`** — the project layer across many projects, organized by a
  registry directory (one subdirectory per project, each holding symlinks to the
  project and its `.org` task file). `ortask.ini` records it as `[projects]
  registry`. Verbs: `list` (read-only task overview), `migrate` (record the
  registry in `~/.config/ortask/ortask.ini`), `pcd` (write a project's directory
  stack; see `docs/projdirs.md`), and `projadd` (create one project's symlink
  subdirectory under the registry). It edits only config and registry symlinks,
  never Org content — `ortask.py` owns local task editing. Specs:
  `docs/orgmgr.md` for the command, `docs/projects.md` for the registry model.
  **Planned:** renames to `projmgr.py` (`pmgr`), with `projadd` becoming `add`
  and `-i` aliased `ptui` — see the Project Navigator section of
  `docs/roadmap.md`.
- **`projtui.py`** — despite the name, library code rather than a third tool:
  2103 lines imported by both scripts, holding the `orti` task workspace
  (`OrgBuffer`, `InteractiveTaskController`) plus ~105 lines of project browser.
  It is slated to move into `ortasklib/` and stop being a script. Spec:
  `docs/interactive.md`.

## Running

```sh
./ortask.py                        # list open tasks (default: task.org)
./ortask.py list --todo            # open tasks only
./ortask.py --file /path/to.org    # operate on a different file
./orgmgr.py list                   # overview of all projects' top-level tasks
./projtui.py                       # interactive project/task menu
```

## Default file resolution (ortask.py)

Despite older docs that mention `README.org`, the implemented default order is:

1. `--file FILE` (wins over everything)
2. `ORTASK_FILE` environment variable
3. Walk upward from the current directory, nearest first, for `task.org`
4. In the same upward walk, exactly one `*.task.org`
5. Legacy names in the same upward walk: `TODO.org`, one other `TODO*.org`,
   `todo.org`, then `tasks.org`
6. exactly one generic `*.org` in the original current directory

Ambiguous same-tier matches are errors. Do not silently choose alphabetically.

## Current state vs. planned state

- **`ortask.py`** implements TODO/DONE plus terminal MOOT parsing, retains
  SUPERSEDED as a compatibility alias, and supports all subcommands. `repair`
  *detects* problems (duplicate IDs, headings under
  `* Tasks` missing a valid ID, subtask IDs that don't match their parent's
  prefix) and reports them, but auto-fix — renumbering and ID assignment — is
  deferred. `repair --dry-run` exits 2 if problems are found; `repair` without
  `--dry-run` reports and exits 0 without modifying the file.
- **`orgmgr.py`** implements `list` (read-only), `migrate` (record the registry
  in `ortask.ini`), and `projadd` (create a project's symlink subdirectory under
  the registry). `list`/`migrate`/`projadd` and `projtui.py` all resolve the
  registry via `manager.resolve_registry()` (`--registry` > `[projects]
  registry` > `~/Projects`). It also implements `pcd`. Future verbs (`projrm`,
  `doctor`) remain specified but unimplemented; `scan` is explicitly not
  planned, per `docs/projects.md`.
- **`projtui.py`** implements the minimal numbered-menu workflow.
- **Shared library (done):** reusable logic lives in the `ortasklib/` package
  (`core`, `tasks`, `manager`); the three scripts are thin front-ends that no
  longer import each other. See `docs/architecture.md`. Everything stays
  stdlib-only even if the TUI later adopts optional dependencies.

## Key files

- `ortasklib/` — shared package: `core.py` (parse/IDs/discovery/atomic writes),
  `tasks.py` (local formatting/show/edit/validation), `manager.py` (project
  discovery/config/summaries)
- `ortask.py` — local task CLI (the core tool)
- `orgmgr.py` — global read-only project/task overview
- `projtui.py` — interactive project/task TUI
- `tests/test_ortask_suite.py` — pytest suite; doubles as the refactor gate
- `README.org` — project docs (no longer the default task data file)
- `todo.org` — the actual task data file for this repo
- `docs/` — specs and design notes (see below); `docs/ortask.md` is the source
  of truth for planned `ortask.py` behavior
- `AGENTS.md`, `GEMINI.md` — sibling agent-instruction files; keep CLAUDE.md
  roughly consistent with them

## Architecture

Three layers, now housed in `ortasklib/` (see `docs/architecture.md`):

1. **Parser**: `core.parse_org(text) -> list[TodoItem]` — regex-based, tracks
   source line numbers for each task and captures its body lines.
2. **Query/mutate**: `core` filtering/ID helpers plus `tasks` edit helpers
   (`add_task`, `change_state`, …) that take text and return new line lists.
3. **Writer**: minimal text patching — change only matched heading lines (and
   surgically insert for `add`); preserve everything else.

Shared modules never parse CLI args or call `sys.exit`; the scripts own that.

Write operations use atomic file replacement (write to a temp file, then
rename), resolving task-file symlinks before replacement. Edits touch only the
`* Tasks` subtree and never reformat the file.

## Task heading and ID format

```org
* Tasks
** TODO t0005 Some task title
** TODO [#A] t0006 Urgent task with priority
** TODO tw26W24 Week of June 8's tasks for ElectoramaWeekly
*** TODO tw26W24.1 A subtask of the weekly parent
** DONE t0001.2 Completed subtask       :research:
```

The parser's heading regex (priority cookie and trailing tags optional):

```
^\*+\s+(?P<state>TODO|DONE)\s+(?:\[#(?P<priority>[A-C])\]\s+)?(?P<id>t\d{4}(?:\.\d+)*)\s+(?P<text>.*?)(?:\s+:(?P<tags>[\w:]+):)?\s*$
```

ID rules:

- Top-level: `t` + 4 digits (`t0001`–`t9999`), assigned sequentially.
- Weekly: `tw` + ISO-like week for recurring week-scoped work. Accepted forms
  are `tw26W24`, `tw26w24`, `tw2026W24`, `tw2026w24`. Command input may omit the
  `tw` prefix (`show 26W24` → `tw26W24`), and two- vs four-digit years compare as
  the same week (`2026w24` → `tw26W24`).
- Subtasks/nesting: parent ID + `.N` (`t0001.3`, `tw26W24.1`, `t0001.3.1`).
- IDs are permanent and never reused, even after deletion.

Task-specific URLs go as plain body lines under the relevant task (not as extra
`*` headings), so they show up in `ortask.py show` and in the TUI's task detail.

## Testing

`tests/test_ortask_suite.py` is the pytest suite and the refactor gate; run it
with `python3 -m pytest tests/` before and after any change. It covers heading
parsing with and without priorities and tags, weekly-ID normalization, ID
allocation, round-trip edits that preserve unrelated lines, XDG-isolated
registry behavior, and PTY coverage of the bounded inline session. See
`docs/testing.md`. New tests follow the same shape: fixture Org documents, and
for `projtui.py`/`orgmgr.py`, workflow logic separated from terminal I/O so read
paths can be asserted never to write to disk.

## Design docs

- `docs/ortask.md` — man-page-style `ortask.py` subcommand reference (planned-behavior source of truth)
- `docs/orgmgr.md` — spec for the project-layer `orgmgr.py` command
- `docs/projects.md` — what a project is, and the registry model (the
  multi-project counterpart to `docs/format.md`)
- `docs/projdirs.md` — the `pcd` project directory stack
- `docs/roadmap.md` — design context for multi-step work, keyed to task IDs
- `docs/interactive.md` — spec for the interactive TUI
- `docs/format.md` — Org format conventions and file-discovery direction ("wiki way")
- `docs/architecture.md` — package layout, layer boundaries, script responsibilities
- `docs/claude-ortask-design.org` — architecture and format spec
- `docs/codex-ortask-design.org` — phased implementation, testing emphasis
- `docs/gemini-ortask-design.org` — LLM integration, robust regex, atomic writes
- `docs/taskwarrior.md` — mental model mapping between Taskwarrior and ortask
- `docs/adjacent-trackers.md` — survey of Taskwarrior, todo.txt-cli, dstask, etc.
- `docs/naming.md` — how the name `ortask` was chosen
- `docs/llm-log.org` — running one-line log of substantive repo changes by LLMs

## Conventions

- Subcommands are verbs (`add`, `apply`, `archive`, `done`, `list`, `open`,
  `repair`, `show`), aligning with Taskwarrior conventions. Register and
  document all subcommands alphabetically, including `help`, and keep dispatch
  and shell completion tables in that order.
- File edits are conservative: only touch the `* Tasks` subtree, only rewrite
  matched lines, never reformat the whole file. Atomic writes only.
- Task IDs are permanent and never reused.
- Keep the Org file idiomatic and minimal — don't invent custom Org extensions.
  The aim is a file that an Emacs/org user would find unremarkable.
- **LLM change log:** when making any user-requested repository change, add one
  concise entry to `docs/llm-log.org` in the same turn, using the existing
  single-line Org format (`** Claude [YYYY-MM-DD Ddd HH:MM]: short description`).
  Use the model name actually doing the work. Do not log pure investigation or
  no-op commands. (This mirrors the convention in `AGENTS.md`.)
