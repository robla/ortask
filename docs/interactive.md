# Interactive ortask.py

This document specifies a future interactive mode for `ortask.py`. It is
both a design note and the seed of user documentation.

## Goal

Interactive mode should step through a `todo.org` file and help the user
decide what to do next. It is not meant to become a full task database or
replace manual Org editing. The file remains the source of truth, and the
interactive layer is a guided workflow over the same `* Tasks` subtree used
by the existing commands.

Primary command:

```sh
./ortask.py interactive
./ortask.py work
```

`work` may become the friendly alias if the command feels more like a daily
workflow than a generic TUI.

## User Experience

The session presents one actionable task at a time:

```text
[TODO] t0004 Test repair on task lists without IDs

Actions: done, skip, show, add-child, note, open-editor, quit
```

Core actions:

- `done`: mark the current task `DONE` and advance.
- `skip`: leave the task unchanged and advance for this session only.
- `show`: display body text, properties, and subtasks.
- `add-child`: create a subtask under the current task.
- `note`: append a short note to the current task body.
- `open-editor`: open the file at or near the current task when possible.
- `quit`: exit without changing the current task.

Interactive mode must always make file writes through the same conservative
writer rules as other commands: touch only the current task heading or the
specific inserted note/subtask, and preserve surrounding Org text.

## Authoring todo.org for Guided Work

The simplest authoring model is ordered TODO headings under `* Tasks`:

```org
* Tasks
** TODO [#A] t0001 Fix broken parser edge case
** TODO t0002 Write docs
*** TODO t0002.1 Draft interactive mode spec
** TODO [#C] t0003 Nice-to-have cleanup
```

Interactive mode should use this priority order by default:

1. open tasks before done tasks
2. higher Org priority first: `[#A]`, then `[#B]`, then `[#C]`
3. parent tasks before subtasks unless a subtask is explicitly selected
4. file order as the final tie-breaker

This keeps the file readable and lets the user control workflow mostly by
reordering headings in an editor.

## Selection Modes

Initial implementation should support:

- `--all`: include all open tasks.
- `--root-only`: step only through direct children of `* Tasks`.
- `--tag TAG`: include tasks with an Org tag.
- `--id ID`: start at a specific task.
- `--limit N`: stop after N presented tasks.

Possible later modes:

- `--priority A|B|C`
- `--children-of ID`
- `--resume SESSION`
- `--random`

## Session State

The first version should avoid persistent session state. `skip` only means
"not now" inside the current process. If a user wants a task to disappear
from future sessions, they should mark it `DONE`, lower its priority, move it
later in the file, or add a future explicit status once the parser supports
that.

Persistent state can be considered later, but it should not be stored inside
the Org file unless the format is documented and manually understandable.

## Toolkit Direction

The current recommendation is to use `prompt_toolkit` for the interactive
loop if the project accepts a dependency. This mode wants custom keybindings,
single-key actions, searchable choices, multiline note input, and a
REPL-like flow. Those are good reasons to use `prompt_toolkit` directly.

Alternatives:

- `InquirerPy` or `questionary`: better if the first version is only menus
  and confirmations.
- `Rich`: useful for formatted output, but not enough by itself for input.
- `Textual`: too much for the first version unless the goal becomes a
  full-screen task application.
- `curses`: no obvious advantage here over `prompt_toolkit`.

Because `ortask.py` is currently stdlib-only, interactive dependencies should
be optional. A packaging-friendly shape would be:

```sh
pip install "ortask[interactive]"
```

If dependencies are missing, `ortask.py interactive` should fail with a clear
message explaining what to install. Non-interactive commands must continue to
work without optional packages.

## Non-Interactive Compatibility

Every interactive operation should map to an existing or planned command:

| Interactive action | Command equivalent |
| --- | --- |
| list candidate tasks | `ortask.py list --todo` |
| show details | `ortask.py show ID` |
| mark done | `ortask.py done ID` |
| reopen | `ortask.py open ID` |
| add child | `ortask.py add TITLE --parent ID` |
| repair before session | `ortask.py repair --dry-run` |

This keeps the interactive layer thin. The parser, query logic, and writer
should stay shared with the normal CLI.

## Safety Rules

- Do not run repair automatically before an interactive session.
- Warn and exit if duplicate IDs are detected.
- Confirm before editing a file with no `* Tasks` section.
- Never hide the task ID from the user.
- Do not rewrite the whole file to save session progress.
- Keep `--file` and `ORTASK_FILE` behavior identical to the rest of the CLI.

## Testing Expectations

Tests should cover the workflow logic without requiring a real terminal:

- candidate ordering by state, priority, hierarchy, and file order
- `skip` advancing without changing the file
- `done` rewriting only the selected heading
- `add-child` inserting under the selected parent
- missing optional dependency error text

Terminal integration tests can come later. The first implementation should
keep the session controller separate from the prompt toolkit adapter so most
behavior can be tested with fake input and temporary Org files.

## Open Decisions

- Should the public command be `interactive`, `work`, or both?
- Should `skip` ever persist across sessions?
- Should body notes use plain text, Org list items, or timestamped logbook
  entries?
- Should the first version include tag filtering, or wait until tag parsing
  is stronger?
- Should optional dependencies be declared through packaging metadata, a
  requirements file, or documented manual installation?
