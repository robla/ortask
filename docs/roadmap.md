# ortask Roadmap

This document holds design context for multi-step work that is too detailed for
`todo.org`. The task list remains the source of execution status and links to
the corresponding roadmap area by task ID.

## Interactive UI

### Goal

`ortask.py -i` should behave as one compact terminal mini app. By default it
should occupy a 20-row region at the bottom of the terminal, preserve the shell
output above it, and repaint that same region when the user opens a task, Help,
a priority picker, or another context. It should not leave every prior menu in
scrollback while the session is still running.

The same application shell now serves `orgmgr.py -i`. The plain numbered menus
remain the non-TTY fallback and are not part of this rendering change.

**Status:** The main context migration is implemented. `ort -i` keeps its task
list, task details, nested subtasks, Help, task confirmation, and priority
picker inside one bounded `InlineMenuSession`. External-editor launch suspends
and resumes that application. Dirty task sessions resolve Save,
Discard, or Continue Editing inside the bounded view stack, and task sessions
with recovery data begin with bounded Keep, Recover, and Discard choices. The
`orgmgr.py -i` project list now serves as the root of that same view stack. The
normal final-frame policy and PTY contract are covered; explicit cleanup and
PTY coverage for abnormal exits remain.

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
- The application uses `full_screen=False` and never enters the alternate
  screen.
- The requested total height defaults to 20 rows. The effective height is at
  most `terminal_rows - 1`, leaving one row outside the application, and the
  application reports a clean error if its minimum layout cannot fit.
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

Twenty rows is a default, not a feature limit. Search, scrolling, nested views,
contextual commands, editing, and Help should remain available within the
bounded viewport. A later `--height` option or environment setting can be
added if a real workflow needs it; configuration is not required for the first
migration.

### Application Shape

The implementation now provides a session-oriented API alongside the one-shot
selector:

- `menu.InlineMenuSession` owns requested/effective height, the active view
  stack, transient messages, movement, Help, terminal resize, external-command
  suspension, and the one call to `Application.run()`.
- `menu.MenuView` describes a title, summary, rows, selected index, available
  commands, optional detail text, and callbacks for actions and resume.
- `projtui.InteractiveTaskController` constructs task-specific views and owns
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

**Implemented.** `InlineMenuSession` supplies the 20-row dynamic
header/body/footer layout and resize clamping for local task sessions and the
registry-scoped project/task stack. Compatibility one-shot selectors remain
available but are no longer used by production interactive paths.

The existing `select_menu()` API remains for compatibility and focused legacy
tests, but production interactive paths no longer use repeated one-shot
applications. The obsolete API is now eligible for separate cleanup.

#### 3. Move contexts onto a view stack

**Implemented.** Local task details, nested subtasks, Help, task confirmation,
priority selection, and the `orgmgr.py -i` project list now push, pop, or
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

Keep the existing `OrgBuffer` safety contract: navigation is read-only, edits
remain buffered and mirrored to the auto-save file, and the real Org file is
written only through an explicit save path.

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

A brief external read of `ortasklib/menu.py`, `projtui.py`, and their git
history against inedit's `_inedit/` split, for context ahead of any shared
extraction:

- **Compatibility selectors still coexist with the production stack.**
  `_run_selector`, `select_menu`, and `select_project_menu` remain in `menu.py`
  alongside `InlineMenuSession`/`MenuView`, but `orgmgr.py -i` has now moved to
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
- the live interface stays within its effective 20-row region through lists,
  details, Help, pickers, prompts, and back navigation;
- returning from task details does not append another full task list;
- long lists scroll without losing the selected row;
- external editor handoff resumes the same session cleanly;
- save, discard, cancellation, and recovery remain safe and visible;
- non-TTY numbered behavior remains usable;
- pipe-input and PTY tests defend repainting, resize, terminal restoration, and
  absence of alternate-screen switching; and
- signals, unexpected exceptions, editor failures, and undersized terminals
  restore terminal state before reporting a clear failure.

## Task Archiving

Tracked as `t0010`. This is a separate area from the interactive-UI work above.

**Status:** Specified; implementation has not started.

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

### Direction for ortask

Add an `archive` verb to `ortask.py` that moves one or more DONE subtrees to
the archive file and writes the documented stock-compatible context
properties. The archive target derives from the task file's own name, so
`tasks.org` archives to `tasks.org_archive`; a later config key or `--to`
option can override it.

Constraints that follow from existing project rules:

- IDs are permanent and never reused, so archived tasks keep their IDs and ID
  allocation must continue to account for them.
- Archiving moves a whole subtree; archiving a parent takes its subtasks with
  it. Archiving a subtask while its parent stays open needs an explicit answer.
- The archive file is a destination, not a task file. It must not be picked up
  by `ortask.py`'s discovery order, and it must not appear as a project's task
  file in `orgmgr.py`. Reading it — `ort list --archived` or similar — is a
  reasonable later addition, but the default views should stay quiet.
- Writes stay atomic and symlink-resolving for both files. Because two file
  replacements are not one atomic transaction, the operation needs rollback
  or recoverable transaction state so a failure does not lose or duplicate the
  subtree.

### Open Questions

- Whether `archive` takes explicit IDs, a `--done` sweep, or both.
- Whether MOOT and compatibility-alias SUPERSEDED subtrees are eligible.
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
rows. `e` should remain a documented command that suspends the bounded session
and opens the current task at its source line.

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

The issue workspace should keep these regions visible together when terminal
space permits:

- the actual title and compact state, priority, identifier, and tag metadata;
- a multiline description/body editor; and
- an embedded subtask list.

Focus moves among editable regions without pushing a new view for each field.
Finite choices such as a state picker, Help, or destructive confirmation may
still use bounded overlays or child views. The workspace must adapt to the
20-row default, scroll long content, and give the body more room when no
subtasks exist.

The subtask region shows all descendant tasks in source order, indented by Org
depth, with its own highlight and scrolling. State and priority commands apply
to the highlighted subtask while that region has focus. `Enter` may navigate
to the child's issue workspace; Back returns to the parent with its prior
selection intact. This is navigation between issues, not a substitute for
showing the parent and its subtasks together.

### Editing and Safety

All in-app mutations continue through `OrgBuffer`: edits remain buffered,
auto-save recovery remains available, and the source file changes only after
the existing save flow. Body mutation needs a pure, surgical helper with tests
covering drawers, planning lines, blank lines, nested tasks, sibling tasks, and
unrelated prose. Title editing from `t0014` remains useful groundwork, but its
current `TEXT` action row is transitional UI.

### Architecture Direction

Build an application-specific `TaskWorkspaceView` and controller in the
ortask codebase on top of `InlineMenuSession`. Reuse the existing focused input
and prompt_toolkit controls, extending the one bounded `Application` to host
multiple focusable regions and a multiline text area. Do not start a second
prompt_toolkit application for editing.

This is proto-Handrail work, but extracting a shared package is premature.
Keep reusable seams clear and compare them with inedit; extract only after a
second adopter exposes stable shared behavior or duplicated bugs.

### Implementation Order

1. Finish abnormal terminal cleanup (`t0009.6.2`), remove obsolete selectors
   (`t0011`), and diagnose the standalone Escape delay (`t0013.1`).
2. Define and test the task-own-body boundary and surgical mutation helper.
3. Replace the action-row detail menu with a persistent issue-workspace shell.
4. Add multiline body editing within the existing buffer and save contract.
5. Complete direct state and priority actions in both list and workspace views.
6. Add the hierarchical, independently scrollable subtask region.
7. Add tag editing and terminal, recovery, resize, and performance coverage.

### Completion Criteria

- Title, own-body text, state, priority, and tags can be edited without an
  external editor.
- The selected issue and its subtasks can be read and acted on in one bounded
  workspace, including with long bodies and subtask lists.
- Action-like pseudo-rows are gone; external editing is an optional `e`
  command.
- Save, discard, recovery, terminal cleanup, and source-structure preservation
  retain their current safety guarantees.

Comments/activity feeds, attachments, custom workflow fields, and general Org
tree restructuring are outside the first workspace milestone. They can be
considered after the core issue-editing model proves useful.
