# ptui Project Navigator

`ptui` is the interactive form of `projmgr.py -i`. Its purpose is not only to
open a registered project, but to help the user decide which project deserves
attention next. The registry remains the source of project membership;
`projects.org` will supply optional presentation and prioritization metadata.

This is a forward specification. Priority, description, and the task-file
mirror are parsed as of `t0035.1` — `orglib.parse(text).project(name)` and
`manager.read_project_metadata()` — but the project list is still alphabetical
and shows none of it, and nothing edits it yet.

## Project List

The project list should retain the current bounded highlight-bar UI, location
summary, open-task count, and task-file navigation. It should add priority and
a short description without hiding the canonical project and task-file paths.
A representative row is:

```text
▶  1  PROJ  [A] ortask     4 open  Org-backed task and project tools
```

An unset priority is shown less prominently than `A`, `B`, or `C`. Broken
projects and warnings remain visible regardless of sorting or metadata.

## Sorting

The default order is **priority, then project name**:

1. `A`, `B`, `C`, then projects with no priority.
2. Case-insensitive project name within each priority group.

The user can switch among these views without changing `projects.org`:

- **Priority**: the default order above.
- **Alphabetical**: case-insensitive project name only.
- **Modified**: canonical task-file modification time, newest first, then
  project name. Projects without a readable task file sort last.

`s` should cycle the sort mode and report the active mode in the footer;
contextual Help should list the modes that exist. Modified time is deliberately
a cheap, predictable proxy for activity: `ptui` must not recursively scan
project trees or invoke Git to calculate it. The selected project remains
anchored by name when the order changes.

Priority and Alphabetical arrive in `t0035.2`, Modified in `t0035.5`, so Help
lists what is actually available at each step rather than advertising a mode
that is not there yet.

## Metadata in `projects.org`

Each registered project may have one top-level section in the registry's
`projects.org`. Extend that section with Org-native priority and a property
drawer:

```org
* [#A] ortask
:PROPERTIES:
:DESCRIPTION: Org-backed task and project tools
:TASK_FILE: ~/src/ortask/todo.org
:END:
** Directories
   - ~/src/ortask
   - ~/src/ortask/docs
```

The registry entry name remains the join key. Readers must recognize both
`* ortask` and `* [#A] ortask`, stripping the priority cookie and trailing tags
before matching. Names are not editable in metadata mode because renaming the
heading alone would not rename the registry entry.

`orglib.syntax._project_title` strips both, as of `t0036`. It did not always:
until then a heading that gained a cookie stopped matching its registry entry,
and since the index became the only source of a project's private directory
stack (`t0026.3`), that silently cost the project its stack. The regression
tests for it are worth keeping wherever this matching moves.

`DESCRIPTION` is a single-line summary intended for the project list.
`TASK_FILE` is an optional, non-normative mirror for human inspection. The
symlink registry and normal task-file resolution remain authoritative; no
command may discover or open a task file from this property. When the mirror
differs from the resolved file, `ptui` should show the mismatch and offer to
refresh it rather than silently treating it as configuration.

Other properties, prose, tags, and child sections are allowed and must survive
edits byte-for-byte outside the changed fields. `Directories` keeps its current
meaning and precedence.

## Metadata Mode

Pressing `m` on a project opens a compact metadata workspace. It should show:

- project name, read-only;
- priority, editable as `A`, `B`, `C`, or unset;
- short description, editable;
- resolved task-file path, read-only;
- recorded `TASK_FILE` mirror and a refresh action;
- the effective directory count and an action for the existing directory-stack
  workflow.

Shift-Up and Shift-Down on the project list should provide the fast path for
raising or lowering priority through `unset -> C -> B -> A`. `m` is the
discoverable path for deliberate edits. A changed priority may move the row in
the default sort, but the highlight follows the same project.

Metadata edits are buffered. `C-s` atomically saves the affected bounded
project section; leaving a dirty workspace presents Save, Discard, and Cancel.
The project-list footer and title must visibly report dirty state. An absent
project section may be created in an already migrated index, but metadata
editing must not implicitly run `pmgr migrate`.

## Safety and Implementation Boundaries

Project discovery continues to come from registry entries, not
`projects.org`. Metadata parsing belongs in `orglib` as a source-backed project
region; registry policy and sort keys belong in `ortasklib.manager`; `projmgr.py`
assembles the views. Generic focus, field, and save/discard mechanics may live
in `ortasklib.menu`, but project-specific metadata must not.

Writes must use source-preimage checks and one atomic bounded rewrite. Duplicate
project headings, duplicate property drawers, malformed priority cookies, or a
changed source file stop the save with a recoverable error. Direct priority
changes and metadata-workspace edits should share one transaction path so undo,
dirty-state reporting, and save behavior cannot disagree.

### Write path

Two mechanisms exist, and each supplies half of what the paragraph above asks
for. The decisions below were settled 2026-08-22; the comparison is kept
because the gap it names is what the work has to close.

| | `taskui.OrgBuffer` | `manager.plan_/apply_registry_directories_update` |
|---|---|---|
| Buffering, undo/redo, dirty state | yes | no |
| Auto-save sibling | yes (`#projects.org#`) | no |
| Preimage check before writing | **no** | yes |
| Scope of the write | the whole file | one bounded section |

`OrgBuffer` takes any Org path, so it runs on `projects.org` unchanged, and its
undo/redo is what the metadata workspace wants. But `save()` calls
`core.atomic_write` on the entire buffer with no check that the file is still
the one that was read, so a `ptui` session holding a buffer open would overwrite
a `pmgr set-dirs` run from another terminal. The index path has the check and
the bounded rewrite, and no buffering at all.

The case to design against is concrete: `ptui` open in one terminal with a
dirty priority edit, `pmgr set-dirs` run in another, then `C-s`.

**What happens then:** the save refuses, with a recoverable error naming the
file that moved. It never discards the other write, and it never silently wins.
So `OrgBuffer` is the base — `ptui` holds one `projects.org` buffer open, which
is what makes undo across several edits work — and it gains the preimage check
the index write path already has.

Refusing is the floor rather than the goal. What the session should eventually
do is merge: two people editing different project sections of one file is not a
conflict in any meaningful sense, and the tool should say so by absorbing the
other write instead of refusing. That is `t0037`, in three stages: detect the
change, plan a pure section-level merge, then reconcile the open buffer and
alert only when reconciliation cannot succeed.

**Merging is an in-memory operation.** It updates the buffer, and may refresh
the auto-save sibling; it never writes `projects.org`, which still changes only
when the user saves. Merge on detection, ahead of any save, so `C-s` either
writes a buffer that already contains the other write or refuses with the
alert. Refusal remains correct for what a merge cannot handle.

### In-memory reconciliation contract

The merge has three exact-text inputs:

- **Base** is the `projects.org` text read at the last load, save, or successful
  reconciliation. In `OrgBuffer` this is the saved disk baseline.
- **Ours** is the current in-memory buffer, including unsaved `ptui` edits.
- **Theirs** is a fresh read of `projects.org` after external change detection.

The project-specific planner belongs in `ortasklib.manager`, using the source
spans exposed by `orglib`; `OrgBuffer` owns only file observation, buffer state,
undo, auto-save, and save orchestration. This keeps a future task-file merger
possible without teaching the generic buffer about registry structure.

The planner is pure: it receives Base, Ours, and Theirs and returns either
merged text plus the absorbed project names, or structured conflicts. It does
not read or write files. Theirs is the output canvas. For every project section
changed in Ours, replay the complete raw section when that section in Theirs is
still byte-for-byte Base. If Ours and Theirs made the same change, accept it. If
both changed the same section differently, report a conflict rather than
attempting a field-level merge. This first version also conflicts on local
changes outside a uniquely addressed existing project section. Starting from
Theirs preserves external changes to the preamble, project order, untouched
sections, and externally added or deleted projects.

After a successful dirty merge, Theirs becomes the new disk baseline and the
merged text remains the dirty buffer. This rebase is essential: `discard` must
return to Theirs, a later save must not reject the already absorbed write, and
the activity log must not claim that `ptui` made the external change. Snapshot
undo entries based on the old file are unsafe after rebasing, so the first
implementation collapses the local work into one transaction from Theirs to
the merged text and clears redo. Undo then removes the local work without
removing the external work.

The merge calculation and decision happen entirely in memory. Once accepted,
the normal auto-save path may copy the merged buffer to `#projects.org#` for
crash recovery; that file is neither a merge input nor a staging file. The real
`projects.org` changes only on explicit `C-s`.

File metadata is only an early-warning optimization. The UI may poll device,
inode, size, and nanosecond mtime during its existing refresh cycle, then read
and compare exact text when that signature changes. `C-s` must always read and
compare exact text again before its atomic write. A clean buffer adopts an
external change as clean. A dirty buffer attempts reconciliation. Missing,
unreadable, malformed, or conflicting input leaves the buffer, history,
auto-save, and real file unchanged and puts the UI into a persistent conflict
state.

The conflict state offers reload/discard, continue editing, and retry. It does
not make force-overwrite a routine recovery action. `C-s` remains blocked until
the user reloads, edits or undoes the overlap and retries, or reconciliation
succeeds after another external change.

Two consequences, both decided:

- **The auto-save sibling stays.** `OrgBuffer` writes `#projects.org#` into the
  registry root for crash recovery. It disturbs no discovery — its suffix is
  `.org#`, so neither `discover_projects` nor the single-generic-`.org`
  fallback sees it — and it is reserved in `docs/config.md` alongside `log/`,
  so a registry under version control can ignore it in one line. Crash recovery
  is worth more than a tidy `git status`.
- **One editor for the section.** The metadata workspace edits directories as
  well as priority and description, so it and `cdproj`'s `e` — which opens the
  index in `$VISUAL` at the project's section (`docs/cdproj.md`) — are two ways
  into one bounded region and must share one transaction path. The external
  editor route reads and writes through the same preimage check rather than
  around it.

### Why the priority cookie and not a property

`* [#A] ortask` is Org's own priority syntax, so Emacs sorts, filters, and
cycles it without being taught anything, and a person reading the index sees a
priority rather than a key-value pair. `:PRIORITY: A` in the drawer would have
avoided `t0036` entirely, which is the honest argument against the cookie: every
reader of the file has to strip it correctly, permanently. The file is one
people open in Emacs, so the cookie wins anyway.

## Suggested Delivery Order

0. Strip the priority cookie when matching project headings (`t0036`, done).
   Nothing below could write a cookie safely until it landed.
1. Parse project priority and description, then add priority/alphabetical sort.
2. Add buffered priority changes from the project list.
3. Add external-change detection, pure section merging, and buffer
   reconciliation (`t0037`).
4. Add the `m` metadata workspace and bounded save path.
5. Add modified-time sorting and the task-file mirror diagnostics.
