# Interactive Workflow for ortask.py

This document specifies interactive use of `ortask.py` and `projtui.py`.
The goal is a guided layer over Org task files, not a separate task database.
It also records lessons from the sibling `castabout` project, whose inline TUI
has a stronger workflow shape than the current minimal `projtui.py`.

## Goal

Interactive ortask tools should help the user stay focused on a current task
without hiding the underlying Org file. Org files remain the source of truth.
The TUI should display state first, ask only when needed, and make writes
small, explicit, and reviewable.

Primary entry points:

```sh
./ortask.py -i
./projtui.py
./projtui.py --registry ~/Projects
```

`ortask.py -i` opens the task menu for the local task file resolved by
`ortask.py`. `projtui.py` starts from the configured project registry.

## Current Implementation

`projtui.py` is currently a numbered-menu TUI with detail display, editor
launch, and `DONE` marking for ortask-compatible tasks with IDs. Local
`ortask.py -i` and project task views in `projtui.py` use the same
castabout-style task dashboard: they print the resolved task file, an
open/done/total summary, and a status table before prompting for a task number.
`projtui.py`'s top-level project list also uses the shared menu renderer. Rich
is used when available, with a plain text fallback.

For project navigation, `projtui.py` looks in `~/Projects` unless
`~/.config/ortask/ortask.ini` sets:

```ini
[projects]
registry = ~/tmpsorta/proj2026
```

On startup it prints the directory it is scanning, for example:

```text
Finding project in ~/tmpsorta/proj2026
```

Keep the plain numbered mode available as a fallback even if richer TUI
behavior is added later.

## Castabout Lessons

`castabout.py` is the best local prototype for a focused workflow TUI. Its
history and guidance point to these ortask principles:

- **Show before asking**: render the dashboard or task list before any prompt.
- **Inline over full-screen**: preserve scrollback and use focused prompts.
- **Editable defaults**: prefill inferred values in editable fields.
- **Esc means back**: nested prompts cancel without writing.
- **Visible URLs first**: show task body URLs before asking about completion.
- **Graceful helpers**: clipboard/browser helpers should fall back to printed
  instructions.
- **Review writes**: show proposed changes before non-trivial writes.

Castabout currently uses `prompt_toolkit` for editable prompts and fast `Esc`
cancellation, `rich` for tables/panels, and ordinary line-oriented Org
writeback. Ortask should borrow those techniques for richer interactive modes
while keeping non-interactive CLI commands stdlib-friendly.

## Shared Menu System

The ortask ecosystem should converge on a shared menuing layer used by
`ortask.py -i`, `projtui.py`, and workflow tools such as `castabout.py`.
Individual tools can provide domain-specific actions, but users should not have
to relearn basic navigation in each program.

Shared behavior should include:

- status-first dashboards that render before prompting
- a consistent row model: number, status, title, optional detail columns
- the same prompt vocabulary: number to select, `e` to edit/open, `b` back,
  `q` quit, `Esc` cancel/back when prompt_toolkit is active
- optional Rich rendering with a plain text fallback
- consistent task ordering and indentation
- arrow-key selection with a highlight bar for task/project rows
- reusable confirmation and proposed-change displays

This layer should not own business logic. It should render menu rows, collect
choices, manage cancellation, and expose hooks for actions. `castabout` can add
"copy draft" and "open destination"; `projtui.py` can add "open editor"; local
`ortask.py -i` can add task-editing actions. All of them should feel like the
same family of menus.

Implementation has started in `ortasklib.menu` with a small menu row dataclass,
shared dashboard/table renderer, and prompt helper that maps `Esc` to
`ContextCancelled` when prompt_toolkit is active. The next pieces to extract
are richer choice loops and highlight-bar selection. Keep the abstraction small:
rendering and choice collection belong in the shared layer; task-specific
actions stay in the calling tool.

## Highlight-Bar Selection

The current `ortasklib.menu` layer is useful as a transition point, but it is
also close to the line where we would start reinventing a prompt library. Rich
tables plus `prompt_toolkit` prompts are fine for static dashboards and numbered
choices. Once rows need up/down navigation, a highlighted current row, and
selection without typing numbers, the selector should be owned by an existing
interactive toolkit rather than by ad hoc terminal escape handling.

Recommended path:

1. Preserve the plain numbered menu as the non-TTY/fallback mode.
2. Add a shared `select_menu()` abstraction in `ortasklib.menu` that returns the
   selected row or a cancellation/action token.
3. Prototype the interactive implementation with `prompt_toolkit` directly
   because ortask already uses it for `Esc`, and because task rows may need
   custom keys, indentation, detail previews, and future Emacs-like behavior.
4. Reconsider InquirerPy if the needed interaction stays close to ordinary
   single-select menus; it may provide the highlight bar with less local code.
5. Defer Textual unless the UI becomes a persistent application with panes,
   live preview regions, or multiple screens.

In short: keep the current Rich/prompt_toolkit blend for now, but do not grow
`ortasklib.menu` into a private TUI framework. If the highlight-bar prototype
requires more than a small selector and a few key bindings, switch to a
maintained prompt/TUI layer before adding more features.

## Workspace Discovery

The project TUI treats each immediate registry subdirectory as a possible
project context when it contains or points to an Org task file. In `proj2026`,
examples include:

- `elweek/` with an ElectoramaWeekly task file
- `ortask/` with this repository's task file

Do not recursively scan arbitrary nested trees. Use the shared task-file
discovery convention from `docs/format.md`, and treat ambiguous files as a stop
condition rather than sorting alphabetically.

## Task Menu Workflow

After project or local-file selection, display open tasks as a menu:

```text
elweek tasks:
  1. [TODO] tw26W26 Promote June 24 ElectoramaWeekly episode
  2. [TODO] tw26W26.1 Prepare next episode
  e. open this Org file in editor
  b. back
  q. quit
```

The user chooses what to work on. The menu should support focus without hiding
judgment or forcing the next task in file order.

Default ordering should be useful:

1. open tasks before done tasks
2. higher Org priority first: `[#A]`, then `[#B]`, then `[#C]`
3. parent tasks before subtasks
4. file order as the final tie-breaker

## Focus Prompt

Selecting a task should show details immediately and then present actions:

```text
** TODO tw26W26.0.3 Post to reddit (/r/electorama)
https://www.reddit.com/r/electorama/submit

Actions:
  d. mark DONE
  e. open in editor
  b. back to task menu
```

Display descendant subtasks, capped at 20 lines with a truncation note. Plain
body URLs are important; workflow tools should display them before asking the
user whether work is complete. Opening the editor for a selected task should
jump to that task's line when the configured editor supports line arguments.

Workflow-specific tools such as castabout may add domain actions such as "copy
draft", "open destination", or "record result". Those actions should still use
the same task identity, URL extraction, and writeback primitives supplied by
`ortasklib`.

## Operations

Initial operations should map to existing or planned CLI behavior:

| TUI action | Command equivalent |
| --- | --- |
| list projects | `orgmgr.py list` |
| list tasks | `ortask.py list --todo --file FILE` |
| show details | `ortask.py show ID --file FILE` |
| mark done | `ortask.py done ID --file FILE` |
| add child task | `ortask.py add TITLE --parent ID --file FILE` |
| open editor | editor at or near task heading |

Every write must use the same surgical persistence rules as `ortask.py`: touch
only the selected heading, inserted note, or inserted child task. Never rewrite
the full Org file to save menu state.

## Toolkit Direction

Keep the stdlib numbered-menu implementation as the baseline. For richer
interactive behavior, prefer the castabout stack, with a stricter boundary
around what ortask should implement itself:

- `prompt_toolkit` for editable prefilled fields, history, key bindings, and
  fast context cancellation. It is also the likely first prototype for
  highlight-bar row selection.
- `rich` for status tables, panels, progress summaries, and proposed changes.
- `argparse` remains fine for `ortask.py`; workflow-specific tools may use
  Click if it suits their command surface.
- InquirerPy/questionary are worth evaluating if ortask only needs conventional
  select/confirm prompts and the custom prompt_toolkit selector starts growing.

Defer `Textual` or a full-screen event loop until the workflow clearly needs
persistent layout or more menu machinery than a small selector. Castabout's
inline loop is already enough for status-first guidance, manual task selection,
and nested prompts; the next test is whether it remains enough with arrow-key
selection.

## Safety Rules

- Ask before mutating tasks; selection alone is read-only.
- Keep task IDs visible whenever an ID exists.
- Warn and stop on duplicate IDs in the selected file.
- Preserve `--file` and `ORTASK_FILE` behavior.
- Do not auto-run repair before a session.
- Do not store persistent session state in the Org file.
- If a prompt is cancelled with `Esc`, do not write.
- Preserve unrelated Org content byte-for-byte where practical.

## Testing Expectations

Tests should separate workflow logic from terminal I/O:

- registry discovery finds expected project task files
- local `ortask.py -i` loads the resolved task file
- task menus preserve priority and file-order rules
- selecting a task does not write to disk
- write actions call the same parser/writer paths as CLI commands
- duplicate IDs produce a clear stop condition
- prompt cancellation returns to the previous context without writing
- proposed writes touch only the selected heading or body insertion

Manual verification can start with temporary fixture directories that mimic the
`proj2026` symlink layout before trying real project task files.

## Castabout Convergence

`castabout.py` currently has bespoke Org parsing, task-file discovery, venue
selection, URL extraction, and writeback. Bring it deeper into the ortask
ecosystem in layers:

1. Share task-file discovery rules with `ortasklib.core`.
2. Use ortask parsing for `* Tasks` and stable IDs where practical.
3. Move reusable body URL extraction and surgical writeback helpers into
   `ortasklib`.
4. Keep castabout-specific episode inference, draft generation, clipboard,
   browser, and ElectoramaWeekly workflow code in castabout.
5. Reuse castabout's prompt_toolkit/Rich interaction techniques in ortask's
   richer interactive mode.

The convergence target is not to turn castabout into an ortask subcommand. It
is to make both tools trust the same Org-task substrate while allowing
castabout to remain a focused workflow assistant.
