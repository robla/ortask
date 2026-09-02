# Interactive Workflow for the ortask suite

This document specifies interactive use of `ortask.py` and `projmgr.py`.
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
./projmgr.py -i
./projmgr.py --registry ~/Projects -i
```

`ortask.py -i` opens the task menu for the local task file resolved by
`ortask.py`. `projmgr.py -i` starts from the configured project registry. The
short aliases are `orti` and `ptui`.

## Current Implementation

`projmgr.py -i` (`ptui`) is the public registry-scoped project navigator. It
owns the project list; the task list, detail display, editor launch, and state
changes live in `ortasklib/taskui.py`, shared with `ortask.py -i`. Local
`ortask.py -i` and project task views use the same castabout-style task
dashboard: an open/done/total summary over a status table of the resolved task
file.

The project navigator reads optional priority and description metadata from
the registry's `projects.org`. It starts in priority-then-name order and `s`
cycles Priority, Alphabetical, and Modified while keeping the highlight anchored
to the same project. `C-t` cycles the project filter between every project and
only those with open tasks; a project whose task file cannot be read stays
visible either way. `v` opens a view options screen over the same axes plus sort
direction. All of it runs off the shared view state in
`ortasklib/viewstate.py`, and the active view is always named in the footer. `m`
opens the metadata workspace, including explicit `TASK_FILE` mirror diagnostics
and refresh. Shift-Up/Down changes the selected project's priority in a
session-wide `projects.org` buffer; `C-/`, `C-r`, and `C-s` undo, redo, and save
those edits, with visible dirty state and
save/discard handling on exit. The
project session also polls the index: clean buffers adopt external writes and
dirty buffers merge disjoint project-section changes in memory. A same-section
overlap remains a persistent named conflict with reload, retry, and continue
controls; no reconciliation writes the real index before `C-s`.

The task selector has two modes, chosen automatically by
`menu.interactive_select_available()`:

- **Highlight-bar mode** (interactive TTY with `prompt_toolkit`): task
  workflows run in one bounded `menu.InlineMenuSession`. Up/Down or `j`/`k`
  move the highlight (wrapping). The list starts as a top-level overview; `Tab`
  expands or collapses a task, Shift-Tab expands all or returns to the
  overview, and Left/Right provide directional tree navigation. `Enter` opens
  the focus view, and so does Right once the highlighted task has nothing left
  to expand; Shift-Left/Right cycles the highlighted task through the
  `TODO`/`DONE` ring; Shift-Up/Down raises or lowers its priority; `p` opens an
  explicit priority picker; `C-/` undoes one logical task edit; `C-r` redoes
  it; `C-s` saves the Org file and clears that history; `C-t` cycles the
  visibility filter (`all -> TODO -> DONE+`, whose third position matches every
  terminal state); `v` opens the view options screen; `e` opens the editor at
  the highlighted task's line; `C-g` opens contextual command help; and `Esc`,
  `b`, `q`, or Left with nothing left to collapse or ascend to goes back
  exactly one level. In `projmgr.py -i`, leaving a task list returns to the
  project menu; in local `ortask.py -i`, that task list is the top level, so
  leaving it exits. The application renders inline (not full screen), defaults
  to 20 rows, and repaints that region as contexts change. On a controlled exit
  it retains one final bounded frame with a factual footer such as `No changes
  to tasks.org`, `Saved changes to tasks.org`, or `Discarded changes to
  tasks.org`; it does not append each visited view. Long lists scroll within
  the row body while the title, summary, and key hint remain fixed; the
  selector keeps one context row above and below the highlight when space
  permits. Selecting a task opens the task workspace described below. Its
  fields start in navigation mode; Enter explicitly enables text or choice
  editing. The view options screen is that same workspace with one field per
  axis.
- **Numbered mode** (non-TTY, piped, or `prompt_toolkit` absent): the original
  numbered dashboard + prompt, preserved as the scriptable fallback with the
  same `C-t` visibility cycle. It lists the complete filtered hierarchy because
  it has no persistent cursor or per-task expansion state. The `v` screen is not
  offered here: numbered mode stays a typed-letter prompt, the way the metadata
  workspace is also highlight-bar only.

The top-level `projmgr.py -i` project list is the root view of the same bounded
application. `Enter` opens the highlighted project, and so does Right: the list
is flat, so Right has no subtree to reveal there and does what it does on a leaf
task. Opening a project pushes its recovery or task view; leaving that task
context refreshes the project list and restores the same project by name.

`cdproj` renders its project list through the same code. Only the title, the
row label, and the third column differ, so the two are told apart on sight
rather than by their key bindings; `docs/projmgr.md` has the table. The row
label doubles as the row's style key, so both belong to
`menu.PROJECT_ROW_LABELS` — a label outside that set renders as a task-neutral
row.

### Do not obscure the location of stuff

A project menu must never make the reader guess where something is. Both
project menus therefore spend their summary line on the highlighted project's
directory and task file, updating as the selection moves:

```text
Project navigator                                  Registry: ~/tmpsorta/proj2026
~/src/elusync  ·  todo.org
```

Two consequences worth stating, because both are easy to get backwards:

- **The registry rides the title line, not the summary line.** It is fixed for
  the whole session, so pairing it with the fixed title keeps it still. Sharing
  a line with the location would make it appear and disappear as the cursor
  passed projects with longer paths.
- **Right-aligned context is the expendable half.** The `title_right` field on
  menu and workspace views is dropped whole when the terminal cannot hold it
  beside the title, because truncating the left to fit context would obscure
  exactly what must stay legible. Nothing load-bearing goes there.

Task views use the same convention. Every `orti` task menu and workspace pins
`File: ~/path/to/tasks.org` to the right of its title, using the canonical path
and shortening `$HOME` to `~`. This remains stable while moving through task,
priority, recovery, and save contexts; narrow terminals may drop it as above.

The numbered dashboards have no highlight to describe, so a location that the
picker delegates to the summary line has to appear in the row instead — which
is why `_ProjectListing` carries a separate `dashboard_detail`.
In highlight-bar mode, `C-g` replaces the rows with a modal help view; `C-g`,
`Esc`, `b`, `q`, or `Enter` closes help and restores the same selection. Custom
actions use `menu.MenuAction` metadata so adding a binding also adds its key and
description to this help view.

Nonfinal notices temporarily replace the footer hint. Warnings such as `task
has no visible subtasks`, boundary notices, and undo/redo feedback clear after
about two seconds, restoring the current view's command hint. A newer message
or view transition cancels the old timeout. Final save, discard, and recovery
outcomes remain persistent.

Treat the interactive UI as a stack. Outside an editing text field, `Esc`, `b`,
and `q` are synonyms for popping its top layer: help returns to the underlying
menu, a subtask returns to its parent task, a task focus view returns to its
task list, and a project task list returns to the project list. While a field is
being edited, `b` and `q` insert text and Esc first returns to field navigation;
a second Back command leaves the workspace. A dirty task workspace warns before
leaving. Popping the root local-task or project-list layer exits the program;
no key should skip intermediate layers.

Unselected rows show task state through the label color (TODO yellow, DONE
green). The highlighted row instead becomes a single continuous bar in the
task's state color — gold for TODO, green for DONE (light gray for any other
state, cyan for a highlighted project) — with black text, so the whole line,
the label included, stays legible. The selected row marker is `▶`. Expandable
tasks carry a disclosure triangle: `▸` when collapsed and `▾` when expanded;
the triangle is part of the highlight bar when that task is selected.

### Editing buffer (auto-save and save-on-exit)

Each file's task menu runs against a `taskui.OrgBuffer` compatibility adapter
over the format-neutral `textbuffer.TextFileBuffer`, modeled on Emacs (t0006).
Task-list edits are buffered, while an explicit task-workspace save writes the
complete current buffer:

- Each task-list state or priority action is one labeled `OrgBuffer`
  transaction. It updates the in-memory buffer and mirrors it to an **auto-save
  sibling** named `#todo.org#` (Emacs convention) for crash recovery. The real
  file remains untouched until Save.
- A task-list header shows `FILE MODIFIED: N edits` while buffered transactions
  differ from disk. `C-/` (the same terminal event as `C-_`) undoes one
  transaction and `C-r` redoes it. Undo and redo refresh the auto-save; undoing
  back to disk removes it. A clean buffer with redo history says `FILE CLEAN ·
  Redo available`.
- `C-s` in the task list atomically saves the complete buffer and resets both
  transaction stacks. Save, Discard, reload after an external editor, and a
  successful task-workspace save are all history boundaries.
- Every save first rereads the real file and requires an exact match with the
  buffer's saved baseline. An external change refuses the save without losing
  the disk version, dirty buffer, undo history, or auto-save.
- `Ctrl-S` in a task workspace validates title, body, state, and priority,
  applies them to the same buffer, atomically saves the entire Org file, and
  removes the auto-save.
  Thus it also commits state or priority edits made before entering the task.
- On leaving the file's editing context (`b`/`q`/Esc), a dirty highlight-bar
  session pushes a bounded Save/Discard/Continue Editing view. Save is selected
  by default; use Up/Down and `Enter` to choose. Save writes the real file
  atomically and removes the auto-save. Discard reverts the buffered edits and
  removes the auto-save. `Esc`/`b`/`q` or Continue Editing returns to the task
  list with the dirty buffer and auto-save intact. The numbered fallback keeps
  its `[Y/n]` prompt and uses `Esc` to continue editing.
- Saving never reformats the file; it commits only the same surgical line edits
  the CLI would make.
- On entering a highlight-bar context with distinct `#todo.org#` recovery data,
  the same bounded session starts with Keep for Later, Recover, and Discard
  choices. Keep is selected by default, and `Esc`/`b`/`q` also keeps the
  recovery file before opening the saved task list. Only selecting Discard
  deletes recovery data. The numbered fallback retains the equivalent
  `y`/`n`/`Enter` prompt.
- Opening the external editor (`e`) first flushes any pending buffer to the real
  file, then re-reads it afterward, so the editor and the buffer never disagree.

Only the interactive TUI buffers. The one-shot CLI (`ortask.py done`, `add`, …)
still writes immediately, since it has no editing session to defer within.

For project navigation, `projmgr.py -i` looks in `~/Projects` unless
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
`ortask.py -i`, `projmgr.py -i`, and workflow tools such as `castabout.py`.
Individual tools can provide domain-specific actions, but users should not have
to relearn basic navigation in each program.

Shared behavior should include:

- status-first dashboards that render before prompting
- a consistent row model: number, status, title, optional detail columns
- the same prompt vocabulary: number/Enter to select, arrows to move, `e` to
  edit/open, and `Esc`/`b`/`q` to pop one context
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

`ortasklib.menu` now contains small task/project row dataclasses, shared
dashboard/table renderers, prompt helpers that map `Esc` to
`ContextCancelled`, and the bounded `MenuView`/`InlineMenuSession` stack. Keep the abstraction small: rendering,
choice collection, context transitions, and terminal lifecycle belong in the
shared layer; task-specific actions stay in the calling tool.

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
   mode (`taskui._numbered_task_menu`), so every interactive selection still has
   a non-interactive equivalent and automation never blocks on a picker.
2. **Done.** `ortasklib.menu.MenuView` is a narrow abstraction over rows that
   returns exactly one of: a selected row, an action token (`edit`, `toggle`,
   …) bound to a row (`MenuResult`), or `back`. It renders and collects choices
   only; it owns no task logic. It replaced a one-shot `select_menu()` with the
   same contract, deleted in `t0011`.
3. **Done.** The selector is a non-full-screen `prompt_toolkit` application with
   keybindings for navigation (arrow keys / `j`/`k`), the `TODO`/`DONE` toggle
   ring (Shift-Left/Right), priority changes (Shift-Up/Down and `p`), task-state
   filtering (`C-t`), editor launching (`e`), contextual help (`C-g`), and
   cancellation (`Esc` / `b` / `q`). The state and priority transitions live in
   pure `ortasklib.tasks` helpers.
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
- `Tab` toggles the highlighted task's direct children, following Org's local
  visibility-cycle convention. Deeper parents remain independently collapsed.
- Shift-Tab toggles between the top-level overview and a fully expanded task
  tree, following Org's global visibility-cycle convention in a task-only view.
- Right expands a collapsed task. Pressing it again — or pressing it on a task
  with no visible children — opens that task, because Down already reaches the
  first child and the second Right is better spent on the one motion the arrows
  otherwise cannot make. Left collapses an expanded task, then moves to its
  parent when already collapsed, then leaves the list once there is no parent
  left to reach — the project browser under `projmgr.py -i`, and the exit
  gateway, save prompt included, in a standalone `ortask.py -i`. These
  directional aliases follow conventional tree controls.
- Enter opens the highlighted task's detail/focus view; Right does the same
  once there is nothing left to expand.
- Shift-Right cycles the highlighted task forward through the TODO state ring;
  Shift-Left cycles backward. These should be documented as the primary state
  keys because they align with Org mode's `org-shiftright` / `org-shiftleft`
  behavior closely enough to transfer muscle memory.
- Shift-Up raises priority through `none -> C -> B -> A`; Shift-Down lowers it
  through the reverse sequence. The scale stops rather than wrapping at both
  ends. In file order — the default — changing a priority never reorders the
  task list. In Priority order it deliberately moves the task among its own
  siblings, and the highlight follows the task rather than the row it was in.
- `p` opens an explicit `A`/`B`/`C`/`none` picker. This is the discoverable path
  for users who do not want to memorize directional shortcuts.
- `C-/` (`C-_` on the wire) undoes one buffered task-list transaction. `C-r`
  redoes the most recently undone transaction; the redo stack is cleared by a
  new mutation.
- `C-s` writes the complete Org buffer to disk and starts a new history.
- `e` opens the current Org file or selected task in the editor.
- `C-t` cycles the task visibility filter: `all -> TODO -> DONE+`. This is not
  an exact Emacs binding, but it keeps `C-c` open for future multi-key command
  families while reserving `/` for search.
- `C-x` requests application exit from any active `orti` or `ptui` context.
  It exits immediately when no changes need resolution; otherwise it asks in
  the footer as specified below.
- `C-g` opens contextual help. Although Emacs normally uses `C-g` to quit the
  current command, ortask uses it as the always-available command reference;
  `C-g`, `Esc`, `b`, `q`, or `Enter` returns to the unchanged menu selection.
- `/` should be reserved for search in the current list or backing Org file.
- `Esc`, `b`, and `q` all pop exactly one context. At the root they request
  application exit through the same dirty-state check as `C-x`.

### Global Exit (`C-x`)

Tracked as `t0046`. This is the application-level counterpart to Back:
`Esc`/`b`/`q` pop one view, while `C-x` requests termination from any active
prompt_toolkit context. The binding applies in menus, Help, view options,
conflict views, workspace navigation, and active text or choice editing. It
does not apply while control has been suspended to an external editor. The
numbered fallback retains its line-oriented `q` behavior. `t0046.1` implements
the opt-in session gateway and footer prompt.
`t0046.2` enables it in standalone `orti` with task-controller concerns;
`t0046.3` enables it in `ptui` with ordered project-index and task-file
concerns. `t0046.4` adds contextual Help, width-aware compact hints, and
lifecycle coverage across nested, editing, conflict, and real-terminal paths.

An exit is safe when there is no unapplied workspace draft and no dirty file
buffer. Safe exits happen immediately, even when clean undo/redo history,
selection, expansion, filter/sort, or navigation context will be lost. Root
Back follows the same rule. Clean recovery data is left in place for a future
session.

A dirty exit leaves the current body and view stack untouched and temporarily
replaces the footer with `Save modified file? Y Yes | N No | ^C Cancel` (using
a file count when more than one source is dirty). `Y` validates and applies
active workspace drafts, then preflights every dirty file with its normal exact
source check before writing. `N` delegates discard to each owning controller;
`C-c` cancels and restores the ordinary footer. Other keys, including repeated
`C-x`, do nothing while the question is active.

Multiple files cannot be one atomic transaction. A later write failure leaves
the application open and reports saved versus pending files. Conflicts and
invalid fields block save-and-exit without losing in-memory work; the footer
question is dismissed so the error is visible with the original body restored.

`InlineMenuSession` owns the global key and one non-recursive exit request, but
it does not inspect Org files or decide how data is saved. Controllers provide
dirty-state, prepare, save, and discard callbacks for their exit concerns.
`C-x` appears in contextual Help and, where width permits, the compact command
bar.

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

### Esc arrives on a timer

`Esc` is the one key above that a terminal cannot report plainly. It sends
`\x1b`, the same byte that opens an arrow or a Meta chord, so the reader has to
wait to learn whether anything follows. prompt_toolkit waits half a second by
default, which is long enough that `Esc` reads as a hang beside the `b` and `q`
that leave the same view at once. The suite waits 50ms: short enough to feel
immediate, wide enough to still assemble a sequence a slow link tore in two.
`ORTASK_ESC_TIMEOUT` widens the wait for links where that is not enough, at the
cost of making `Esc` sluggish again.

## Sort and Filter Interface (hybrid quick-toggle plus view screen)

> Gemini proposed the hybrid model below. Claude revised it against the shipped
> bindings and the task-tree invariant; the corrections are listed at the end of
> the section. The quick toggles and dedicated view screen are both part of the
> adopted direction.

### State of the work

`t0039.1` through `t0039.5` are done. `ortasklib/viewstate.py` holds the model,
both surfaces run their quick toggles off it, `ptui` has the filter it was
missing, `orti` has the sort it was missing, and `v` opens the view screen on
both. `t0039.6` (tag filtering) stays open.

Direction is reachable now, and it is declared per sort rather than as a global
Boolean. `ViewAxes.directions` maps each reversible order to the words for its
two ends — `A-Z`/`Z-A`, `newest first`/`oldest first`,
`highest first`/`lowest first` — and an order absent from that mapping cannot be
inverted at all. File order is absent, so `ViewState` refuses a reversed task
view outright and `with_sort` drops a direction the new order cannot hold. That
is what keeps children under their parents.

### Keep the model generic

A focused module was better than expanding `manager.py`, `taskui.py`, or
`menu.py`, but the first cut still mixed generic state with task and project
policy. `t0039.3` separated them before the screen was built on top:

- `ortasklib/viewstate.py` is the state machine and stable ordering, and imports
  nothing but the standard library — not `core`, not Org, not `prompt_toolkit`.
  A subprocess test blocks all three at the meta-path so a convenience import
  added later fails there rather than quietly turning it back into a hub.
- Task filter positions, their labels, and `task_state_matches` live in
  `taskui.py`, beside task presentation. Project axes, their labels, and their
  direction words live in `projmgr.py`, beside project presentation. `manager`
  keeps the sort and filter mechanics and knows nothing about badges or forms.
- `ortasklib/viewui.py` holds the prompt_toolkit adapter, so `viewstate.py`
  gains no view policy and `menu.py` gains no branches.

Keep it in-tree until a second application makes a Handrail extraction more than
speculation.

### The problem

Cycling is the fastest control for two or three positions on one axis, but it
does not expose the available choices or make the current combination easy to
set deliberately. The `v` screen provides that discoverable, explicit control
and a stable home for direction and later axes. It complements rather than
replaces the quick controls and persistent badge.

### Inline quick toggles

No new key was introduced. Both existing cycles moved onto the shared model and
each surface gained the one it lacked:

| Key   | Meaning      | `orti`                  | `ptui`                  |
|-------|--------------|-------------------------|-------------------------|
| `s`   | sort cycle   | File/Priority/Title     | Priority/Alpha/Mod      |
| `C-t` | filter cycle | all/TODO/DONE+          | all/open only           |
| `v`   | view screen  | Show, Order, Direction  | Show, Order, Direction  |

`ptui`'s project filter hides only what the navigator can prove is quiet. A
project whose task file is missing, broken, or unreadable stays in the list:
`docs/ptui.md` requires warnings to survive filtering, and a project that cannot
be read is not a project with nothing left to do.

Keys that are not available for this:

- `Tab` is fold and `S-Tab` is fold-all in the task list
  (`taskui.TASK_MENU_ACTIONS`). It cannot become a filter toggle.
- Plain `t` is ruled out by the keybinding section above.
- `S` (Shift-S) is one glyph away from the `S-↑`/`S-↓` priority family that
  both surfaces bind, so `v` carries the view screen instead.

`s` reaches `orti` only once the task list has more than one order to cycle
through — see "What `orti` cannot sort by yet".

### The view badge

Both surfaces already carried a mode word in the instruction line, so the badge
extends that rather than adding a second status area. It names what is being
shown and then how it is ordered — `all · File order`, `TODO · Priority order`
for tasks; `Priority sort`, `open only · Alphabetical sort`,
`Modified sort (oldest first)` for projects — and it is present always, not only
when the view is non-default. A reader who cannot see why a row is missing has
no way to get it back.

The same reasoning gives `ptui` a modification-age column. Naming the order in
the footer says how the list is sorted; the column is what lets a reader check
it, which is why it shows in every order and not only in Modified. See
`docs/ptui.md`.

An axis with a single position is not a choice: its key is not offered and it
stays out of the badge. Nothing has one today, but the rule is what let the task
list carry a sort axis through `t0039.1`–`t0039.4` without `s` doing anything.

### View Options screen (`v`)

`v` opens a `menu.WorkspaceView` built by `viewui.choice_screen()`. It reuses
the workspace's existing vocabulary rather than inventing a second form idiom:
↑↓ (or Tab) move among fields, `Enter` begins changing the focused one, ←/→
choose while changing, `Enter` or `Esc` finishes, and `Esc` again returns to the
list. Space-to-toggle was not adopted.

```text
View options: project navigator            Registry: ~/tmpsorta/proj2026
Showing: open only · Modified sort (oldest first)
  Show       [open only]
  Order      ◀ Modified ▶
  Direction  [oldest first]
↑↓ field · Enter change · ←/→ choose · C-s save · C-g help · Esc back
```

The offered positions differ per surface, and the screen renders the axes the
surface declares rather than a union with dead options. Both surfaces now show all three
fields, but not the same positions: `ptui` has no file order and no task states,
`orti` has no modification time. `ViewState.choices()` decides, so a field
appears the moment its axis has something to choose, and the screen grows into
tag filtering without changing its interaction model.

An order with no opposite reads `n/a` in the direction field rather than
vanishing, so the field list does not reshuffle under the cursor — and because
the state is the authority, a choice it refuses never leaves the form showing a
setting that is not in effect.

The direction field's words follow the selected order and track it live, so the
screen never shows the labels that were true when it opened.

### There is no apply key, so `C-s` still means save

Every choice takes effect the moment it is made. There is nothing to commit,
because view state is session-only and is never written to disk — an apply step
would be ceremony around a change that has already happened, and view choices
never make the file buffer dirty.

That matters for one key in particular. `C-s` means "write the file" in every
other context in the suite, and a screen that redefined it would punish the one
reflex a user actually has: someone with buffered priority edits who hits `C-s`
out of habit must not discover that it only closed a settings form. So `C-s`
inside the view screen does exactly what it does outside it — `orti` saves the
task file, `ptui` saves `projects.org` — and the footer says so. The navigator's
version deliberately leaves the screen open afterward: the key wrote the file,
which is no reason to close what the user is working in.

Since changes apply live and the list is covered while the screen is open, the
screen's summary line carries the resulting badge as it is built, and the list
underneath rebuilds through the same `on_resume` path every other pushed view
uses. There is no cancel: a view is not an edit, and changing a choice back
costs one keystroke.

### Vocabulary

One set of words in the code, the badge, the screen, and the CLI flags. The
code's triple is `all` / `todo` / `done` and the CLI flag is `--todo`; those
stayed as the wire values rather than growing "Incomplete" and "Terminal"
alongside them. Human labels need not repeat wire values: the badge says
`DONE+`, and contextual help spells out `DONE+ (DONE, MOOT, SUPERSEDED)` — all
three states that position actually selects.

### Direction is part of the key, not `reverse=True`

The recency order is `(unavailable, -mtime, name, name)`: unreadable task files
sort last, and the name tie-break is ascending. Passing `reverse=True` to
`sorted` would hoist the unreadable projects to the top and flip the tie-break
too. So `viewstate.order_by` runs three stable passes, weakest key first, and
inverts only the middle one; `manager` supplies each mode as separate `primary`,
`tiebreak`, and `unavailable` callables rather than one packed tuple.

That mechanism does not by itself make every order reversible, which is why
`ViewAxes.directions` names the reversible sorts and the words for each side.
The file-order task tree is not among them.

Each render also reads a project's task file exactly once.
`manager.snapshot_projects()` returns one `ProjectSnapshot` per project — open
count, total, modification time — and filtering, ordering, the `n open` column,
and the diagnostics all answer from it. That is not only cheaper: a row can no
longer disagree with the filter that kept it, which is what a second read
midway through a render would allow.

### Sorting a tree without breaking it

The task list is a tree rendered as a flat list, and every reader of that list —
fold, tree navigation, ancestor inclusion — assumes a parent is immediately
followed by its own subtree. A flat sort satisfies none of them: children end up
above other families' parents. `ptui`'s list is flat and has none of this
constraint, which is why only `orti` needed `t0039.5`.

So `taskui.sibling_ordered_items()` walks the tree depth-first and sorts only
each sibling group. Rows move only among the rows they belong with, the shape is
untouched, and source order is the final tie-break so equal siblings never
shuffle. Inversion applies to the chosen key alone.

File order stays the default and is the one order that cannot be inverted:
reversing it would put children before the parents they belong to. That is
declared, not assumed — file order is absent from `TASK_SORT_DIRECTIONS`, so
`ViewState` refuses a reversed task view and `with_sort` drops a direction the
new order cannot hold.

The one guarantee this changes is the keybinding section's "changing a priority
never reorders the task list". That now applies to file order. In Priority order
the edited task deliberately moves among its siblings, and the highlight follows
the task rather than the row.

### Numbered fallback

The numbered project loop offers `s=sort`, `t=filter`, digits, and `q`. Keep
those quick toggles as typed letters. The `v` screen remains
prompt_toolkit-only; numbered mode must not grow a form.

### Not in the first iteration

- **Tag filtering.** There is no tag index, and a tag filter interacts with
  ancestor inclusion in a way no current filter does. Its own task.
- **Multi-column sort.** One axis plus direction covers the cases named here.
- **Persistence.** View state is session-only. It is deliberately not written to
  `ortask.ini` or `projects.org`; `ptui`'s write path is bounded by `t0037` and
  a view preference is not worth widening it. `--todo` seeds the initial filter
  and nothing writes back.

### Revisions to the original proposal

1. `Tab` for the filter toggle — dropped; it is fold.
2. Two-way `Open ↔ All` toggle — kept as the shipped three-way cycle, which the
   two-way version would regress.
3. A new modal overlay — retained, but matching the workspace key vocabulary
   does not require reusing a transaction-oriented class with the wrong save
   model.
4. Sorting in `orti` — deferred to its own task, for the tree invariant.
5. The tag field — moved out of the first iteration.
6. Direction — the sort implementation inverts only its primary key, but UI
   exposure waits for per-sort capability and direction labels.
7. Multi-column sort (named in `docs/gemini-ortask-design.org`) — out of scope;
   the dialog sketch was already single-column.

## Workspace Discovery

`docs/projects.md` defines what a project is and how the registry is read. The
project TUI implements that model; it does not extend it. Two consequences
shape this interface:

- **Do not recursively scan arbitrary nested trees.** Registry membership is
  explicit. Use the shared task-file discovery convention from
  `docs/format.md`, and treat ambiguous files as a stop condition rather than
  sorting alphabetically.
- **A project with no task file is still a project.** It appears in the list
  with no task counts, rather than being omitted. So does a project whose
  symlink is broken, marked as broken. A list that hides its empty cases is
  worse than one that shows them, because a newly added project is the one the
  user is most likely looking for.

## Task Menu Workflow

After project or local-file selection, display open tasks as a menu:

```text
elweek tasks:
  1. [TODO] tw26W26 Promote June 24 ElectoramaWeekly episode
  2. [TODO] tw26W26.1 Prepare next episode
  e. open this Org file in editor
  Esc/b/q. back one level
```

The user chooses what to work on. The menu should support focus without hiding
judgment or forcing a computed priority order.

Default display order is the order of headings in the Org file. This preserves
the visible parent/child hierarchy, keeps authored weekly workflows readable,
and matches what an Emacs Org user expects after arranging a tree by hand.
The highlight-bar list initially shows only top-level tasks. Expansion reveals
children in file order without changing counts or task order. Only `TODO`
qualifies by default, matching what `ortask.py list` selects without flags;
`C-t` cycles the filter through `all -> TODO -> DONE+`.
Filtering retains a nonmatching ancestor when it provides the path to a
matching descendant, rather than promoting the descendant to a false root.
Org priorities such as `[#A]` remain visible metadata; they do not move rows.

The highlight-bar selector also re-anchors the highlight on the same task ID
across reloads, so a toggled task stays selected even if the list membership
changes. Because the menu order is file order, toggling TODO/DONE never moves a
row out from under the cursor. Collapsing a branch containing the selection
re-anchors the highlight to the nearest visible ancestor.

## Task Editor

Selecting a task opens a workspace over the same buffered Org file. State,
priority, title, and the complete body are editable on one screen rather than
menu rows or child contexts. State and priority use one compact control row:

```text
Edit tw26W26.0.3
Tags: promo · Subtasks: 2 · Line: 18

 State [TODO]       Priority [B]
Title
Post to reddit (/r/electorama)
Body
https://www.reddit.com/r/electorama/submit

Subtasks
▶ TODO    tw26W26.0.3.1 Record the posted URL
  DONE    tw26W26.0.3.2 Check the submission
[ Open in external editor ]

↑↓←→ fields · Enter edit/open · Ctrl-S save · Esc back
```

Title has initial focus in navigation mode. Arrow keys, Tab, and Shift-Tab move
among all fields and actions, including text fields, without changing values.
Enter begins editing the focused State, Priority, Title, or Body. In edit mode,
Left/Right changes State or Priority and ordinary text/cursor keys edit Title or
Body. Enter finishes a choice or Title; within Body it inserts a newline. Esc
finishes any edit and returns to field navigation. State cycles through `TODO`,
`DONE`, and `MOOT`; a parsed `SUPERSEDED` remains visible until changed, then
follows the canonical `MOOT` position. Priority stops at the ends of
`none -> C -> B -> A`. `Ctrl-S` validates and applies all four fields atomically,
saves the entire Org file, clears recovery data and transaction history, resets
both text-field undo histories, and leaves the workspace open at a new clean
baseline. The footer shows `TASK EDITED` after any field changes,
while the header independently shows `FILE MODIFIED: N edits` when task-list
transactions are pending.

Escape from navigation returns directly when the fields match the workspace
baseline. When they differ, it opens Save and Return, Continue Editing, and
Discard and Return choices. Continue Editing is selected by default. Discard
restores all four fields to the values loaded when the workspace opened or last
saved; it does not discard older edits already buffered from the task list.

The body boundary ends at the next Org heading, so child and sibling headings
cannot be changed from the body control. A five-row viewport contains every
descendant task in Org source order, indented by heading depth. Navigate to it
with arrows or Tab, press Enter to interact with it, then move its highlight
with Up/Down or `j`/`k` and five rows with Page Up/Page Down. Enter again opens
the selected descendant's workspace; Esc returns to field navigation. The
viewport scrolls to keep the selected descendant visible and does not truncate
the list. Back returns to the parent with its draft fields and subtask selection
intact. The header count includes all descendants. Direct state and priority
actions within the subtask viewport remain part of `t0016.4`.

The bottom `[ Open in external editor ]` button uses the same terminal handoff
and source-line targeting as `e` in the task list. Navigate to it and press
Enter. If workspace fields are dirty, opening it first
requires Save, Continue Editing, or Discard; Continue Editing is the safe
default. State and priority remain directly editable from the task list, and
printable letters type normally only while a text field is in edit mode. The
numbered fallback retains its older detail/action workflow.

Workflow-specific tools such as castabout may add domain actions such as "copy
draft", "open destination", or "record result". Those actions should still use
the same task identity, URL extraction, and writeback primitives supplied by
`ortasklib`.

## Operations

Initial operations should map to existing or planned CLI behavior:

| TUI action | Command equivalent |
| --- | --- |
| list projects | `projmgr.py list` |
| list tasks | `ortask.py list --todo --file FILE` |
| show details | `ortask.py show ID --file FILE` |
| mark done | `ortask.py done ID --file FILE` |
| set priority | interactive buffered heading edit |
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
