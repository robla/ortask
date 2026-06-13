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

## The three tools

- **`ortask.py`** — local CLI scoped to a single Org task file. Verbs: `list`,
  `show`, `add`, `done`, `open`, `repair`. This is the core tool. The intended
  shell alias is `ort`. Spec: `docs/ortask.md`.
- **`orgmgr.py`** — global manager across many projects. Verbs: `list`
  (read-only task overview), `migrate` (initialize the shared
  `~/.config/ortask/ortask.ini` `[projects]` registry, importing any legacy
  `projdir`), and `projadd` (register one directory in that registry; gated
  until `migrate` runs). It edits only its own config, never Org content —
  `ortask.py` owns local task editing. Spec: `docs/orgmgr.md`.
- **`projtui.py`** — interactive terminal menu: pick a project, pick a task, see
  a focused work prompt, optionally mark DONE or open in an editor. Delegates
  writes to the same parser/writer paths as `ortask.py`. Spec:
  `docs/interactive.md`.

## Running

```sh
./ortask.py                        # list open tasks (default: todo.org)
./ortask.py list --todo            # open tasks only
./ortask.py --file /path/to.org    # operate on a different file
./orgmgr.py list                   # overview of all projects' top-level tasks
./projtui.py                       # interactive project/task menu
```

## Default file resolution (ortask.py)

Despite older docs that mention `README.org`, the implemented default order is:

1. `--file FILE` (wins over everything)
2. `ORTASK_FILE` environment variable
3. `todo.org` in the current directory
4. `tasks.org` in the current directory
5. the first `*.org` file alphabetically (warns if multiple)

`docs/format.md` and the TUI/orgmgr specs describe a richer intended discovery
direction — prefer dedicated `TODO.org` / `TODO-ProjectName.org` files, then
`todo.org`, then larger files like `README.org` that contain a `* Tasks`
section. That broader discovery is **specified but not yet implemented** in
`ortask.py`'s probe order; treat it as planned behavior.

## Current state vs. planned state

- **`ortask.py`** (~640 lines) implements the TODO/DONE keyword parser and all
  subcommands. `repair` *detects* problems (duplicate IDs, headings under
  `* Tasks` missing a valid ID, subtask IDs that don't match their parent's
  prefix) and reports them, but auto-fix — renumbering and ID assignment — is
  deferred. `repair --dry-run` exits 2 if problems are found; `repair` without
  `--dry-run` reports and exits 0 without modifying the file.
- **`orgmgr.py`** implements `list` (read-only), plus `migrate` and `projadd`
  for the shared `[projects]` registry (config-only writes; `projadd` is gated
  behind `migrate`). The registry is written but not yet *consumed* by
  `list`/`projtui` — those still use the `projdir` workspace. Future verbs
  (`projrm`, `scan`, `doctor`) remain specified but unimplemented.
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
rename). Edits touch only the `* Tasks` subtree and never reformat the file.

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

No test suite exists yet. When adding tests, use pytest with fixture org
documents covering: heading parsing with/without priorities and tags, weekly-ID
parsing and normalization, `--items` limits, ID allocation, and round-trip edits
that preserve unrelated lines. For `projtui.py`/`orgmgr.py`, separate workflow
logic from terminal I/O and assert that read paths never write to disk.

## Design docs

- `docs/ortask.md` — man-page-style `ortask.py` subcommand reference (planned-behavior source of truth)
- `docs/orgmgr.md` — spec for the global `orgmgr.py` manager
- `docs/interactive.md` — spec for the `projtui.py` interactive TUI
- `docs/format.md` — Org format conventions and file-discovery direction ("wiki way")
- `docs/claude-ortask-design.org` — architecture and format spec
- `docs/codex-ortask-design.org` — phased implementation, testing emphasis
- `docs/gemini-ortask-design.org` — LLM integration, robust regex, atomic writes
- `docs/taskwarrior.md` — mental model mapping between Taskwarrior and ortask
- `docs/adjacent-trackers.md` — survey of Taskwarrior, todo.txt-cli, dstask, etc.
- `docs/naming.md` — how the name `ortask` was chosen
- `docs/llm-log.org` — running one-line log of substantive repo changes by LLMs

## Conventions

- Subcommands are verbs (`list`, `show`, `add`, `done`, `open`, `repair`),
  aligning with Taskwarrior conventions.
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
