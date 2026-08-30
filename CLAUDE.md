# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ortask is a small suite of Python CLI tools for querying and editing TODO tasks
stored in org-mode files. Tasks live under a `* Tasks` subtree, using standard
`TODO`/`DONE`/`MOOT` keywords and stable task IDs such as `t0001`, `t0001.1`,
and week-based `tw26W24`.

The guiding philosophy is the "wiki way" (see `docs/format.md`): plain text that
a human can read and edit, with only enough convention for tooling to help. The
tools are intended as training wheels for org-mode users who are not (yet) Emacs
power users — the Org file always remains the source of truth, and every edit
must stay valid even if the tools are never run again.

Python 3.10+. Core CLI behavior is stdlib-only. `prompt_toolkit` enables the
full interactive UI and Rich improves numbered tables; both must degrade to a
working fallback when absent. `pytest` is needed only to run the tests.

## The tools

- **`ortask.py`** — local CLI scoped to a single Org task file. Verbs: `add`,
  `apply` (instantiate the `* Template` subtree as new weekly tasks; see
  `docs/templates.md`), `archive` (move DONE subtrees to the stock
  `<file>_archive`), `done`, `help`, `list`, `open`, `repair`, `show`. `-i`
  opens the interactive task workspace. This is the core tool. Intended
  aliases: `ort`, and `orti` for `-i`. Spec: `docs/ortask.md`.
- **`projmgr.py`** — the project layer across many projects, organized by a
  registry directory (one subdirectory per project, each holding symlinks to
  the project and its `.org` task file). `ortask.ini` records it as
  `[projects] registry`. Verbs: `add` (register the project you are in),
  `cdproj` (write a project's directory stack; see `docs/cdproj.md`), `help`,
  `init` (record the registry in `~/.config/ortask/ortask.ini`), `list`
  (read-only task overview), `migrate` (move legacy per-entry private directory
  files into the registry index), `repair` (diagnose/fix registry problems;
  `doctor` survives as a deprecated alias), `rm` (remove one registry entry),
  `set-dirs` (write a directory stack into one `projects.org` section). `projadd`
  remains a deprecated alias for `add`. `-i` opens the project navigator. Project-level writes are limited to
  config, the registry (including its index), and explicit output files;
  `ortask.py` owns task content. Intended aliases: `pmgr`, and `ptui` for `-i`.
  Specs: `docs/projmgr.md` for the command, `docs/projects.md` for the registry
  model. Renamed from `orgmgr.py` on 2026-08-19.
- **`misc/`** — the shell half. `cdproj.func.sh` defines `cdproj`, which applies
  a stack to the current shell and whose `-s` saves the live stack back through
  `pmgr set-dirs`; only the shell can change the shell's own directory stack,
  which is why this part is not Python. Also `ortask-completion.bash` and
  `projmgr.aliases.sh`.

## Running

```sh
./ortask.py                        # list open tasks (default: tasks.org)
./ortask.py list --todo            # open tasks only
./ortask.py --file /path/to.org    # operate on a different file
./projmgr.py list                  # overview of all projects' top-level tasks
./projmgr.py -i                    # interactive project navigator (ptui)
python3 -m pytest tests/           # the refactor gate
```

## Default file resolution (ortask.py)

1. `--file FILE` (wins over everything)
2. `ORTASK_FILE` environment variable
3. An upward walk from the current directory. In each directory, in this order:
   `tasks.org`, `task.org`, exactly one `*.task.org`, `TODO.org`, exactly one
   other `TODO*.org`, `todo.org`
4. Exactly one generic `*.org` in the *original* directory, as a last resort

The whole ladder is tried in one directory before moving to its parent, so a
nested project never inherits its parent's file by accident. Ambiguous
same-tier matches are errors. Do not silently choose alphabetically.

`projmgr.py` uses two stricter forms: `discover_org_file` probes a single
directory without walking up, and `preferred_task_file_in` additionally refuses
the generic `*.org` fallback, which makes it usable as "does this directory look
like a project root?"

## Current state vs. planned state

- **`ortask.py`** implements every subcommand, parses `MOOT` as terminal, and
  retains `SUPERSEDED` as a compatibility alias. `repair` detects problems,
  exits 0 if clean, 2 if problems remain, and 1 on unreadable subject or non-TTY
  refusal (unless `--dry-run` or `--force`). `info` reports file metadata and
  database status (supports `--file` and `--format json`).
- **`projmgr.py`** implements every specified verb. `repair` checks registry
  health (`doctor` is a deprecated alias for `repair --dry-run`). `info` displays
  resolved project context, task counts, and registry metadata (supports
  `--name`, `--path`, `--file`, `--format json`). All commands resolve the
  registry via `manager.resolve_registry()` (`--registry` > `[projects]
  registry` > `~/Projects`) and enumerate it via `manager.discover_projects()`.
  `scan` is explicitly not planned, per `docs/projects.md`.
- **Registry index (done 2026-08-21, `t0026`):** a project's *private*
  directory stack lives in `<registry>/projects.org` — one top-level heading per
  registry entry, with a `** Directories` child. The entry's symlinks are still
  the membership marker, but everything else migrated.
- **Activity log (done 2026-08-22, `t0033`):** `<registry>/log/YYYY-MM.jsonl`
  records mutations across both tools, append-only, gated on `[log] enabled = true`
  in `ortask.ini` or `ORTASK_LOG=on`, read through `ort log`.
- **`docs/ortask.md` / `docs/projmgr.md`** are the command references for all verbs.
- **Shared library (done):** reusable logic lives in `ortasklib/` (`core`,
  `tasks`, `manager`, `menu`, `taskui`); the two scripts are thin front-ends
  that do not import each other. See `docs/architecture.md`.
- **`orglib/` (started 2026-08-21):** a *peer* package, not part of
  `ortasklib`. It holds the Org syntax (`syntax.py`: heading regexes,
  `TodoItem`, `parse_org`, top-level `parse_directories`, and source-backed
  `parse_project_directories`, and `parse_project_section` for index metadata)
  plus a small `parse(text) -> Document` boundary.
  `Document.directories(project)` returns source spans and distinguishes a
  missing project, a missing section, and an empty one. The package imports
  nothing outside the standard library, and the dependency runs `ortasklib` →
  `orglib` only, enforced by a test that blocks `ortasklib` at the meta-path.
  `core.py` re-exports the moved names, so older callers still work. The intent
  is that a different Org backend could be substituted later; see
  `docs/orglib.md`.
- **Open, and worth knowing before starting work:** `t0020` — the `parse_org`
  call sites in `tasks.py`, `taskui.py`, and `ortask.py` still name `core`;
  route new read paths through `orglib.parse()` instead. `t0023` — task files
  do not yet declare `#+TODO:`, so other Org parsers read `MOOT` as ordinary
  heading text. `t0032` — `docs/projmgr.md` and `docs/ortask.md` now spec
  `pmgr repair` (replacing `doctor`) and an `info` verb in both tools; those two
  sections are marked `**Status: specified, not implemented**` and everything
  else still says `doctor`, which is what ships.

## Key files

- `orglib/` — standalone Org syntax package: `syntax.py` (heading regexes,
  `TodoItem`, task and directory parsing, source spans), `__init__.py` (the
  `parse()`/`Document` boundary). Imports nothing outside the stdlib.
- `ortasklib/` — shared package: `core.py` (IDs/discovery/atomic writes, and
  re-exports of `orglib.syntax`), `tasks.py` (local formatting/show/edit/
  validation), `manager.py` (registry discovery/config/index/summaries),
  `menu.py` (bounded inline application), `viewstate.py` (stdlib-only filter/
  sort/direction model shared by `orti` and `ptui`), `viewui.py` (the `v` view
  options screen), `taskui.py` (task list and issue workspace, shared by `orti`
  and `ptui`)
- `ortask.py` — local task CLI (the core tool)
- `projmgr.py` — the project layer: registry, index, project list, directory
  stacks
- `misc/` — shell integration: the `cdproj` function, completion, aliases
- `tests/test_ortask_suite.py` — pytest suite; doubles as the refactor gate
- `README.org` — project docs (no longer the default task data file)
- `todo.org` — the actual task data file for this repo
- `docs/` — specs and design notes (see below); `docs/ortask.md` is the source
  of truth for planned `ortask.py` behavior
- `AGENTS.md`, `GEMINI.md` — sibling agent-instruction files; keep CLAUDE.md
  roughly consistent with them

## Architecture

Three layers, split across `orglib/` and `ortasklib/` (see
`docs/architecture.md`):

1. **Parser**: `orglib.syntax.parse_org(text) -> list[TodoItem]` — regex-based,
   tracks source line numbers for each task and captures its body lines. Still
   reachable as `core.parse_org`. New read paths should go through the boundary
   instead: `orglib.parse(text).tasks()`. Directory lookups already do
   (`orglib.parse(text).directories(name)`), and they return the source span
   they read, which is what lets a writer rewrite one section of a shared file.
2. **Query/mutate**: `core` filtering/ID helpers plus `tasks` edit helpers
   (`add_task`, `change_state`, …) that take text and return new line lists.
3. **Writer**: minimal text patching — change only matched heading lines (and
   surgically insert for `add`); preserve everything else.

Shared modules never parse CLI args or call `sys.exit`; the scripts own that.
Keep `orglib` generic: ortask's state names, ID grammar, and section
conventions belong in `ortasklib`.

Every write touches only its own region and never reformats the file: task
edits stay inside `* Tasks`, `apply` reads `* Template`, `archive` moves
subtrees to the archive file, and `set-dirs` rewrites one project's
`** Directories` section in `projects.org`. Writes use atomic file replacement
(temp file, then rename), resolving symlinks before replacement.

## Task heading and ID format

```org
* Tasks
** TODO t0005 Some task title
** TODO [#A] t0006 Urgent task with priority
** TODO tw26W24 Week of June 8's tasks for ElectoramaWeekly
*** TODO tw26W24.1 A subtask of the weekly parent
** DONE t0001.2 Completed subtask       :research:
```

The parser's heading regex, `orglib.syntax.HEADING_RE` (priority cookie and
trailing tags optional):

```
^(?P<stars>\*+)\s+(?P<state>TODO|DONE|MOOT|SUPERSEDED)\s+(?:\[#(?P<priority>[A-C])\]\s+)?(?P<id>t(?:\d{4}|w(?:\d{2}|\d{4})[Ww]\d{2})(?:\.\d+)*)\s+(?P<text>.*?)(?:\s+:(?P<tags>[\w:]+):)?\s*$
```

ID rules:

- Top-level: `t` + 4 digits (`t0001`–`t9999`), assigned sequentially.
- Weekly: `tw` + ISO-like week for recurring week-scoped work. Accepted forms
  are `tw26W24`, `tw26w24`, `tw2026W24`, `tw2026w24`. Command input may omit the
  `tw` prefix (`show 26W24` → `tw26W24`), and two- vs four-digit years compare as
  the same week (`2026w24` → `tw26W24`).
- Subtasks/nesting: parent ID + `.N` (`t0001.3`, `tw26W24.1`, `t0001.3.1`).
- IDs are permanent and never reused, even after deletion.
- An ID names one task for its whole life. If the work is redefined, close the
  old ID (`DONE`, or `MOOT` when it is no longer worth doing) and open a new
  one. Reopening a task is fine; rewriting an open task's subject into a
  different task is not. Narrowing scope or fixing wording is ordinary
  curation --- replacing what the task *is* is not.

Task-specific URLs go as plain body lines under the relevant task (not as extra
`*` headings), so they show up in `ortask.py show` and in the TUI's task detail.

## Testing

`tests/test_ortask_suite.py` is the pytest suite and the refactor gate; run
`python3 -m pytest tests/` before and after any change (195 tests as of
2026-08-21). `python3 -m py_compile ortask.py projmgr.py ortasklib/*.py
orglib/*.py` is a quick syntax check. Coverage includes heading parsing with and
without priorities and tags, weekly-ID normalization, ID allocation, round-trip
edits that preserve unrelated lines, XDG-isolated registry behavior, and PTY
coverage of the bounded inline session. See `docs/testing.md`. New tests follow
the same shape: fixture Org documents, and for `taskui`/`projmgr.py`, workflow
logic separated from terminal I/O so read paths can be asserted never to write
to disk.

**Data safety:** never run a mutating command against `todo.org` or the
configured registry just to see whether an implementation works. Use temporary
fixtures, `--help`, `--dry-run`, or the test suite. Ask before running
`ortask.py add`/`archive`/`done`/`repair` or `projmgr.py add`/`migrate`/`rm`/
`set-dirs` on real data.

## Design docs

- `docs/ortask.md` — man-page-style `ortask.py` subcommand reference (planned-behavior source of truth)
- `docs/projmgr.md` — spec for the project-layer `projmgr.py` command
- `docs/projects.md` — what a project is, and the registry model (the
  multi-project counterpart to `docs/format.md`)
- `docs/config.md` — every setting the suite reads, and the registry index
- `docs/cdproj.md` — the `cdproj` project directory stack, and saving it back
- `docs/templates.md` — the `* Template` subtree and `ort apply`
- `docs/roadmap.md` — design context for multi-step work, keyed to task IDs
- `docs/interactive.md` — spec for the interactive TUI
- `docs/format.md` — Org format conventions and file-discovery direction ("wiki way")
- `docs/architecture.md` — package layout, layer boundaries, script responsibilities
- `docs/orglib.md` — the parser boundary, a survey of external Org libraries,
  and each model's assessment of whether to adopt one
- `docs/testing.md` — what the suite covers and how to add to it
- `docs/ecosystem.md` — how ortask relates to the author's other tools
- `docs/logging.md` — the planned event log: what gets recorded, where, and
  how it is read back
- `docs/extensions.md` — how programs outside the suite consume its data,
  including the zim journal integration
- `docs/claude-ortask-design.org` — the origin design doc, annotated with what shipped
- `docs/codex-ortask-design.org` — phased implementation, testing emphasis
- `docs/gemini-ortask-design.org` — LLM integration, robust regex, atomic writes
- `docs/taskwarrior.md` — mental model mapping between Taskwarrior and ortask
- `docs/adjacent-trackers.md` — survey of Taskwarrior, todo.txt-cli, dstask, etc.
- `docs/naming.md` — how the name `ortask` was chosen
- `docs/llm-assessments.md` — each model's standing assessment of the project
- `docs/llm-log.org` — running one-line log of substantive repo changes by LLMs

## Conventions

- Subcommands are verbs, aligning with Taskwarrior conventions. Register and
  document them alphabetically, including `help`, and keep dispatch, help, and
  shell completion tables in that order.
- File edits are conservative: touch only the region the command owns, rewrite
  only matched lines, never reformat the whole file. Atomic writes only.
- Task IDs are permanent, never reused, and never repurposed: redefined work
  gets a new ID, and the old one is closed `DONE` or `MOOT`.
- Keep the Org file idiomatic and minimal — don't invent custom Org extensions.
  The aim is a file that an Emacs/org user would find unremarkable.
- Docs stay objective and fact-based, including about other Org libraries and
  tools.
- **LLM change log:** when making any user-requested repository change, add one
  entry to `docs/llm-log.org` in the same turn, in the existing single-line Org
  format (`** Claude [YYYY-MM-DD Ddd HH:MM TZ]: short description`). One line,
  present tense, naming the model actually doing the work. Re-read the end of
  the file immediately before appending — other models append concurrently. Do
  not log pure investigation or no-op commands. (This mirrors the convention in
  `AGENTS.md`.)
