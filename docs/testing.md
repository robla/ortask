# Testing Plan

This document defines nine high-value tests for refactoring the ortask suite
into shared library modules. The goal is not exhaustive coverage; it is a small
suite that catches regressions in the behaviors the scripts currently share.

Use `pytest` and small temporary Org files. Tests should avoid real user files
and should not depend on `~/.config/ortask` unless `XDG_CONFIG_HOME` is pointed
at a temporary directory.

## 1. Parse Standard Task Tree

Verify that `core.parse_org()` reads the `* Tasks` subtree when present and
otherwise reads valid TODO/DONE task headings across the file. It should return
`TodoItem` records with IDs, priority cookies, tags, line numbers, and body
lines. Include prose before and after `* Tasks` to prove unrelated sections are
ignored when the explicit section exists.

## 2. Normalize and Match Weekly IDs

Verify that `normalize_id()` and `canonical_id()` treat `26W24`, `26w24`,
`tw26W24`, `tw2026W24`, and `tw2026w24` as the same task lookup key while
preserving the stored ID on parsed items.

## 3. Filter Root TODO Tasks

Given a mixed tree with TODO, DONE, top-level tasks, and subtasks, verify that
task filtering can produce the `orgmgr.py list` view: only root-level parsed
tasks and only TODO tasks by default. Also verify `--all` includes root-level
DONE tasks but still excludes subtasks, including for files without `* Tasks`.

## 4. Discover Local Org File

In temporary project directories, verify task-file lookup order: explicit file
and `ORTASK_FILE` first; then an upward walk from the current directory that
prefers `tasks.org`, `task.org`, exactly one `*.task.org`, and compatibility names
(`TODO.org`, one other `TODO*.org`, `todo.org`). Confirm the nearest directory
wins, multiple same-tier matches are ambiguous, `*.task.org` beats generic
`*.org`, and generic fallback is used only when exactly one exists in the
original current directory.

## 5. Add Top-Level and Subtask

Using a temporary Org file, verify that adding a top-level task allocates the
next `tNNNN` ID and inserts under `* Tasks`. Then add a subtask under an
existing parent and verify it gets the next dotted child ID, is inserted after
the parent’s existing descendants, and preserves unrelated prose.
Also verify that `ortask.py add` creates `tasks.org` only when no task file is
discovered, and does not append `* Tasks` to arbitrary existing Org prose.

## 6. Toggle Task State In Place

Verify that marking a task `done` and reopening it modifies only the matched
heading line. The test should compare surrounding text, body lines, blank
lines, tags, and unrelated tasks before and after the edit.

## 7. Detect Repair Problems

Verify that validation reports duplicate IDs, TODO/DONE headings without valid
IDs, and dotted subtask IDs that do not match the current parent. This protects
the future `repair` implementation even while automatic fixes remain limited.

## 8. Summarize Projects for orgmgr

Build a temporary workspace with multiple project directories, hidden/skipped
directories, and selected Org files. Verify the manager layer lists only valid
projects, reports each project’s selected task file, and returns only top-level
task summaries. Include one project with task headings but no `* Tasks` section,
and one project with no parseable tasks that produces a warning rather than
crashing.

## 9. CLI Smoke Tests

Run the scripts against temporary fixtures with subprocess:

```sh
./ortask.py --file /tmp/example.org list
./ortask.py --file /tmp/example.org show t0001
./orgmgr.py --registry /tmp/workspace list --format json
./projtui.py --registry /tmp/workspace
```

For `projtui.py`, provide input such as `q\n` and assert it exits cleanly after
printing the project list. These smoke tests confirm the top-level scripts still
import the refactored library correctly and keep their basic command contracts.

## Refactor Gate

Before and after each architecture step, run:

```sh
python3 -m py_compile ortask.py orgmgr.py projtui.py
python3 -m pytest -q
```

The first seven tests should pass before moving code out of `ortask.py`; the
manager and smoke tests should pass before removing script-to-script imports.
