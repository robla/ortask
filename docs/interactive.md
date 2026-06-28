# Interactive Workflow for the ortask suite

This document specifies interactive use of `ortask.py` and `orgmgr.py`.
The goal is a guided layer over Org task files, not a separate task database.
It also records lessons from the sibling `castabout` project, whose inline TUI
has a stronger workflow shape than the current project browser.

## Goal

Interactive ortask tools should help the user stay focused on a current task
without hiding the underlying Org file. Org files remain the source of truth.
The TUI should display state first, ask only when needed, and make writes
small, explicit, and reviewable.

Primary entry points:

```sh
./ortask.py -i
./orgmgr.py -i
./orgmgr.py --registry ~/Projects -i
```

`ortask.py -i` opens the task menu for the local task file resolved by
`ortask.py`. `orgmgr.py -i` starts from the configured project registry. The
short aliases are expected to be `ort -i` and `orgm -i`.

## Current Implementation

`orgmgr.py -i` is the public registry-scoped project browser. It currently
delegates to `projtui.py`, which remains the implementation shim with detail
display, editor launch, and state changes for ortask-compatible tasks with IDs.
Local `ortask.py -i` and project task views use the same castabout-style task
dashboard: an open/done/total summary over a status table of the resolved task
file.

The task selector has two modes, chosen automatically by
`menu.interactive_select_available()`:

- **Highlight-bar mode** (interactive TTY with `prompt_toolkit`): an inline
  arrow-key selector via `menu.select_menu()`. Up/Down or `j`/`k` move the
  highlight (wrapping); `Enter` opens the focus view; Shift-Left/Right cycles
  the highlighted task through the `TODO`/`DONE` ring; `C-t` cycles the
  visibility filter (`all -> TODO -> DONE`); `e` opens the editor at the
  highlighted task's line; `b`/`Esc` go back; `q` closes the current task-file
  context. In `orgmgr.py -i`, that returns to the project menu; in local
  `ortask.py -i`, it exits. The app renders inline (not full screen), so it
  erases itself on exit and leaves scrollback intact.
- **Numbered mode** (non-TTY, piped, or `prompt_toolkit` absent): the original
  numbered dashboard + prompt, preserved as the scriptable fallback with the
  same `C-t` visibility cycle.

The top-level project list uses the same shared selection model: highlight-bar
mode on an interactive TTY, and the Rich/plain numbered dashboard as the
non-interactive fallback.

### Editing buffer (auto-save and save-on-exit)

Interactive edits do not touch the real Org file immediately. Each file's task
menu runs against a `projtui.OrgBuffer`, modeled on Emacs (t0006):

- Edits (Shift-Left/Right in the selector, a focus-view `mark DONE`) update an
  in-memory buffer and mirror it to an **auto-save sibling** named `#todo.org#`
  (Emacs convention) for crash recovery. The real file is untouched.
- On leaving the file's editing context (`b`/`q`/Esc), if the buffer is dirty it
  prompts `todo.org has been modified; save todo.org? [Y/n]`. The answer is
  three-way:
  - `Enter`/`y`/`yes` (the default) writes the real file atomically and removes
    the auto-save. The file is never reformatted — only the same surgical line
    edits the CLI would make are committed.
  - `n`/`no` discards the pending edits and removes the auto-save.
  - `Esc` cancels the exit: it returns to the still-running menu with the edits
    intact (buffer dirty, auto-save kept), so it is a safe "wait, not yet" rather
    than a save-or-lose choice.
- On entering a context where a `#todo.org#` already exists (e.g. after a crash),
  it offers a three-way choice: `y` recovers those unsaved changes into the
  buffer, `n` discards the recovery file, and `Enter`/`Esc` keep it untouched to
  decide later. Only an explicit `n`/`no` deletes leftover recovery data.
- Opening the external editor (`e`) first flushes any pending buffer to the real
  file, then re-reads it afterward, so the editor and the buffer never disagree.

Only the interactive TUI buffers. The one-shot CLI (`ortask.py done`, `add`, …)
still writes immediately, since it has no editing session to defer within.

For project navigation, `orgmgr.py -i` looks in `~/Projects` unless
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
`ortask.py -i`, `orgmgr.py -i`, and workflow tools such as `castabout.py`.
Individual tools can provide domain-specific actions, but users should not have
to relearn basic navigation in each program.

Shared behavior should include:

- status-first dashboards that render before prompting
- a consistent row model: number, status, title, optional detail columns
- the same prompt vocabulary: number/Enter to select, arrows to move, `e` to
  edit/open, `b` or `Esc` back, `q` close the current context
- Org/Emacs-compatible task-state cycling with Shift-Right and Shift-Left as
  the primary keys
- optional Rich rendering with a plain text fallback
- consistent task ordering and indentation
- arrow-key selection with a highlight bar for task/project rows
- reusable confirmation and proposed-change displays

This layer should not own business logic. It should render menu rows, collect
choices, manage cancellation, and expose hooks for actions. `castabout` can add
"copy draft" and "open destination"; the project browser can add "open editor";
local `ortask.py -i` can add task-editing actions. All of them should feel like
the same family of menus.

Implementation has started in `ortasklib.menu` with small task/project row
dataclasses, shared dashboard/table renderers, prompt helpers that map `Esc` to
`ContextCancelled`, and inline highlight-bar selectors for task and project
rows. Keep the abstraction small:
rendering and choice collection belong in the shared layer; task-specific
actions stay in the calling tool.

## Highlight-Bar Selection

The current `ortasklib.menu` layer is useful as a transition point, but it is
also close to the line where we would start reinventing a prompt library. Rich
tables plus `prompt_toolkit` prompts are fine for static dashboards and numbered
choices. Once rows need up/down navigation, a highlighted current row, and
selection without typing numbers, the selector should be owned by an existing
interactive toolkit rather than by ad hoc terminal escape handling.

### Evaluation & Decision

After evaluating the options in
[python-tui-toolkit-options.md](../tui2026/python-tui-toolkit-options.md), the
conclusion is that the arrow-key highlight-bar selector — the interactive
centerpiece this design is aiming at — should be built **directly on
`prompt_toolkit`** rather than on InquirerPy or Textual.

Two points frame the choice:

- **The numbered menu stays as the fallback, not the ceiling.** Numbered
  selection already supports in-list actions (type a number to focus a row, type
  `e`/`d` to act), so it remains a fully usable mode for non-TTY and scripted
  contexts. The highlight bar is the richer interactive layer built on that same
  row model: keep the plain menu working, but treat the highlight bar as the
  primary interactive target rather than a someday-maybe.
- **A wrapper would not save the dependency, only the code.** InquirerPy and
  questionary are themselves built on `prompt_toolkit`, which ortask already
  carries (optionally) for `Esc` cancellation. So the choice is not "add a heavy
  dependency vs. stay light"; it is "own a small selection widget vs. accept a
  wrapper's interaction model." That reframes the YAGNI argument: the cost being
  weighed is custom code, not a new package.

Given that, `prompt_toolkit`-direct wins on the one requirement that actually
distinguishes the options:

- **Direct in-list actions.** ortask wants hotkeys that act on the *currently
  highlighted* row — `e` to open the editor, `d` to toggle DONE — without first
  committing the selection and tearing down the prompt. Select-only wrappers
  model a prompt as "return one value"; bending them into "return a value *or* an
  action token, keyed off the live cursor index" fights the framework.
  `prompt_toolkit` `KeyBindings` express this naturally.
- **Inline flow.** Unlike Textual, which owns a full-screen application loop,
  `prompt_toolkit` (with `full_screen=False`) keeps the interaction inline and
  preserves scrollback, matching the "inline over full-screen" principle.

### Recommended Path

Steps 1–3 are implemented for the task selector; step 4 remains the boundary to
watch.

1. **Done.** The plain numbered menu remains the non-TTY / fallback / scriptable
   mode (`projtui._numbered_task_menu`), so every interactive selection still has
   a non-interactive equivalent and automation never blocks on a picker.
2. **Done.** `ortasklib.menu.select_menu()` is a narrow abstraction over rows
   that returns exactly one of: a selected row, an action token (`edit`,
   `toggle`, …) bound to a row (`MenuResult`), or `back`/`quit`. It renders and
   collects choices only; it owns no task logic.
3. **Done.** The selector is a non-full-screen `prompt_toolkit` application with
   keybindings for navigation (arrow keys / `j`/`k`), the `TODO`/`DONE` toggle
   ring (Shift-Left/Right), task-state filtering (`C-t`), editor launching (`e`),
   and cancellation (`Esc` / `b`). The ring itself is `tasks.next_state()`, a
   pure helper.
4. Treat Textual as the *exit ramp*, not a competitor. The tripwire is concrete:
   the moment the selector wants a live detail-preview pane that re-renders as
   the highlight bar moves, multiple focusable regions, scrolling columns, or
   mouse support, stop hand-rolling `prompt_toolkit` layouts and move that screen
   to Textual. Below that line, a one-column selector in `prompt_toolkit` is the
   right amount of machinery.

In short: keep the Rich (output) + `prompt_toolkit` (input) blend, keep the
numbered menu as the baseline, and do not let `ortasklib.menu` grow into a
private TUI framework. If the selector ever needs more than one column and a
handful of keybindings, that is the signal to adopt Textual for that screen — not
to keep extending the hand-rolled loop.

## Keybinding Direction

Use Emacs Org mode as the north star when it gives ortask a defensible default,
but keep the on-screen command strip plain enough that a non-Emacs user can
learn it in one pass. This should feel more like Pine than raw Emacs: visible
prompts, a small vocabulary, and editor-compatible shortcuts where they are
worth teaching.

Recommended task-list bindings:

- Up/Down move the highlight. `j`/`k` are acceptable vi-style aliases because
  they are common, low-risk, and do not conflict with Org task semantics.
- Enter opens the highlighted task's detail/focus view.
- Shift-Right cycles the highlighted task forward through the TODO state ring;
  Shift-Left cycles backward. These should be documented as the primary state
  keys because they align with Org mode's `org-shiftright` / `org-shiftleft`
  behavior closely enough to transfer muscle memory.
- `e` opens the current Org file or selected task in the editor.
- `C-t` cycles the task visibility filter: `all -> TODO -> DONE`. This is not
  an exact Emacs binding, but it keeps `C-c` and `C-x` open for future
  multi-key command families while reserving `/` for search.
- `/` should be reserved for search in the current list or backing Org file.
- `b` and `Esc` go back one context without writing.
- `q` closes the current context: local `ortask.py -i` exits, while project mode
  returns from a project task view to the project list.

Avoid making `t` a row-selector state toggle. It is not very mnemonic once the
command grows beyond "toggle", and it competes with future meanings such as
"TODO-only filter", "tag", or "title". Reserve plain `d` for explicit "mark
DONE" actions in focus prompts; in the row selector, prefer Shift-Arrow for the
ring so the same model supports both forward and backward cycling.

Future TODO-state guidance:

- The current ring is `TODO -> DONE -> TODO`. Org's full ring commonly includes
  "no keyword" as another state. Do not add the no-keyword state until the
  parser and menu can keep an ID-bearing heading visible after its TODO keyword
  is removed; otherwise cycling would make the selected row disappear.
- When no-keyword support lands, the ring should be explicit in the UI, for
  example `TODO -> DONE -> none`, and Shift-Left should reverse that order.
- Keep filtering on `C-t` unless there is a strong reason to move deeper into
  Emacs-style multi-key sequences such as `C-c t` or `C-c / t`. This keeps state
  changes separate from visibility changes and avoids overloading plain `t`.

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
  q. close this task view
```

The user chooses what to work on. The menu should support focus without hiding
judgment or forcing a computed priority order.

Default display order is the order of headings in the Org file. This preserves
the visible parent/child hierarchy, keeps authored weekly workflows readable,
and matches what an Emacs Org user expects after arranging a tree by hand.
`TODO` and `DONE` rows are visible by default. `C-t` cycles visibility through
`all -> TODO -> DONE`, but filtering must not otherwise reorder the remaining
rows. Org priorities such as `[#A]` remain visible metadata; they do not move
rows.

The highlight-bar selector also re-anchors the highlight on the same task ID
across reloads, so a toggled task stays selected even if the list membership
changes. Because the menu order is file order, toggling TODO/DONE never moves a
row out from under the cursor.

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
- InquirerPy/questionary become attractive only if the in-list hotkey
  requirement is dropped — i.e. if a plain single/multi-select is all ortask
  needs. They are wrappers over the same `prompt_toolkit`, so they trade control
  for convenience, not weight. If instead the selector needs *more* (a live
  preview pane, multiple regions), that is a Textual signal, not a wrapper one.

Defer `Textual` or a full-screen event loop until the workflow clearly needs
persistent layout or more menu machinery than a small selector. Castabout's
inline loop is already enough for status-first guidance, manual task selection,
and nested prompts; the next test is whether it remains enough with arrow-key
selection.

## Safety Rules

- Selection alone is read-only; only explicit edit keys change anything.
- Buffer edits in memory and confirm before writing the real file on exit; never
  write the user's Org file as a silent side effect of navigating.
- Mirror pending edits to the `#name#` auto-save file so a crash is recoverable.
- Keep task IDs visible whenever an ID exists.
- Warn and stop on duplicate IDs in the selected file.
- Preserve `--file` and `ORTASK_FILE` behavior.
- Do not auto-run repair before a session.
- Do not store persistent session state in the real Org file.
- If a prompt is cancelled with `Esc`, do not write.
- Preserve unrelated Org content byte-for-byte where practical.

## Testing Expectations

Tests should separate workflow logic from terminal I/O:

- registry discovery finds expected project task files
- local `ortask.py -i` loads the resolved task file
- task menus preserve Org file order and hierarchy
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
2. Use ortask parsing for `* Tasks` when present, whole-file task headings when
   absent, and stable IDs where practical.
3. Move reusable body URL extraction and surgical writeback helpers into
   `ortasklib`.
4. Keep castabout-specific episode inference, draft generation, clipboard,
   browser, and ElectoramaWeekly workflow code in castabout.
5. Reuse castabout's prompt_toolkit/Rich interaction techniques in ortask's
   richer interactive mode.

The convergence target is not to turn castabout into an ortask subcommand. It
is to make both tools trust the same Org-task substrate while allowing
castabout to remain a focused workflow assistant.
