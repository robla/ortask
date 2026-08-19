# ortask Roadmap

This document holds design context for multi-step work that is too detailed for
`todo.org`. The task list remains the source of execution status and links to
the corresponding roadmap area by task ID.

## Interactive UI

### Goal

`ortask.py -i` should behave as one compact terminal mini app. By default it
should fit its height to the current view, grow only as much as useful up to a
20-row inline ceiling, preserve the shell output above it, and repaint that
same region when the user opens a task, Help, a priority picker, or another
context. A user who wants more room should be able to choose a fixed inline
height or a deliberate full-screen mode. The application should not leave
every prior menu in scrollback while the session is still running.

The same application shell now serves `projmgr.py -i`. The plain numbered menus
remain the non-TTY fallback and are not part of this rendering change.

**Status:** The main context migration is implemented at a fixed requested
height of 20 rows. `ort -i` keeps its task list, task details, nested subtasks,
Help, task confirmation, and priority
picker inside one bounded `InlineMenuSession`. External-editor launch suspends
and resumes that application. Dirty task sessions resolve Save,
Discard, or Continue Editing inside the bounded view stack, and task sessions
with recovery data begin with bounded Keep, Recover, and Discard choices. The
`projmgr.py -i` project list now serves as the root of that same view stack. The
normal final-frame policy and PTY contract are covered; explicit cleanup and
PTY coverage for abnormal exits remain. Adaptive sizing and optional
full-screen presentation are planned as `t0018`.

### Original Cause

The original production selector was inline but not a persistent application.
`ortasklib.menu._run_selector()` constructs a new
`prompt_toolkit.Application`, calls `Application.run()`, and exits that
application for every selection or action. Its original callers then looped and
called `select_menu()` again. Opening a task created another selector, returning
from that task created another task-list selector, and project navigation
repeated the same pattern.

The applications use `full_screen=False`, which correctly avoids the alternate
screen, but leave `erase_when_done` at prompt_toolkit's `False` default. Each
completed application therefore contributes a retained frame to terminal
history. Setting `erase_when_done=True` would hide some duplication, but it
would not solve the underlying lifecycle: nested views, text prompts, save
questions, and external-editor transitions would still be separate terminal
interactions.

Help already demonstrates the desired behavior within one selector. `Ctrl-G`
changes selector state and invalidates the existing application, so Help
replaces the rows and then restores them in place. That behavior established
the model now used by the rest of the interactive workflow.

### Bounded Inline Contract

The ortask interaction should follow these rules:

- One invocation owns one prompt_toolkit application until the user leaves its
  top-level interactive context.
- Inline mode uses `full_screen=False`; only an explicit full-screen request
  may enter the alternate screen.
- The default requested height is content-driven and capped at 20 rows. The
  effective inline height is at most `terminal_rows - 1`, leaving one row
  outside the application, and the application reports a clean error if its
  minimum layout cannot fit.
- A fixed header and command footer surround one scrolling body. Long task
  lists scroll inside the body while the selected row remains visible.
- Project lists, task lists, task details, Help, priority selection, recovery,
  and save/discard confirmation are views in the same bounded region.
- Enter or another action pushes a child view when appropriate. `Esc`, `b`, or
  `q` pops one view; only popping the top-level view exits the application.
- State and priority edits update the in-memory `OrgBuffer`, refresh affected
  rows, and invalidate the application without ending it.
- Running an external editor temporarily suspends the application, restores
  normal terminal operation, and repaints the same region after the editor
  exits.
- A controlled final exit retains one bounded final frame whose footer reports
  the latest factual outcome: saved, discarded, recovered/kept, or unchanged.
  It must not retain a historical copy of every visited view. Failures erase a
  possibly misleading partial frame after restoring terminal modes.

Twenty rows is the normal inline ceiling, not a feature limit. Search,
scrolling, nested views, contextual commands, editing, and Help should remain
available within the bounded viewport, while explicit full-screen mode can use
the terminal's remaining rows.

### Adaptive Height and Presentation Modes

Tracked as `t0018`. The fixed-height shell is the implementation baseline, not
the desired default policy.

The default `auto` policy should compute a preferred total height from the
active view's visible rows plus its chrome. A compact task list can omit the
otherwise blank header spacer, using two header rows and two footer rows; three
tasks can therefore occupy seven rows instead of reserving 20. Expanding a
subtree, opening Help, or entering a workspace should recompute the preference
and grow the same application up to 20 rows. Collapsing or returning to a
smaller view should contract it again, provided PTY tests show no stale lines
or disruptive scrollback movement.

Sizing needs three separate values:

- the view's minimum and preferred body rows;
- the session's requested mode and height (`auto` or a positive integer); and
- the terminal-clamped effective height used by prompt_toolkit.

Views should report their own sizing needs rather than teaching the session
about tasks, Help, pickers, or editors. Explicit `--height N` should disable
auto-fit for that invocation while preserving terminal clamping. A `fit`
command can restore automatic sizing later.

`--full-screen` should construct the same views in a prompt_toolkit
`Application(full_screen=True)`. This is a distinct presentation mode, not a
numeric height: it uses the alternate screen, fills the terminal, and restores
the prior shell display on exit instead of retaining an inline final frame.
The first implementation can select the mode at startup. Runtime switching is
not required initially; when added, it should live in Handrail's Resize mode
rather than depend on a function key that is difficult to generate on compact
keyboards. A controlled application rebuild or another well-tested transition
must preserve view, focus, edits, and terminal state.

Handrail's viewport convention should be consistent across orti and inedit:

- `Alt-Up` and `Alt-Down` scroll the active viewport without changing the
  logical selection or cursor. Scrolling stops before that anchor would leave
  the visible region.
- `C-^` enters Resize mode. Terminals encode this as the same control character
  as `C-6`, so Help should teach both spellings and tests should feed the actual
  `0x1e` byte.
- Inside Resize mode, Up makes the bottom-anchored application taller, Down
  makes it shorter, `a` restores automatic content-fit sizing, `m` maximizes
  inline, and `f` enters or leaves full-screen presentation. `C-^`, Enter, or
  Back leaves the mode.

The live footer should identify Resize mode and its available commands. The
first manual Up or Down disables auto-fit for the invocation; `a` restores it.
This is a session presentation change, not a data mutation, so leaving the mode
keeps the chosen size without creating a dirty task buffer.

Nano uses `C-6` to set its mark, but Handrail deliberately gives CUA-style
selection priority and uses this key for the less frequent layout mode. The
binding remains a recognizable simplification of Emacs's `C-x ^` vertical
window command without consuming `C-x`, which inedit and nano use for Exit.
See [nano's mark command](https://www.nano-editor.org/dist/v5/nano.html) and
[Emacs window resizing](https://www.gnu.org/software/emacs/manual/html_node/emacs/Change-Window.html).

### Application Shape

The implementation now provides a session-oriented API alongside the one-shot
selector:

- `menu.InlineMenuSession` owns requested/effective height, the active view
  stack, transient messages, movement, Help, terminal resize, external-command
  suspension, and the one call to `Application.run()`.
- `menu.MenuView` describes a title, summary, rows, selected index, available
  commands, optional detail text, sizing preferences, and callbacks for actions
  and resume.
- `taskui.InteractiveTaskController` constructs task-specific views and owns
  Org parsing, `OrgBuffer` edits, stable task selection, and task actions.

The implemented layout follows inedit's proven structure: an `HSplit` with a
dynamic header, one body region, and a fixed footer, all constrained by a
callable height. Menu, detail, Help, picker, and confirmation views share one
`FormattedTextControl`; heading-text editing switches that body to a focused
single-line `TextArea` without starting another application.

Command metadata remains the source for bindings, footer hints, and `Ctrl-G`
Help. New `MenuAction` handlers should transition session state rather than
return to an outer Python loop.

### Migration Stages

#### 1. Characterize the terminal contract

**Implemented for normal operation.** Pipe-input tests cover task/subtask/Help/
picker transitions in one application, bounded scrolling, effective-height
clamping, buffered edits, terminal handoff, and captured VT output. A real PTY
test covers a 55-task scroll, live resize repaint, Help, final retained outcome,
termios restoration, absence of common alternate-screen entry sequences, and
placement of the next prompt. Abnormal signal and exception cases remain in
Stage 6.

The tests distinguish repaint control sequences from retained frames, verify
that no common alternate-screen entry sequence is emitted, and confirm that
the cursor and next shell prompt end below the application.

#### 2. Add the persistent bounded shell

**Implemented as the fixed-height baseline.** `InlineMenuSession` supplies the
20-row dynamic header/body/footer layout and resize clamping for local task
sessions and the registry-scoped project/task stack. Compatibility one-shot
selectors remain available but are no longer used by production interactive
paths.

The existing `select_menu()` API remains for compatibility and focused legacy
tests, but production interactive paths no longer use repeated one-shot
applications. The obsolete API is now eligible for separate cleanup.

#### 3. Move contexts onto a view stack

**Implemented.** Local task details, nested subtasks, Help, task confirmation,
priority selection, and the `projmgr.py -i` project list now push, pop, or
replace `MenuView` instances without ending the application. Returning from a
task context refreshes the project list and restores selection by project name.

Help can be either a modal flag over the current view or an explicit stack
entry, but it must use the same command metadata and close back to the exact
selection. A picker cancellation must similarly restore the parent without
changing data.

#### 4. Bring prompts inside the application

**Implemented for interactive task sessions.** Leaving a dirty task session
pushes a bounded Save/Discard/Continue Editing view. Entering with distinct
auto-save data starts with a bounded Keep/Recover/Discard view, with Keep as the
safe default for both Enter and Back. Outcomes appear in the footer, and
canceling an exit returns to the live task list with edits intact. The numbered
fallback deliberately retains its plain prompts.

Operational warnings and feedback use a transient footer message that restores
the current command hint after about two seconds. Replacing a notice or leaving
its view cancels the older timeout so it cannot clear newer text. Final save,
discard, and recovery outcomes remain persistent.

Keep the existing `OrgBuffer` safety contract: navigation is read-only, edits
remain buffered and mirrored to the auto-save file, and the real Org file is
written only through an explicit save path. State and priority actions are
logical snapshot transactions: `C-/` undoes, `C-r` redoes, and `C-s` saves the
whole file and resets both stacks. The task-list header keeps the file-level
dirty count visible even when a transient footer message is present.

#### 5. Suspend for the external editor

**Implemented for the task session.** `InlineMenuSession.suspend()` uses
prompt_toolkit terminal handoff, and the task controller reloads and replaces
the active view after `_open_editor()` returns.

Before suspension, the controller resolves pending buffered edits according to
an explicit policy. After the editor returns, it reloads the file, reparses
tasks, restores the closest stable selection, and repaints the bounded
application. Editor-launch failures still need to become bounded status
messages rather than raw output interleaved with the UI; Stage 6 tracks that
work.

#### 6. Finish lifecycle and final display

**Partially implemented.** `InlineMenuSession` starts with
`erase_when_done=True`. A controlled root pop records the latest factual
outcome, renders it in the final footer, switches erasure off, and lets
prompt_toolkit place the cursor below the retained frame. Save/discard outcomes
survive a return to the project root; otherwise the controller reports no
changes. Real PTY coverage verifies the normal retained frame and terminal
restoration.

Signal handling, unexpected exceptions, editor failures, and terminal resize
below the minimum still need explicit failure outcomes and PTY coverage. Those
paths must leave erasure enabled and restore the terminal before diagnostics.

### Relationship to Handrail

This work should establish the bounded-inline behavior before ortask depends on
a new shared library. The reusable concepts are the persistent inline shell,
viewport policy, command metadata and Help, view-stack transitions, stable-row
selection, suspend/resume, and terminal test harness. Org parsing, task
mutation, project discovery, and `OrgBuffer` remain ortask responsibilities.

If inedit and ortask converge on the same semantics after this migration, those
proven pieces become candidates for a future common package. Avoid designing a
backend-neutral SDK first and then forcing both applications through it.

### Risks Observed So Far

A brief external read of `ortasklib/menu.py`, the task UI, and their git
history against inedit's `_inedit/` split, for context ahead of any shared
extraction:

- **Compatibility selectors still coexist with the production stack.**
  `_run_selector`, `select_menu`, and `select_project_menu` remain in `menu.py`
  alongside `InlineMenuSession`/`MenuView`, but `projmgr.py -i` has now moved to
  the persistent stack. Removing the unused compatibility path is separate
  cleanup rather than a blocker for the bounded-session roadmap.
- **One ortask module is doing what inedit splits three ways.** inedit
  separates presentation, application lifecycle, and terminal handling into
  distinct modules with an enforced acyclic dependency graph
  (`inedit/AGENTS.md`). ortask's equivalent surface — rendering, session
  lifecycle, dashboards, and plain-text printing — is still one `menu.py`.
  Splitting that locally (this roadmap's own Migration Stage 2/3 territory)
  seems worth doing before treating `menu.py` as a stable extraction source.
- **Cosmetic churn has already cost real coordination overhead.** The
  highlight-bar color/continuity logic went through four consecutive commits
  by three different agents (Codex, Gemini, Gemini, Claude) on 2026-06-28
  before it settled. A shared style-token layer, of the kind
  `handrail-plan.md` proposes, would likely have turned that into a one-line
  change instead of a rewrite-review-rewrite cycle.
- **`InlineMenuSession` is good evidence for a shared vocabulary, not proof of
  a cross-project library yet.** Its view/session shape already lines up with
  `handrail-plan.md`'s `InlineApp`/`View`/`ViewStack`/`SelectionResult`
  sketch, and both local-task and project/task controllers now use it. Those
  are still two paths in one application, not independent consumers; shared
  extraction should continue to wait for matching evidence from another app.

### Completion Criteria

The interactive UI work is complete when:

- `ort -i` and `orgm -i` each run one prompt_toolkit application per session;
- the default live interface fits compact views, grows through content changes,
  and stays within its effective 20-row inline ceiling;
- fixed-height and full-screen requests preserve the same navigation, editing,
  and safety behavior, with alternate-screen use confined to full-screen mode;
- returning from task details does not append another full task list;
- long lists scroll without losing the selected row;
- external editor handoff resumes the same session cleanly;
- save, discard, cancellation, and recovery remain safe and visible;
- non-TTY numbered behavior remains usable;
- pipe-input and PTY tests defend adaptive repainting, resize, terminal
  restoration, inline alternate-screen avoidance, and full-screen entry/exit;
  and
- signals, unexpected exceptions, editor failures, and undersized terminals
  restore terminal state before reporting a clear failure.

## Task Archiving

Tracked as `t0010`. This is a separate area from the interactive-UI work above.

**Status:** Implemented in `ortask.py archive`; interactive archive actions and
custom destinations remain future work.

### Goal

Move completed tasks out of the working task file without inventing an ortask
convention. An Emacs org user who already archives with `C-c C-x C-a` should
find ortask's archive file unremarkable, and should be able to keep archiving
from Emacs afterward with no ortask involvement.

### Stock Emacs Behavior to Match

[Org's archive-file documentation](https://orgmode.org/manual/Moving-subtrees.html)
describes `org-archive-subtree` and `org-archive-location`, whose stock value
is:

```
"%s_archive::"
```

`%s` expands to the source file's basename including its extension, so
`todo.org` archives to `todo.org_archive`. The empty location after `::` does
not specify a container heading. `* Archived Tasks` is a common customization,
not stock behavior, and ortask should not create it unless the effective
archive location requests it.

Org records source context in a property drawer on the archived heading. The
fields are controlled by `org-archive-save-context-info`; its stock settings
include archive time, source file, former outline path, category, TODO state,
and inherited tags. Ortask should define and test its compatibility baseline
explicitly rather than assuming every Emacs configuration writes identical
metadata.

Common customizations include overriding the destination filename, adding a
target such as `* Archived Tasks`, or tagging entries `:ARCHIVE:` in place
instead of moving them. Archiving in Org is not exclusively a file move.

Stock archiving does not copy the source file's in-buffer keyword settings, and
`.org_archive` is not in `auto-mode-alist`, so a newly created archive gets a
`-*- mode: org -*-` line but no `#+TODO:` declaration. Any custom terminal
keyword in the moved subtrees then stops parsing as a state: Org folds the word
into the heading title instead. Archiving ElectoramaWeekly's four terminal
weeks produced an archive in which only 23 of 64 headings still read as
complete, until the declaration was added by hand.

### Implemented Direction

The `archive` verb moves one or more DONE subtrees to
the archive file and writes the documented stock-compatible context
properties. The archive target derives from the task file's own name, so
`tasks.org` archives to `tasks.org_archive`; a later config key or `--to`
option can override it.

Constraints that follow from existing project rules:

- IDs are permanent and never reused, so archived tasks keep their IDs and ID
  allocation must continue to account for them.
- Archiving moves a whole subtree; archiving a parent takes its subtasks with
  it. Archiving a subtask while its parent stays open is supported as described
  below.
- The archive file is a destination, not a task file. It must not be picked up
  by `ortask.py`'s discovery order, and it must not appear as a project's task
  file in `projmgr.py`. Reading it — `ort list --archived` or similar — is a
  reasonable later addition, but the default views should stay quiet.
- Writes stay atomic and symlink-resolving for both files. Because two file
  replacements are not one atomic transaction, the operation needs rollback
  or recoverable transaction state so a failure does not lose or duplicate the
  subtree.
- A newly created archive file should carry the source file's `#+TODO:`
  declaration, so custom terminal keywords keep parsing as states rather than
  decaying into heading text. This is a deliberate deviation from stock
  behavior; see castabout's `docs/roadmap.md` "Archive Declaration". When the
  archive already exists, merge into its declaration rather than adding a
  second one, and keep keywords that earlier entries were archived under — an
  archive accumulates across renames, so its declaration is the union over
  time, not a snapshot of current vocabulary.

The implemented command resolves the original selection questions as follows:

- Bare `ort archive` selects literal `DONE` headings in source order. It does
  not sweep `MOOT` or compatibility-alias `SUPERSEDED` headings.
- `ort archive ID` moves only that task's whole subtree, regardless of state.
- A selected DONE child can move while its parent remains open. If a selected
  ancestor already contains other DONE candidates, the subtree moves once.
- Both files use atomic replacement. The archive is written first and restored
  if replacing the source fails.
- `add` includes IDs found in the adjacent archive when allocating new IDs.

### Remaining Questions

- Whether the TUI gets an archive action, and whether it needs confirmation
  given that archiving is reversible only by editing two files.
- Whether ortask should recognize an existing `#+ARCHIVE:` keyword or an
  `:ARCHIVE:` property in the task file and honor it over the default.

## Orti Issue-Editing Workspace

Tracked as `t0014` (initial title-editing foundation), `t0015` (direct state
and priority actions), and `t0016` (the full workspace). `orti` is the user's
shell alias for `ortask.py -i`; it is not a separate executable or data format.

### Product Direction

Orti should become a compact TUI editor for an Org-backed bug and task
database. Its interaction model should resemble a small issue tracker: users
can browse, inspect, and edit an issue without routinely leaving the
application. The Org file remains the authoritative, human-editable backend.

The external editor is still important for arbitrary Org restructuring, but
it is an escape hatch rather than a peer of every in-app edit. The task view
must therefore stop presenting synthetic `TEXT`, `EDIT`, `STATE`, or `PRIOR`
rows. `e` remains a documented task-list command that suspends the bounded
session and opens the highlighted task at its source line. Once an editable
field has focus, printable letters are text; Escape returns to the task list.

### Org Issue Boundary

For the first full editor, an issue consists of:

- its heading title, TODO state, priority, identifier, and tags;
- its own body between the heading and its first descendant heading, including
  prose, planning lines, and property drawers; and
- its descendant task headings, displayed as related subtasks but excluded
  from the parent's body editor.

This definition keeps ordinary Org files valid and prevents a multiline edit
from accidentally consuming or rewriting child and sibling subtrees. Orti
should not introduce a private issue syntax merely to imitate JIRA fields.

### Interaction Model

The task-list view remains the fast triage surface. State, priority, filtering,
Help, and other common commands operate directly on the highlighted task.
`Enter` opens an issue workspace rather than a menu of actions.

The list starts as a top-level overview. `Tab` toggles a task's direct children,
Shift-Tab toggles all branches, and Left/Right collapse, expand, or move between
parents and children. Collapsed and expanded parents show `▸` and `▾`
respectively. Folding preserves Org file order and is independent from state
filtering and summary counts.

The issue workspace keeps these regions visible together:

- the actual title and compact state, priority, identifier, and tag metadata;
- a multiline description/body editor;
- a bounded, selectable descendant viewport; and
- a bottom external-editor escape hatch.

Focus moves among editable regions without pushing a new view for each field.
State and priority use compact, focusable controls in one row; Left/Right
changes the focused value without opening another view. Optional pickers, Help,
or destructive confirmation may still use bounded overlays or child views. The
workspace must adapt to the 20-row inline ceiling, use additional rows in
full-screen mode, scroll long content, and give the body more room when no
subtasks exist.

The subtask region shows all descendant tasks in source order, indented by Org
depth, in a five-row scrolling viewport. It has its own highlight; Up/Down and
Page Up/Page Down reach every descendant without truncation. Enter pushes the
child's issue workspace; Back returns to the existing parent workspace with
its draft fields and prior subtask selection intact. This is navigation between
issues, not a substitute for showing the parent and its subtasks together.
Direct state and priority commands on the highlighted subtask remain planned.

The external editor button is focusable and guards dirty fields with the same
Save/Continue/Discard policy as workspace exit.

### Editing and Safety

All in-app mutations continue through `OrgBuffer`. Task-list state and priority
actions remain buffered with auto-save recovery until the file-level save flow.
Each action is one labeled full-text transaction. Undo and redo update recovery
data and the header's `FILE MODIFIED` count; save, discard, recovery reload, and
external-editor reload establish new history boundaries.

The task workspace keeps compact state and priority controls, a one-line title,
and a multiline body visible at the same time. Tab and Shift-Tab move focus;
Left/Right changes a focused choice, and Enter advances a choice, moves from
title to body, or inserts a body newline. `Ctrl-S` validates all four fields as
one transaction, updates `OrgBuffer`, atomically saves the entire Org file,
removes recovery data, resets both field undo histories, and clears the file
transaction stacks.
The prompt_toolkit field histories and `OrgBuffer` transaction history remain
separate: typing is undone within a focused field, while task-list actions are
undone as complete Org mutations.

The workspace tracks all four fields against the values loaded on entry or at
the last successful save and marks changed controls `TASK EDITED` in the
footer. Escape returns immediately when they match that baseline. When they
differ, Escape opens Save and Return, Continue Editing, and Discard and Return
choices; Continue Editing is the safe default. Discard affects only unpersisted
workspace controls, not older changes already held by `OrgBuffer`.

`tasks.change_body()` replaces only the non-heading lines after the selected
task heading and before the next Org heading. Text that would create a heading
is rejected so descendants, siblings, and unrelated sections cannot be
absorbed accidentally.

### Architecture Direction

`projtui.py` composes application-specific title/body `TextArea` controls and
compact state/priority windows and action buttons inside the generic
`menu.WorkspaceView` lifecycle seam. `InlineMenuSession` owns focus switching,
list-region movement, button activation, Help, save dispatch, dirty
presentation, guarded Back, and terminal lifecycle while remaining agnostic
about task fields and mutation.
Editing stays inside the same bounded `Application`; do not start a second
prompt_toolkit application.

This is proto-Handrail work, but extracting a shared package is premature.
Keep reusable seams clear and compare them with inedit; extract only after a
second adopter exposes stable shared behavior or duplicated bugs.

### Implementation Order

1. Finish abnormal terminal cleanup (`t0009.6.2`), remove obsolete selectors
   (`t0011`), and diagnose the standalone Escape delay (`t0013.1`).
2. Define and test the task-own-body boundary and surgical mutation helper.
   (Completed by `t0016.1`.)
3. Replace the action-row detail menu with a persistent issue-workspace shell.
   (Completed by `t0016.2`.)
4. Add multiline body editing within the existing buffer and save contract.
   (Completed by `t0016.3`.)
5. Complete direct state and priority actions in both list and workspace views.
   (Compact workspace controls completed by `t0015.3`; optional picker and
   plain-key list aliases remain separate follow-ups.)
6. Add the hierarchical, independently scrollable subtask region. (The
   source-order viewport, highlight, scrolling, and child navigation are
   complete; direct subtask actions remain.)
7. Add tag editing and terminal, recovery, resize, and performance coverage.

### Completion Criteria

- Title, own-body text, state, priority, and tags can be edited without an
  external editor.
- The selected issue and its subtasks can be read and acted on in one bounded
  workspace, including with long bodies and subtask lists.
- Action-like pseudo-rows are gone; external editing is an optional task-list
  `e` command and bottom workspace button.
- Save, discard, recovery, terminal cleanup, and source-structure preservation
  retain their current safety guarantees.

Comments/activity feeds, attachments, custom workflow fields, and general Org
tree restructuring are outside the first workspace milestone. They can be
considered after the core issue-editing model proves useful.

## Project Navigator

Tracked as `t0019`. The model this section builds on is `docs/projects.md`;
this section covers the tool shape, the names, and the order of the work.

**Status:** implemented on 2026-08-19. `pmgr add` registers the project you are
in, projects without task files are listed, `orgmgr.py` is `projmgr.py`,
`projtui.py` is `ortasklib/taskui.py`, `cdproj` moved with the project layer, and
one project view serves the navigator, `cdproj`, and both numbered fallbacks.
`init`, `rm`, and `doctor` exist. What remains is listed under *Still open*.

### Goal

The project layer should be something the user is eager to start up: one
command that shows the projects currently being worked on, and one word that
adds a new one. Before this work it was neither. Registering a project meant
recalling `orgmgr.py projadd`, a project without a task file did not appear at
all, and the tool that drew the project list was named `projtui.py`, which was
neither the manager nor, mostly, about projects.

Nothing in this section changes the registry model. The symlink registry is
the part that works, and `docs/projects.md` now records why.

### Names

Two commands, four names, matching what actually exists:

| Name | What it is |
| --- | --- |
| `ort` | `ortask.py` — one task file |
| `orti` | `ortask.py -i` — the issue workspace over that file |
| `pmgr` | `projmgr.py` — the project layer: registry, list, `add`, `cdproj` |
| `ptui` | `projmgr.py -i` — the project navigator |

`pmgr` alone lists, as `ort` alone lists.

The interactive alias is `ptui` rather than the strictly parallel `pmgri`.
`orti` works because `ort` is short and `-i` appends cleanly; `pmgri` is a
five-letter finger-tangle that reads as nothing. `ptui` reads as "project TUI",
matches existing muscle memory from `projtui.py`, and gives the surface the
user most wants to open a name of its own rather than a modifier on another
name. This is one line of shell configuration and is cheap to reverse.

### The rename was on `orgmgr.py`, not `projtui.py`

`projtui.py` was 2103 lines and was imported by both `ortask.py` and
`orgmgr.py`. Only its last thirty lines were a front-end. It was library code
wearing a script name, and that was the largest single source of the muddle
between the tools.

Its contents also do not divide the way the filename suggests. About 105 lines
(`InteractiveProjectController`, `project_menu`) are the project browser.
Everything else — `OrgBuffer`, the task list, the issue workspace,
`InteractiveTaskController` — is the `orti` surface, which has nothing to do
with projects and is the subject of `t0016`.

So:

- `orgmgr.py` became `projmgr.py`. It was already the project-layer front-end;
  it gained the name.
- `projtui.py`'s task UI moved into `ortasklib/taskui.py` as the shared library
  it already was, leaving the project browser to `projmgr.py`.
- `projtui.py` stopped existing as a script. `ptui` is an alias for
  `projmgr.py -i`, not a file.

This restored the architecture `docs/architecture.md` already claimed: thin
front-ends over `ortasklib/`, with no script importing another script.

### Verbs

`projmgr.py` verbs, alphabetical as usual: `add`, `cdproj`, `doctor`, `help`, `init`,
`list`, `rm`.

- `add` replaces `projadd`, and is specified in `docs/projects.md`. The prefix
  was only ever there to disambiguate from local task verbs in a tool that also
  had none; `pmgr add` adds a project exactly as `ort add` adds a task.
- `rm` replaces the planned `projrm`, for the same reason.
- `init` replaces `migrate`, which no longer migrates anything — it has only
  written the registry path into `ortask.ini` since `projtui.ini` was removed.
  Low priority, but a verb whose name describes a job it no longer does is the
  kind of drift this section exists to clear.
- `cdproj` moves here from `orgmgr.py` unchanged in behavior (originally named `pcd`).
  It was always a project-layer command; it landed in `orgmgr.py` because that was
  where the registry lived.

### One project list, two entry points

`cdproj` and `ptui` drew two different project pickers over the same registry. They
now share `_project_rows`, `_project_location`, `_anchor_index`, and
`_project_view` in `projmgr.py`, so only what `Enter` does differs: open the
project's tasks, or write its directory stack and exit. The numbered fallbacks
share the same rows.

What is still separate is the session wrapper — `_ProjectBrowser` keeps a task
controller and a resume hook, `_CdprojSession` keeps an output path and an edit
picker. That difference is real, and merging the two classes would only hide it.

### What the list must show

A project list worth opening has to survive the empty cases, because the
project you just added is the one you most want to see:

- every registered project, including those with no task file;
- open task counts where a task file exists, and a plain "no tasks yet" where
  one does not;
- broken projects, marked as broken rather than omitted.

### Implementation Order

All six steps are complete (`t0019.1`–`t0019.6`):

1. Retired the `SKIP_PROJECT_DIRS` blocklist for the outward-symlink marker, and
   made a task file optional in `manager.discover_projects()`. This was the
   behavior change; everything after it was naming.
2. Added `add` with project-root walking.
3. Renamed `orgmgr.py` to `projmgr.py`, moved `cdproj` (initially `pcd`) with it, updated
   `misc/ortask-completion.bash`, `misc/cdproj.func.sh`, and the `pmgr`/`ptui`
   aliases, and renamed `docs/orgmgr.md` to `docs/projmgr.md`.
4. Moved `projtui.py`'s task UI into `ortasklib/taskui.py` and its project
   browser into `projmgr.py`; deleted the script.
5. Converged the `cdproj` picker and the `ptui` project list onto one row and view
   builder.
6. Renamed `migrate` to `init`; added `rm` and `doctor`. `migrate` and `projadd`
   remain as deprecated aliases.

### Still open

- The marker rule allows one directory symlink per entry. A registry whose
  entries are *real* project directories rather than symlinks — which is what a
  fresh `~/Projects` would be — shows nothing. That is deliberate for now
  (explicit membership is the point), but it is the likeliest reason someone
  else's registry would look empty.
- `add` does not offer to create a task file for a project that has none, and
  `pcd` still has no way to append the current directory to a project's stack
  (`docs/projdirs.md`).
- The deprecated `migrate` and `projadd` aliases should eventually go.

### Completion Criteria

All met:

- `pmgr add` in an arbitrary project directory registers it, with or without a
  task file, and says what it registered.
- `ptui` opens a list of every registered project, including new and
  task-less ones, as the root of the same bounded session.
- No script imports another script; `projtui.py` is gone.
- `pcd`, `pmgr`, and `ptui` share one project list and one project record.
- The registry remains readable, editable, and repairable with `ls`, `ln -s`,
  and `rm`.
