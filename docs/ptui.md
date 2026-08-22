# ptui Project Navigator

`ptui` is the interactive form of `projmgr.py -i`. Its purpose is not only to
open a registered project, but to help the user decide which project deserves
attention next. The registry remains the source of project membership;
`projects.org` will supply optional presentation and prioritization metadata.

This is a forward specification. The current project list is alphabetical and
does not yet read or edit project priority and description metadata.

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
contextual Help should list all modes. Modified time is deliberately a cheap,
predictable proxy for activity: `ptui` must not recursively scan project trees
or invoke Git to calculate it. The selected project remains anchored by name
when the order changes.

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

## Suggested Delivery Order

1. Parse project priority and description, then add priority/alphabetical sort.
2. Add buffered priority changes from the project list.
3. Add the `m` metadata workspace and bounded save path.
4. Add modified-time sorting and the task-file mirror diagnostics.
