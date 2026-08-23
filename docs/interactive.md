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
to the same project. `m` opens the metadata workspace, including explicit
`TASK_FILE` mirror diagnostics and refresh. Shift-Up/Down changes the selected
project's priority in a session-wide `projects.org` buffer; `C-/`, `C-r`, and
`C-s` undo, redo, and save those edits, with visible dirty state and
save/discard handling on exit. The
project session also polls the index: clean buffers adopt external writes and
dirty buffers merge disjoint project-section changes in memory. A same-section
overlap remains a persistent named conflict with reload, retry, and continue
controls; no reconciliation writes the real index before `C-s`.

The task selector has two modes, chosen automatically by
`menu.interactive_select_available()`:

- **Highlight-bar mode** (interactive TTY with `prompt_toolkit`): task workflows
  run in one bounded `menu.InlineMenuSession`. Up/Down or `j`/`k`
  move the highlight (wrapping). The list starts as a top-level overview;
  `Tab` expands or collapses a task, Shift-Tab expands all or returns to the
  overview, and Left/Right provide directional tree navigation. `Enter` opens
  the focus view;
  Shift-Left/Right cycles the highlighted task through the `TODO`/`DONE` ring;
  Shift-Up/Down raises or lowers its priority; `p` opens an explicit priority
  picker; `C-/` undoes one logical task edit; `C-r` redoes it; `C-s` saves the
  Org file and clears that history; `C-t` cycles the visibility filter (`all ->
  TODO -> DONE`); `e` opens the editor at the highlighted task's line; `C-g`
  opens contextual command help; and `Esc`, `b`, or `q` goes back exactly one
  level. In `projmgr.py -i`,
  leaving a task list returns to the project menu; in local `ortask.py -i`, that
  task list is the top level, so leaving it exits. The application renders
  inline (not full screen), defaults to 20 rows, and repaints that region as
  contexts change. On a controlled exit it retains one final bounded frame with
  a factual footer such as `No changes to tasks.org`, `Saved changes to
  tasks.org`, or `Discarded changes to tasks.org`; it does not append each
  visited view.
  Long lists scroll within the row body while the title, summary, and key hint
  remain fixed; the selector keeps one context row above and below the highlight
  when space permits.
  Selecting a task opens the task workspace described below. Its fields start
  in navigation mode; Enter explicitly enables text or choice editing.
- **Numbered mode** (non-TTY, piped, or `prompt_toolkit` absent): the original
  numbered dashboard + prompt, preserved as the scriptable fallback with the
  same `C-t` visibility cycle. It lists the complete filtered hierarchy because
  it has no persistent cursor or per-task expansion state.

The top-level `projmgr.py -i` project list is the root view of the same bounded
application. Opening a project pushes its recovery or task view; leaving that
task context refreshes the project list and restores the same project by name.

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
- Right expands a collapsed task, then moves to its first child when pressed
  again. Left collapses an expanded task, then moves to its parent when already
  collapsed. These directional aliases follow conventional tree controls.
- Enter opens the highlighted task's detail/focus view.
- Shift-Right cycles the highlighted task forward through the TODO state ring;
  Shift-Left cycles backward. These should be documented as the primary state
  keys because they align with Org mode's `org-shiftright` / `org-shiftleft`
  behavior closely enough to transfer muscle memory.
- Shift-Up raises priority through `none -> C -> B -> A`; Shift-Down lowers it
  through the reverse sequence. The scale stops rather than wrapping at both
  ends, and changing priority never reorders the task list.
- `p` opens an explicit `A`/`B`/`C`/`none` picker. This is the discoverable path
  for users who do not want to memorize directional shortcuts.
- `C-/` (`C-_` on the wire) undoes one buffered task-list transaction. `C-r`
  redoes the most recently undone transaction; the redo stack is cleared by a
  new mutation.
- `C-s` writes the complete Org buffer to disk and starts a new history.
- `e` opens the current Org file or selected task in the editor.
- `C-t` cycles the task visibility filter: `all -> TODO -> DONE`. This is not
  an exact Emacs binding, but it keeps `C-c` and `C-x` open for future
  multi-key command families while reserving `/` for search.
- `C-g` opens contextual help. Although Emacs normally uses `C-g` to quit the
  current command, ortask uses it as the always-available command reference;
  `C-g`, `Esc`, `b`, `q`, or `Enter` returns to the unchanged menu selection.
- `/` should be reserved for search in the current list or backing Org file.
- `Esc`, `b`, and `q` all pop exactly one context. Only popping the top-level
  local task list or project list exits the program.

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

## Proposal: Hybrid Sort & Filter Interface (Gemini)

> **Attribution Note:** Gemini recommends this design. It is documented here for peer review and critique by other LLMs and human contributors. It should not be treated as a user-mandated constraint.

As lists in `ortask` (`orti`) and `projmgr` (`ptui`) grow across multiple dimensions (states, priorities, dates, tags, and project attributes), the interactive interface needs a clean balance between single-keystroke speed and multi-dimensional configurability.

### The Problem

- **Inline-only cycling** (e.g., repeatedly pressing `s` or `C-t` to cycle through every permutation) becomes cumbersome when there are more than 3–4 sort columns or multiple filter combinations.
- **Dedicated-only modal screens** add unnecessary interaction friction to the 90% use case (e.g., quickly toggling "Open tasks only" vs "All tasks", or toggling Priority vs Alphabetical order).

### The Proposed Hybrid Model

Gemini recommends combining **inline quick-toggles** with a **dedicated view configurator**:

#### 1. Inline Quick Toggles (Daily Flow)
For the most frequent 1-key operations directly on the list:
- **`s` (Sort Cycle):** Rapidly cycles through the primary 2–3 sort modes (e.g. `Priority` $\rightarrow$ `Alphabetical / Natural` $\rightarrow$ `Modified`).
- **`Tab` or `t` / `C-t` (Filter Toggle):** Rapidly toggles the primary task state filter (`Incomplete / Open` $\leftrightarrow$ `All`).
- **Header / Footer Badge:** Always displays the currently active view state (e.g. `[Filter: Open | Sort: Priority ↑]`), ensuring the user is never confused about why an item is hidden or where it is positioned.

#### 2. Dedicated View Configurator (`S` or `v`)
Pressing `S` (Shift-S) or `v` (View Options) opens a compact, bounded overlay/form for fine-grained multi-axis selection:

```text
┌─ View Options ──────────────────────────────────────────────┐
│ Filter State:  (•) Open/Incomplete   ( ) All   ( ) Terminal │
│ Filter Tags:   [                      ] (comma-separated)   │
│ Sort Column:   (•) Priority   ( ) ID/Natural   ( ) Modified │
│ Direction:     (•) Ascending  ( ) Descending                │
│                                                             │
│ [Space] Toggle · [Tab/↑↓] Navigate · [Enter] Apply · [Esc]  │
└─────────────────────────────────────────────────────────────┘
```

- **Radio / Choice Groups:**
  - **Filter State:** `Incomplete / Open` (TODO), `All`, `Terminal` (DONE / MOOT).
  - **Sort Axis:** `Priority`, `Natural / File Order`, `Task / Project Name`, `Modified Time`.
  - **Order:** `Ascending / Normal` vs `Descending / Inverted`.
- **Keyboard Navigation:** Standard arrow keys or `Tab`/`Shift-Tab` to navigate controls; `Space` to toggle radio selections; `Enter` to commit and re-render the list; `Esc` to cancel without changing active settings.

### Surface Applicability

- **Task Lists (`orti` / `ortask.py -i`):** Focuses on state (`Open` vs `All`), priority (`A`/`B`/`C`), tag filtering, and natural heading order.
- **Project Navigator (`ptui` / `projmgr.py -i`):** Focuses on project priority, alphabetical name, task count, and modified time.

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
children in file order without changing counts or task order. `TODO` and `DONE`
qualify by default; `C-t` cycles the filter through `all -> TODO -> DONE`.
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
