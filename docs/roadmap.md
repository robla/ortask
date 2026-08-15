# Interactive UI Roadmap

## Goal

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
final outcome policy and PTY cleanup coverage remain.

## Original Cause

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
replaces the rows and then restores them in place. The rest of the interactive
workflow should use that model.

## Bounded Inline Contract

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
- A controlled final exit may retain one final frame or concise summary. It
  must not retain a historical copy of every visited view. Failures should
  favor erasing a possibly misleading partial frame after restoring terminal
  modes.

Twenty rows is a default, not a feature limit. Search, scrolling, nested views,
contextual commands, editing, and Help should remain available within the
bounded viewport. A later `--height` option or environment setting can be
added if a real workflow needs it; configuration is not required for the first
migration.

## Proposed Application Shape

The implementation now provides a session-oriented API alongside the one-shot
selector:

- `menu.InlineMenuSession` owns requested/effective height, the active view
  stack, transient messages, movement, Help, terminal resize, external-command
  suspension, and the one call to `Application.run()`.
- `menu.MenuView` describes a title, summary, rows, selected index, available
  commands, optional detail text, and callbacks for actions and resume.
- `projtui.InteractiveTaskController` constructs task-specific views and owns
  Org parsing, `OrgBuffer` edits, stable task selection, and task actions.

The layout can follow inedit's proven structure: an `HSplit` with dynamic
header, one body window, and a fixed footer, all constrained by a callable
height. A `DynamicContainer` or equivalent state-driven control should swap
the body when a view needs a different focusable control. Ordinary menu views
can share one `FormattedTextControl`; text entry and confirmation views may use
a focused `BufferControl` or `TextArea` without starting another application.

Command metadata should remain the source for bindings, footer hints, and
`Ctrl-G` Help. The current `MenuAction` is a useful starting point, but the
session needs context-sensitive availability and handlers that transition
state rather than return to an outer Python loop.

## Migration Stages

### 1. Characterize the terminal contract

**Partially implemented.** Pipe-input tests now cover task/subtask/Help/picker
transitions in one application, bounded scrolling, effective-height clamping,
buffered edits, terminal handoff, and the absence of common alternate-screen
entry sequences in captured VT output. Real PTY cleanup and next-prompt tests
remain.

Add prompt_toolkit pipe-input tests and PTY-level tests before changing the
lifecycle. Cover a long task list, task-detail entry and return, Help, a nested
picker, and final exit. Tests should distinguish repaint control sequences from
new retained frames, verify that no alternate-screen entry sequence is emitted,
and confirm that the cursor and next shell prompt end below the application.

### 2. Add the persistent bounded shell

**Implemented.** `InlineMenuSession` supplies the 20-row dynamic
header/body/footer layout and resize clamping for local task sessions and the
registry-scoped project/task stack. Compatibility one-shot selectors remain
available but are no longer used by production interactive paths.

Build a single 20-row application containing a dynamic header, scrolling body,
and command footer. Port task-list navigation first while keeping selection
anchored by task ID. Add `before_render` resize handling so shrinking the
terminal adjusts the effective height without replacing the application.

The existing `select_menu()` API remains for compatibility and focused legacy
tests, but production interactive paths no longer use repeated one-shot
applications. The obsolete API is now eligible for separate cleanup.

### 3. Move contexts onto a view stack

**Implemented.** Local task details, nested subtasks, Help, task confirmation,
priority selection, and the `orgmgr.py -i` project list now push, pop, or
replace `MenuView` instances without ending the application. Returning from a
task context refreshes the project list and restores selection by project name.

Represent task details, subtasks, project selection, priority selection, and
Help as push/pop transitions. Replace recursive `focus_menu()` calls and outer
`while` loops with controller transitions. Each view should preserve its
stable selection key so returning to a parent restores the prior highlight and
scroll position.

Help can be either a modal flag over the current view or an explicit stack
entry, but it must use the same command metadata and close back to the exact
selection. A picker cancellation must similarly restore the parent without
changing data.

### 4. Bring prompts inside the application

**Implemented for interactive task sessions.** Leaving a dirty task session
pushes a bounded Save/Discard/Continue Editing view. Entering with distinct
auto-save data starts with a bounded Keep/Recover/Discard view, with Keep as the
safe default for both Enter and Back. Outcomes appear in the footer, and
canceling an exit returns to the live task list with edits intact. The numbered
fallback deliberately retains its plain prompts.

Keep the existing `OrgBuffer` safety contract: navigation is read-only, edits
remain buffered and mirrored to the auto-save file, and the real Org file is
written only through an explicit save path.

### 5. Suspend for the external editor

**Implemented for the task session.** `InlineMenuSession.suspend()` uses
prompt_toolkit terminal handoff, and the task controller reloads and replaces
the active view after `_open_editor()` returns.

Replace direct `subprocess.run()` from a completed selector with
prompt_toolkit's `run_in_terminal()` or an equivalent suspend/resume helper.
Before suspension, resolve pending buffered edits according to an explicit
policy. After the editor returns, reload the file, reparse tasks, restore the
closest stable selection, and repaint the bounded application. Editor launch
errors should become status messages rather than raw output interleaved with
the UI.

### 6. Finish lifecycle and final display

Decide and test the controlled-exit frame. The inedit precedent is to retain
one final bounded frame with factual outcome text while erasing on abnormal
failure. Ortask should at minimum report whether changes were saved,
discarded, or left unchanged. Terminal restoration, signal handling, resize
failure, and exception cleanup need PTY coverage before the old loop is
removed.

## Relationship to Handrail

This work should establish the bounded-inline behavior before ortask depends on
a new shared library. The reusable concepts are the persistent inline shell,
viewport policy, command metadata and Help, view-stack transitions, stable-row
selection, suspend/resume, and terminal test harness. Org parsing, task
mutation, project discovery, and `OrgBuffer` remain ortask responsibilities.

If inedit and ortask converge on the same semantics after this migration, those
proven pieces become candidates for a future common package. Avoid designing a
backend-neutral SDK first and then forcing both applications through it.

## Risks Observed So Far

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

## Completion Criteria

The roadmap is complete when:

- `ort -i` and `orgm -i` each run one prompt_toolkit application per session;
- the live interface stays within its effective 20-row region through lists,
  details, Help, pickers, prompts, and back navigation;
- returning from task details does not append another full task list;
- long lists scroll without losing the selected row;
- external editor handoff resumes the same session cleanly;
- save, discard, cancellation, and recovery remain safe and visible;
- non-TTY numbered behavior remains usable; and
- pipe-input and PTY tests defend repainting, resize, terminal restoration, and
  absence of alternate-screen switching.
