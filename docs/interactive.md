# Project TUI for ortask.py

This document specifies a future `projtui.py` helper for using `ortask`
from a project-management workspace such as `proj2026`. It replaces the
earlier idea of simply stepping through one `todo.org` task after another.
The desired flow is: choose a project, show that project's tasks as a menu,
select one task, and receive a focused prompt for what to do next.

## Goal

`projtui.py` should help the user stay focused on a current project without
turning Org-mode into a separate task database. Org files remain the source
of truth. The TUI is only a guided layer over project symlinks and the task
headings parsed by `ortask.py`.

Primary usage from a workspace directory:

```sh
./ortask/ortask/projtui.py
```

When run from `proj2026`, the first useful target is promoting recent
Electorama Weekly episodes through the `elweek/` project parent.

Current minimal implementation:

```sh
./projtui.py
./projtui.py --projdir /home/robla/tmpsorta/proj2026
```

The first version is intentionally plain: numbered project menus, numbered
task/heading menus, detail display, editor launch, and `DONE` marking for
ortask-compatible tasks with IDs. For non-task Org files such as the current
`TODO-ElWeek.org` template, it displays headings as read-only reference.

By default, `projtui.py` looks in `~/Projects`. A global config file can
override that default:

```ini
[projtui]
projdir = ~/tmpsorta/proj2026
```

The config file lives at `~/.config/ortask/projtui.ini`, or under
`$XDG_CONFIG_HOME/ortask/projtui.ini` when `XDG_CONFIG_HOME` is set.
Command-line `--projdir` wins over the config file. On startup, the tool
prints the directory it is scanning, for example:

```text
Finding project in ~/tmpsorta/proj2026
```

## Workspace Discovery

The tool should treat each immediate subdirectory as a possible project
context when it contains or points to an Org task file. In `proj2026`, that
means examples such as:

- `elweek/` with `TODO-ElWeek.org`
- `ortask/` with `todo.org`

The initial screen should be a project menu:

```text
Project:
  1. elweek     TODO-ElWeek.org
  2. ortask     todo.org
  q. quit
```

Do not follow every nested directory looking for tasks. Keep discovery
predictable and explain skipped entries only in a debug or verbose mode.

## Task Menu Workflow

After project selection, display the project's open tasks as a menu rather
than automatically advancing through them:

```text
elweek tasks:
  1. [#A] publish latest episode promo post
  2. draft social copy for last week's episode
  3. update episode links page
  r. refresh
  b. back to projects
  q. quit
```

The user chooses what to work on. This is important: the TUI should support
focus without hiding judgment or forcing the next task in file order.

Default ordering should still be useful:

1. open tasks before done tasks
2. higher Org priority first: `[#A]`, then `[#B]`, then `[#C]`
3. parent tasks before subtasks
4. file order as the final tie-breaker

## Focus Prompt

Selecting a task should show a compact work prompt, not immediately mutate
the file:

```text
Task: publish latest episode promo post
https://www.reddit.com/r/electorama/submit

Actions:
  d. mark DONE
  e. open in editor
  b. back to task menu
```

The prompt should show task details immediately when a task is selected,
including descendant subtasks. Detail display is capped at 20 lines with a
truncation note so selecting a large parent task stays readable. For the
Electorama Weekly case, links used to complete the task should appear as plain
body lines under the relevant task, so the TUI displays them before the action
menu. When the selected task has direct subtasks, list them as numbered menu
items so the user can drill into one without returning to the full task list.
Opening the editor for a selected task should jump to that task's line when
the configured editor supports line arguments.

## Operations

Initial operations should be small and map to existing or planned `ortask.py`
commands:

| TUI action | Command equivalent |
| --- | --- |
| list projects | workspace scan |
| list tasks | `ortask.py list --todo --file FILE` |
| show task details | `ortask.py show ID --file FILE` |
| mark done | `ortask.py done ID --file FILE` |
| add note | planned note writer |
| add child task | `ortask.py add TITLE --parent ID --file FILE` |
| open editor | editor at or near task heading |

Every write must use the same surgical persistence rules as `ortask.py`:
touch only the selected heading, inserted note, or inserted child task.
Never rewrite the full Org file to save menu state.

## Toolkit Direction

The first version can be a minimal terminal menu using only the Python
standard library. Plain numbered choices are acceptable and may be better
than a full-screen TUI while the workflow is still settling.

Optional libraries can come later:

- `prompt_toolkit`: useful for searchable menus, history, and richer input.
- `questionary` or `InquirerPy`: useful if the tool becomes mostly menus and
  confirmations.
- `Textual`: defer unless the goal becomes a persistent full-screen project
  dashboard.

If optional dependencies are introduced, keep non-interactive `ortask.py`
commands stdlib-only.

## Safety Rules

- Ask before mutating tasks; selection alone is read-only.
- Keep task IDs visible whenever an ID exists.
- Warn and stop on duplicate IDs in the selected file.
- Preserve `--file` and `ORTASK_FILE` behavior when delegating to `ortask.py`.
- Do not auto-run repair before a session.
- Do not store persistent session state in the Org file.

## Testing Expectations

Tests should separate workflow logic from terminal I/O:

- workspace discovery finds `elweek/TODO-ElWeek.org` and `ortask/todo.org`
- project selection loads the expected Org file
- task menus preserve priority and file-order rules
- selecting a task does not write to disk
- write actions call the same parser/writer paths as CLI commands
- duplicate IDs produce a clear stop condition

Manual verification can start with temporary fixture directories that mimic
the `proj2026` symlink layout before trying real project task files.

## Open Decisions

- Should `projtui.py` live beside `ortask.py`, or should it become
  `ortask.py project` later?
  I don't anticipate add "ortask.py project" later, but one never knows.  I think I want ortask.py to eventually be spilt into the core library (lib/orgmod.py) and the cli (ortask.py or bin/ortask.py)
- How should the workspace scan choose among multiple `.org` files in one
  project directory?
  Naming/placement convention.  Priority order:
  1. TODO.org
  2. todo.org (case insensitive)
  3. one level deep of subdirectories, alphanumeric order
- Should there be a project metadata file, or is the symlink layout enough?
  Symlink layout is enough for now.  There may be a global file for all projects at some point down the road.
- Should task bodies support a structured "prompt" block, or should the TUI
  simply display existing body text?
  Display the existing body text.  I don't want to deviate at all from orgmode norms, though I want the org file to be simple as possible.  This tool is meant as a complementary tool for orgmode users who may not be Emacs power users (e.g. I'm not really an Emacs power user, I don't think).  The idea is that this is training wheels for someone that eventually just wants to switch over to using Emacs to manage their day-to-day.
- What is the smallest useful flow for the Electorama Weekly promotion work?
  Use `TODO-ElWeek.org` as the project file. Put the latest episode promotion
  checklist under `* Tasks` using normal ortask headings, and keep the existing
  destination template as reference. A minimal checklist should cover:
  1. identify the latest episode URL/title
  2. draft short promo copy
  3. post to `/r/electorama`
  4. post to X/Twitter
  5. post to Facebook
  6. record links or notes under the task body
  Then run `./projtui.py`, choose `elweek`, select one task, and mark it done
  only after the external promo step is actually complete.
