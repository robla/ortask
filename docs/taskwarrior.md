# Taskwarrior Workflow Notes for ortask.py

This document is for two related readers:

- someone who wants to understand how Taskwarrior workflows feel without
  adopting Taskwarrior
- a Taskwarrior user who wants a quick mental model for `ortask.py`

It is not a pitch to replace Taskwarrior. The two tools solve adjacent
problems with different tradeoffs.

## The Short Version

Taskwarrior is a feature-rich task database with strong filtering,
reports, dates, recurrence, and workflow metadata. `ortask.py` is a
much narrower idea: treat one Org-mode `* Tasks` subtree as the source
of truth and make conservative CLI reads and edits against that text.

If you know Taskwarrior, the closest way to think about `ortask.py` is:

- keep the command-oriented feel
- drop the database, urgency engine, and report system
- make the file itself the canonical store
- use stable task IDs in headings instead of short numeric IDs

## Taskwarrior Workflow, in Broad Strokes

A typical Taskwarrior workflow looks like this:

1. Add tasks quickly with short commands.
2. Filter aggressively by project, tag, due date, status, or custom report.
3. Mark tasks done, start/stop work, postpone, annotate, or modify fields.
4. Let Taskwarrior compute views such as next, waiting, overdue, or ready.

The important idea is not any one command. It is that Taskwarrior keeps
structured task data in its own store and gives you many derived views
over that data.

## The ortask.py Mental Model

`ortask.py` keeps much less state. The working model is:

- tasks live in an Org file
- one `* Tasks` subtree is the task database
- tasks are plain Org headings with `TODO` or `DONE`
- each task has a stable ID such as `t0001` or `t0001.2`

Example:

```org
* Tasks
** TODO t0001 Plan release
*** TODO t0001.1 Write release notes
*** DONE t0001.2 Tag repository
```

That means the file is both the storage layer and the human-readable
interface. You inspect it with your editor, grep, git, or shell tools,
and `ortask.py` exists to make common actions easier and safer.

## Command Mapping

These are rough workflow equivalents, not exact feature matches.

| Taskwarrior habit | ortask.py equivalent | Notes |
| --- | --- | --- |
| `task add "Buy milk"` | `ortask.py add "Buy milk"` | New stable ID assigned automatically. |
| `task list` | `ortask.py list` | Meant for simple task listing, not rich reports. |
| `task <id> done` | `ortask.py done t0001` | Uses stable text ID, not a short numeric row ID. |
| `task <id> info` | `ortask.py show t0001` | Shows one task with its local context. |
| `task <id> modify ...` | limited | The design is intentionally narrower. |
| `task <id> start` / `stop` | missing | No active-state or time-tracking workflow. |
| `task project:foo` or `+work` filters | mostly missing | Intended CLI is much simpler than Taskwarrior filtering. |

## Where ortask.py Is Deliberately Narrower

If you are thinking in Taskwarrior terms, these are the biggest gaps:

- no urgency score or report engine
- no built-in project, tag, due-date, or waiting filters yet
- no recurrence engine
- no start/stop tracking
- no equivalent of Taskwarrior contexts, hooks, or rc customization

That narrower scope is intentional. The design goal is not "Taskwarrior
for Org files." It is "a conservative CLI for one editable Org task
tree."

## What a Taskwarrior User Should Pay Attention To

The most important differences are structural:

- IDs are stable and meant to be referenced in prose, commits, and chat.
- Subtasks are hierarchical in the file, not modeled as a separate
  dependency system.
- Manual editing is expected. Reordering headings in Emacs or another
  editor is part of the workflow, not something the CLI fights.
- Surrounding prose matters. Notes, drawers, and non-task sections are
  expected to survive CLI edits unchanged.

## A Better Comparison

Taskwarrior is a mature general-purpose task manager with a powerful
query model. `ortask.py` is closer to a thin command layer over a text
file that you already want to keep in version control and edit by hand.

If you want computed workflow views, Taskwarrior is the better model.
If you want a readable Org file with light CLI assistance, `ortask.py`
is aiming at a different niche.
