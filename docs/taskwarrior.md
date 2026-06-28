# Taskwarrior Workflow Notes

Taskwarrior is a command-line task database. A task is a record with a
description, status, UUID, and optional metadata such as project, tags,
priority, due date, scheduled date, wait date, annotations, dependencies, and
recurrence. Most daily use is adding tasks, filtering them into useful views,
and marking them complete.

## Core Model

Taskwarrior stores tasks outside the files you edit by hand. Commands query and
modify that store, then reports render selected tasks.

Important concepts:

- **Status**: tasks are usually `pending`, `completed`, `deleted`, or waiting.
- **ID**: report numbers are short row IDs for the current view and can change;
  UUIDs are stable.
- **Project**: `project:foo` groups tasks by area of work.
- **Tags**: `+email`, `+home`, or `+blocked` add lightweight labels.
- **Dates**: `due:`, `scheduled:`, `wait:`, `until:`, and `recur:` drive views.
- **Urgency**: Taskwarrior computes a score from metadata; `next` sorts by it.
- **Reports**: named views such as `list`, `next`, `waiting`, and `completed`.

## Basic Workflow

Add a task:

```sh
task add "Draft release notes"
task add project:ortask +docs due:friday "Tighten task guide"
```

List active work:

```sh
task list
task next
task project:ortask
task +docs
task due.before:tomorrow
```

Inspect and update one task:

```sh
task 12 info
task 12 modify priority:H
task 12 annotate "Waiting on review"
task 12 done
```

Task numbers in examples such as `12` come from the current report. If the
report changes, the number may change too. Taskwarrior keeps the underlying UUID
stable, but normal interactive use relies on the short report number.

## Filters and Reports

Taskwarrior’s power comes from filters. A command is usually:

```sh
task <filter> <command>
```

Common filters:

```sh
task project:ortask list
task +docs list
task status:pending list
task due.before:eow list
task priority:H next
task project:ortask +docs due.before:friday next
```

Common reports:

```sh
task list        # pending tasks
task next        # prioritized pending tasks
task waiting     # tasks hidden until a wait date
task completed   # finished tasks
task all         # broad view, including non-pending statuses
```

## Dates, Waiting, and Recurrence

Taskwarrior distinguishes several scheduling ideas:

- `due:friday` means the task is due then.
- `scheduled:monday` means it should start appearing as scheduled work then.
- `wait:tomorrow` hides the task until tomorrow.
- `recur:weekly` creates repeating work.
- `until:` can limit how long a task or recurrence remains relevant.

Example:

```sh
task add project:newsletter due:wed recur:weekly "Publish weekly update"
task add wait:tomorrow "Follow up after meeting"
```

## Notes and Changes

Use annotations for timestamped notes:

```sh
task 12 annotate "Posted draft link in chat"
```

Use `modify` for metadata changes:

```sh
task 12 modify project:ortask +review due:monday
task 12 modify -review
```

Use `delete` when a task should disappear from active work without being marked
complete:

```sh
task 12 delete
```

## Mapping to ortask.py

`ortask.py` uses Org headings as the durable record instead of a task database.
The nearest mental translation is:

| Taskwarrior | ortask.py |
| --- | --- |
| `task add "Text"` | `ortask.py add "Text"` |
| `task list` | `ortask.py list` |
| `task 12 info` | `ortask.py show t0001` |
| `task 12 done` | `ortask.py done t0001` |
| `task 12 annotate ...` | edit notes under the Org heading |
| `project:` / `+tag` filters | separate files, headings, or future metadata |
| recurring weekly task | `ortask.py apply --template weekly` |

The biggest practical difference is identity. Taskwarrior’s visible task
numbers are report-local; `ortask.py` IDs such as `t0001` or `tw26W24.1` are
stored in the heading and intended to remain stable in prose, commits, and
scripts.

Example Org task tree:

```org
* Tasks
** TODO t0001 Draft release notes
*** TODO t0001.1 Collect changes
*** DONE t0001.2 Write first draft
```

Taskwarrior encourages computed views over structured metadata. `ortask.py`
keeps the structure visible in the file and uses commands for small, predictable
edits.
