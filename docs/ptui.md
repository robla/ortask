# ptui Project Navigator

`ptui` is the interactive form of `projmgr.py -i`. Its purpose is not only to
open a registered project, but to help the user decide which project deserves
attention next. The registry remains the source of project membership;
`projects.org` supplies optional presentation and prioritization metadata.

This remains partly a forward specification. `t0035.1` parses priority,
description, and the task-file mirror; `t0035.2` displays the first two with
open-task count and adds priority/alphabetical sorting; `t0035.3` adds buffered
priority changes from the project list; `t0035.4` adds the `m` metadata
workspace; `t0035.5` completes the dashboard with Modified sorting and mirror
diagnostics. `t0037.1` polls for external index changes, `t0037.2` supplies the
pure section-level merge planner, and `t0037.3` rebases disjoint external
changes into the open buffer or presents a named conflict.

## Project List

The project list retains the bounded highlight-bar UI, location summary,
open-task count, and task-file navigation. It adds priority and a short
description without hiding the canonical project and task-file paths. A
representative row is:

```text
▶  1  PROJ  [A] ortask     4 open  Org-backed task and project tools
```

An unset priority is shown less prominently than `A`, `B`, or `C`. Broken
projects and warnings remain visible regardless of sorting or metadata.

## Sorting

The default order is **priority, then project name**:

1. `A`, `B`, `C`, then projects with no priority.
2. Case-insensitive project name within each priority group.

The user switches among these views without changing `projects.org`:

- **Priority**: the default order above.
- **Alphabetical**: case-insensitive project name only.
- **Modified**: canonical task-file modification time, newest first, then
  project name. Projects without a readable task file sort last.

`s` cycles the sort mode and reports the active mode in the footer;
contextual Help should list the modes that exist. Modified time is deliberately
a cheap, predictable proxy for activity: `ptui` must not recursively scan
project trees or invoke Git to calculate it. The selected project remains
anchored by name when the order changes.

Priority and Alphabetical were implemented by `t0035.2`; `t0035.5` adds
Modified to the same cycle. Help derives its description from the implemented
mode ring.

The sort implementation can invert a mode's primary axis without inverting its
tie-break or availability rule: an unreadable task file stays at the bottom of
a reversed Modified list, and project-name ties stay ascending. No key exposes
direction yet. Before one does, `t0039.3` must declare which sorts are
reversible and give their directions meaningful labels; a possible `v` screen
is deferred to `t0039.4` and is not required by the current quick toggles.

## Filtering

`C-t` cycles the project filter between every project and only those with open
tasks, reporting the active view in the same footer badge as the sort mode
(`open only · Priority sort`). The count is the top-level `TODO` count that
`projmgr.py list` shows, so the row, the filter, and that listing never
disagree.

The filter hides only what the navigator can prove is quiet. A project whose
task file is missing, broken, or unreadable stays visible: warnings survive
filtering, and a project that cannot be read is not a project with nothing left
to do. Selection stays anchored by project name when that project remains
visible. If the selected project is itself filtered out, selection falls back
to the nearest surviving row.

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
differs from the resolved file, `ptui` marks the project row and shows both
paths in the selected-project summary and metadata workspace. Relative mirrors
are compared from the real project directory after expanding `~` and
environment variables. An unset mirror is normal, and a repeated `TASK_FILE`
reports its existing ambiguity instead of a second mismatch. Refresh stages
the canonical path in the workspace; `C-s` saves it.

Other properties, prose, tags, and child sections are allowed and must survive
edits byte-for-byte outside the changed fields. `Directories` keeps its current
meaning and precedence.

## Metadata Mode

Pressing `m` on a project opens a compact metadata workspace showing:

- project name, read-only;
- priority, editable as `A`, `B`, `C`, or unset;
- short description, editable;
- resolved task-file path, read-only;
- recorded `TASK_FILE` mirror and a refresh action;
- the effective directory count and an editable custom directory stack;
- an action that opens `projects.org` at the project heading in `$VISUAL`.

Arrow keys, Tab, and Shift-Tab move among every field and action, including
text fields, without changing values. Enter puts the focused priority or text
field into edit mode; only then do Left/Right change priority or typing and
cursor keys edit text. Enter finishes a single-line field, while Enter inserts
a line in the directory field; Esc returns either field to navigation mode.
Buttons activate directly with Enter. `C-s` applies all changed fields as one
`OrgBuffer` transaction before saving the complete index. Esc from navigation
offers Save, Discard, and Continue when fields are dirty. The directory field
records one path per line and initially shows only the custom stack. Leaving a
missing custom stack blank creates no override; clearing an existing stack
saves an empty custom section, which selects the project root.

Shift-Up and Shift-Down on the project list raise or lower priority through
`unset -> C -> B -> A`. A changed priority may move the row in the default sort,
but the highlight follows the same project. The `m` workspace is the
discoverable path for deliberate metadata edits.

Project-list priority edits are buffered in one `projects.org` buffer. `C-/`
and `C-r` undo and redo them, and `C-s` atomically saves the exact buffer after
a source-preimage check. Leaving with edits presents Save, Discard, and
Continue Editing. The project-list footer and title report dirty state. An
absent project section is created when its priority is first set in an already
migrated index; editing never implicitly runs `pmgr migrate`.

The workspace's external-editor action first saves or rejects the current
buffer, opens the same project section, and reloads the result. `cdproj`'s
private-list editor uses the same `OrgBuffer` plus bounded directory writer,
so it also checks the loaded source revision before handing the file to
`$VISUAL`.

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

The two write mechanisms now share stale-source protection, while retaining
different transaction scopes:

| | `TextFileBuffer` via `taskui.OrgBuffer` | `manager.plan_/apply_registry_directories_update` |
|---|---|---|
| Buffering, undo/redo, dirty state | yes | no |
| Auto-save sibling | yes (`#projects.org#`) | no |
| Preimage check before writing | yes | yes |
| Scope of the write | exact buffered file built from bounded edits | one bounded section |

As of `t0035.3`, `ptui` holds one `taskui.OrgBuffer` adapter over
`projects.org` for the whole session. As of `t0038.1`, its transactional state
machine is the peer `textbuffer.TextFileBuffer`; the adapter supplies ortask's
writer, auto-save naming, and activity observer. Priority operations replace
only the matched heading line in that buffer. Metadata properties replace one
drawer line, and directory editing replaces one direct-child section; unrelated
source remains byte-identical. `pmgr set-dirs` calls the same in-memory
directory writer before its preimage-checked section update. `save()` rereads
the file and requires it to match the saved baseline exactly before atomically
writing the buffer.

The case to design against is concrete: `ptui` open in one terminal with a
dirty priority edit, `pmgr set-dirs` run in another, then `C-s`.

**What happens then:** polling or `C-s` plans a merge. A change to another
project section is absorbed into the in-memory buffer, while the local priority
edit remains dirty. A differing change to the same section opens a conflict
view naming that project and does not alter either version. The real index is
not written until an explicit successful save.

**Merging is an in-memory operation.** It updates the buffer, and may refresh
the auto-save sibling; it never writes `projects.org`, which still changes only
when the user saves. Merge on detection, ahead of any save, so `C-s` either
writes a buffer that already contains the other write or refuses with the
alert. Refusal remains correct for what a merge cannot handle.

### In-memory reconciliation contract

The merge has three exact-text inputs:

- **Base** is the `projects.org` text read at the last load, save, or successful
  reconciliation. In `TextFileBuffer` this is the saved disk baseline.
- **Ours** is the current in-memory buffer, including unsaved `ptui` edits.
- **Theirs** is a fresh read of `projects.org` after external change detection.

The project-specific planner belongs in `ortasklib.manager`, using the source
spans exposed by `orglib`; `TextFileBuffer` owns only file observation, buffer
state, undo, auto-save, and save orchestration. This keeps a future task-file
merger possible without teaching the generic buffer about Org or registry
structure.

As of `t0037.2`, `manager.plan_project_index_merge()` implements the pure
planner. It receives Base, Ours, and Theirs and returns an immutable
`ProjectIndexMergePlan`: either merged text with replayed-local and
absorbed-external project names, or typed, source-labeled conflicts. It does not
read or write files. Theirs is the output canvas. For every project section
changed in Ours, it replays the complete raw section when that section in
Theirs is still byte-for-byte Base. If Ours and Theirs made the same change, it
accepts it. If both changed the same section differently, it reports a conflict
rather than attempting a field-level merge.

Local preamble changes, project additions/deletions/reordering, and edits to
reserved sections are also conflicts because they are outside an existing,
uniquely addressed Base project section. External preamble changes,
reordering, additions, deletions of locally unchanged projects, and untouched
sections survive because the planner starts from Theirs. Replacements are
applied back-to-front and the complete result is parsed again before success.

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

As of `t0037.1`, file metadata is only an early-warning optimization. The UI
polls device, inode, size, and nanosecond mtime during its refresh cycle, then
reads and compares exact text when that signature changes. `C-s` always reads
and compares exact text again before its atomic write, including when the
signature appears unchanged. A clean buffer adopts changed disk text as its new
clean baseline and refreshes the project rows. A dirty buffer records Theirs
and, as of `t0037.3`, immediately invokes the section planner. Success makes
Theirs the saved baseline, collapses the replayed local work to one undo step,
and refreshes the auto-save without writing the real index. `C-s` performs a
second exact check before its atomic write; event logging therefore compares
Theirs with the saved merge rather than attributing absorbed changes to `ptui`.
Missing and unreadable files block save. Polling uses no watcher thread.

The conflict state offers reload/discard, continue editing, and retry.
It does not make force-overwrite a routine recovery action. `C-s` remains
blocked until the user reloads, edits or undoes the overlap and retries, or
reconciliation succeeds after another external change.

Two consequences, both decided:

- **The auto-save sibling stays.** The `OrgBuffer` adapter configures
  `TextFileBuffer` to write `#projects.org#` into the
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
1. Parse project priority and description, then add priority/alphabetical sort
   (`t0035.1`–`t0035.2`, done).
2. Add buffered priority changes from the project list (`t0035.3`, done).
3. Detect external index changes without weakening save safety (`t0037.1`,
   done).
4. Add the pure project-section merge planner (`t0037.2`, done).
5. Reconcile the open buffer and expose conflicts (`t0037.3`, done).
6. Isolate the format-neutral transactional buffer (`t0038.1`, done).
7. Add the `m` metadata workspace and bounded save path (`t0035.4`, done).
8. Add modified-time sorting and the task-file mirror diagnostics (`t0035.5`,
   done).
