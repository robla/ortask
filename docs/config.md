# Configuration

Everything the suite reads at startup, and where each piece of it lives.

The organizing rule: **there is one machine-global location, the registry, plus
one opt-in boolean for the derived activity log.** Everything else is a file in
the tree, found by looking where you already are. A tool whose behavior depends
on hidden state is hard to reason about from a shell prompt, so the registry
remains the seed from which paths and project settings are discovered.

See `docs/projects.md` for what a registry *is*, `docs/cdproj.md` for the
directory stack, and `docs/format.md` for the Org conventions themselves.

## What is configured where

| Setting | Lives in | Read by | Shared or machine-local |
|---|---|---|---|
| Where the registry is | `~/.config/ortask/ortask.ini` | both tools | machine-local |
| Whether event logging is enabled | `[log] enabled` in `ortask.ini` | write commands | machine-local |
| Which projects exist | symlinks in registry subdirectories | `projmgr.py` | the registry's own repo, if it has one |
| Which task file a project uses | the entry's `.org` symlink, else discovery | both tools | same |
| A project's shared directory stack | `* Directories` in its task file | `projmgr.py cdproj` | the project's own repo |
| A project's private directory stack | the registry index file (see below) | `projmgr.py cdproj` | never the project's repo |
| Which file `ort` acts on | `--file`, `$ORTASK_FILE`, or an upward walk | `ortask.py` | n/a |

Read the table top to bottom and the scope narrows: one global file, then one
directory of pointers, then per-project settings. Nothing in the lower rows can
be set from the upper ones.

## `ortask.ini`

The only global configuration file. It honors `$XDG_CONFIG_HOME`, defaulting to
`~/.config/ortask/ortask.ini`. The registry is required only when overriding
the default; logging is an optional second section:

```ini
[projects]
registry = ~/Projects

[log]
enabled = true
```

Resolution order for the registry is `--registry` on the command line, then
`[projects] registry`, then the built-in default `~/Projects`. A `~` the user
typed is stored as typed rather than expanded, so the file stays portable
between machines and readable by a human.

`~/Projects` is the intended standard location, including for this project's
author, whose registry currently lives at `~/tmpsorta/proj2026` for historical
reasons. Documentation should use `~/Projects` in examples so that the default
and the convention are the same thing.

`projmgr.py init` writes the file (`--dry-run` to preview, `--force` to
overwrite an existing value). Writing goes through the same atomic replace as
Org edits, so an interrupted write cannot leave a half-file. Hand-editing is
equally supported — it is an ini file, and nothing caches it.

Absent `[log] enabled` means false. The log location is not another setting: it
is `<registry>/log`, with `ORTASK_LOG_DIR` available for tests and one-off
overrides. `projmgr.py init` updates `[projects] registry` while preserving the
`[log]` section. Future settings with sensible per-project answers still belong
in Org where the user can see them.

## Environment variables

| Variable | Effect |
|---|---|
| `ORTASK_FILE` | The task file `ortask.py` acts on. Beats the upward walk; loses to `--file`. |
| `ORTASK_LOG` | `on` or `off`; overrides `[log] enabled`. |
| `ORTASK_LOG_DIR` | Override `<registry>/log` for event reads and writes. |
| `XDG_CONFIG_HOME` | Relocates `ortask.ini` (to `$XDG_CONFIG_HOME/ortask/`). |
| `ORTASK_PROJMGR` | Path to `projmgr.py` for `misc/cdproj.func.sh` and the completion script, when it is not on `PATH`. Shell-side only; no Python reads it. |
| `VISUAL`, `EDITOR` | Which editor `ortask.py open` launches, `VISUAL` first. |

Note the asymmetry: there is an `ORTASK_FILE` but no `ORTASK_REGISTRY`. The
registry is settable only by `--registry` or `ortask.ini`. Nothing depends on
that gap; it simply has not been needed.

## Task-file resolution

`ortask.py` finds its file by walking up from the current directory. In one
directory, the first of these that exists wins:

1. `tasks.org`
2. `task.org`
3. exactly one `*.task.org`
4. `TODO.org`
5. exactly one other `TODO*.org`
6. `todo.org`

Ambiguity within a tier is an error, never an alphabetical guess. The whole
ladder is tried in the current directory before moving to its parent, so a
nested project never inherits its parent's file by accident. If the walk finds
nothing, exactly one generic `*.org` in the *original* directory is accepted as
a last resort.

`projmgr.py` uses the same ladder in two stricter forms: `discover_org_file`
probes a single directory without walking up (so `add` cannot register a
parent's file), and `preferred_task_file_in` additionally refuses the generic
`*.org` fallback, which makes it usable as the question "does this directory
look like a project root?"

## Per-project configuration

A project's configuration lives in Org, next to the thing it describes. There
are two sources, and the split is by *audience*, not by content:

- **Shared** — a `* Directories` section in the project's own task file. It is
  committed with the project and applies to everyone who clones it.
- **Private** — machine-local paths that would be noise or leakage in a shared
  repo. These live in the registry, because the registry is the only place that
  is neither the project's repo nor a global config file.

When both exist the private list wins outright and sets the order, and any entry
present only in the shared list is reported as a warning rather than merged in.
The full rule is in `docs/cdproj.md`. **That rule is unaffected by anything
below** — what follows changes only where the private side is stored.

An Org heading was chosen over an ini section for a reason worth restating: the
file a user already opens to read their tasks is the file that should carry the
project's settings. A second syntax would mean a second thing to learn and a
second thing to keep valid.

### What "private" means here

It means *not in the project's own repository*. It does not mean secret, and it
does not mean untracked.

Whether private config is then version-controlled is the registry owner's
business. A registry that is itself a git repo may legitimately track it, as the
current one does — that is private-to-the-project-but-shared-across-my-machines
data, which is a coherent thing to want. The tools never run git and never
require the registry to be a repository.

## The registry index file

**Status: implemented (`t0026.1`–`t0026.3`).** The registry index supersedes
the per-entry `directories-private.org` scheme; there is no fallback between
the two models.

Private per-project config no longer lives in one file per registry entry
(`<entry>/directories-private.org`). It lives in **one Org file at the registry
root**. The symlinks do not change: they remain the pointers, because that is
what they are, and the outward-symlink marker rule still decides what is a
project. Only the private config is centralized.

This is worth being explicit about, because an earlier draft of this document
argued against centralizing. That argument was against moving the *pointers*
into a document — which would have forced `projmgr.py` to become an Org editor
just to run `rm`, and would have put a file and a set of symlinks in a position
to disagree about what exists. Centralizing only the private config raises
neither problem: `pmgr rm` is still `rm -r entry`, and the index never decides
what a project is, only what settings a project has.

What centralizing buys is the thing per-entry files could not: one document you
can read top to bottom, that has room for prose about *why* a project is
registered and what state it is in. That is the literate part, and it only works
if there is one file.

### What it is called

**`projects.org`**, at the registry root — so, `~/Projects/projects.org`.

It is the name a person would guess, it says what it is without a convention to
learn, and it cannot be confused with a project's own task file because it is
not one of the names in the resolution ladder.

On the possible clash: there is one, it is small, and it is arguably a feature.
`discover_projects` only ever looks at subdirectories, so a file at the registry
root is invisible to project discovery — no clash there. But standing *in* the
registry and running `ort` does pick it up, via the "exactly one generic `*.org`
in the current directory" fallback. Verified:

```console
$ cd ~/Projects && ort
# operates on ~/Projects/projects.org
```

That is a reasonable thing to happen. It means the index can carry its own
`* Tasks` section for cross-project and registry-level work, and `ort` finds it
by standing in the right place. The caveat: that fallback requires *exactly one*
`.org` file there, so adding a second one at the registry root turns it into an
ambiguity error. Anything that wants to be a sibling should be a subdirectory.

### Shape

At most one top-level heading per project, whose text is the registry entry
name. Projects with no private settings or index prose may be absent:

```org
#+TITLE: Projects

* elusync
  Sync tooling for Electorama. Registered while working out the
  electowiki export path.
** Directories
   - ~/Projects/elusync
   - ~/src/elusync

* ortask
** Directories
   - ~/src/ortask
   - ~/src/ortask/docs
```

Notes on the shape:

- **Directory entries may be list items, subheadings, bare paths, or `file:`
  links.** `orglib.syntax._strip_directory_entry` already accepts all four, so the
  central file needs no new entry syntax. List items read better when nested
  under a project heading, and are used in the examples above.
- **Only a direct child `** Directories` heading is configuration.** A deeper
  heading with the same title is ordinary prose structure. This keeps the
  parser's boundary obvious and prevents a note from becoming configuration by
  accident.
- **A direct child `:PROPERTIES:` drawer holds per-project metadata.**
  `DESCRIPTION` and `TASK_FILE` are specified in `docs/ptui.md`; `TASK_FILE` is
  a non-normative mirror, and no command may resolve a task file from it. Other
  properties are preserved and ignored. A project heading may also carry an Org
  priority cookie, `* [#A] ortask`, which `ptui` uses for ordering.
- **Other prose under a project heading is free text** and is never parsed.
  That is the point of the file.
- **The project name is the join key.** Matching should be case-insensitive, to
  agree with the case-insensitive ordering `discover_projects` already uses.
  Compare the heading text with any leading priority cookie **and** any
  trailing `:tags:` stripped (`orglib.syntax._project_title`), so `* ortask`,
  `* [#A] ortask`, and `* [#A] ortask :work:` all name the same project. One
  file carrying two of those forms is ambiguous and is reported rather than
  resolved.
- **`* Tasks` is reserved**, and so is `* Template`. They are the index's own
  task section, the thing that makes standing in the registry and running `ort`
  useful. A registry entry named `Tasks` would collide; `doctor` should say so
  rather than the reader silently taking one for the other.
- **A tool that writes the file writes list items** — `   - ~/src/ortask` — and
  writes `~` for paths under `$HOME`. Reading accepts all four entry forms;
  writing picks one. See "Saving the directory stack" in `docs/cdproj.md`.

### What changes in the code

The read side landed in `t0026.1`. `orglib.syntax.parse_directories()` retains
its top-level behavior for project task files, while
`parse_project_directories()` first locates the unique top-level project
heading and then its unique direct-child `** Directories`. The public
`orglib.parse(text).directories(project)` result distinguishes a missing
project, a missing section, and an empty section, and carries exact source spans
for both the project and directory subtrees. Migration validation and current
private-stack consumers use those source-backed lookups.

Three consequences worth deciding deliberately:

1. **A project in the registry but not in the index** has no private stack. It
   falls back to the project's own `* Directories`, then to the project root.
   Silent and normal — the same as having no private file today.
2. **A project in the index but not in the registry** is a stale section.
   Deleting an entry no longer deletes its private config with it, so `pmgr
   doctor` reports the stale section.
3. **Duplicate headings for one project** should be a reported error, not a
   silent first-wins.

`pmgr doctor` performs the checks that go with those: a section matching no
registry entry, duplicate sections for one entry, duplicate direct-child
`Directories` sections, an index that cannot be read or parsed, and a
`directories-private.org` left behind after the index was created.

The index is also the first Org file `projmgr.py` edits in place rather than
creates. Writing one project's `Directories` subtree while leaving every other
byte alone is the same bounded-region problem `t0028` and `t0029` describe for
task editing, at a smaller scale, and it is worth doing through `orglib` rather
than beside it.

### Migration gate

The existence of `<registry>/projects.org` marks the registry as migrated.
Commands that consume or edit private directory settings require that marker
and never fall back to `directories-private.org`:

- `cdproj`, its picker/editor path, and `set-dirs` stop with
  `registry not migrated; run pmgr migrate` when the index is absent.
- A leftover legacy file beside an index is an incomplete migration, not a
  second source. Those commands stop and direct the user back to `pmgr migrate`.
- `pmgr doctor` remains usable and reports either state as a problem, exiting 2.
- `list`, `add`, `rm`, and `init` continue to work because they do not consume
  private directory settings.

A registered project need not have a section in a migrated index. That means it
has no private list and falls back to its project list, then its project root.
It is distinct from an empty direct-child `Directories` section, which is an
explicit empty private list.

### `pmgr migrate`

Migration is one-time, explicit, and data-preserving. `pmgr migrate [--dry-run]`
performs these phases in order:

1. Read and validate every `<entry>/directories-private.org` before changing
   anything. Require exactly one top-level `* Directories` subtree. Reject
   duplicate project names and content that cannot be nested safely. The first
   implementation conservatively rejects every `#+KEYWORD:` line because its
   scope may escape the project subtree; name every offending file.
2. Build one project section per legacy file. Wrap the legacy document in a
   top-level project heading and demote each of its headings one level, so its
   comments, prose, entry spelling, and unrelated subtrees survive. The old
   `* Directories` becomes the required direct child `** Directories`.
3. Write `projects.org` atomically, then remove the legacy files. With no legacy
   files, write an otherwise empty index so the migration marker still exists.

`--dry-run` prints the proposed index and the files that would be removed. If a
process stops after the atomic index write but before cleanup, rerunning
`migrate` removes a legacy file only when the corresponding index section is
exactly what the deterministic nesting transformation would generate. A
mismatch or a missing section is a hard error for manual resolution. This
resumability avoids pretending several file removals can be one atomic
filesystem transaction.

If a structurally valid `projects.org` exists with no legacy files, migration
is already complete and the command succeeds without changing it. Only
`pmgr migrate` reads legacy private files after the cutover.

### Reserved names

- `projects.org` at the registry root — the index.
- `log/` at the registry root — monthly disposable JSON Lines activity files.
- `#projects.org#` at the registry root — the Emacs-style auto-save sibling
  `ptui` writes while it holds the index open (`docs/ptui.md`). Disposable, and
  another line for a registry that ignores generated files.
- `*-private.org` — the superseded per-entry scheme (`docs/projects.md`).
  Retained as a reserved suffix so a converted registry can still exclude any
  such leftovers with one gitignore line.

## Org library assessment

The evaluation of external Python Org-mode libraries versus `ortasklib`'s bespoke surgical editing model is detailed in `docs/orglib.md`.

In short: `ortask` avoids full AST round-tripping to preserve byte-exact file fidelity, maintains zero runtime dependencies (stdlib-only), and considers lightweight testing integration (such as optional verification with `orgparse`).

## Known gaps

- **`*-private.org` is reserved but not enforced.** `docs/projects.md` states
  that private files are never candidates for task-file resolution, but
  `manager.choose_org_file` excludes only the exact name
  `directories-private.org`. A file named `notes-private.org` in a registry
  entry is currently selected as that project's task file. Verified 2026-08-20.
  The registry index makes this less pressing, since per-entry files go away.
- **`CLAUDE.md` and `GEMINI.md` describe the resolution ladder wrongly.** Both
  treat `tasks.org` as a legacy fallback tried last; it is in fact the *first*
  name probed. The ladder in this document is the verified one.
- **The project has no `LICENSE` file**, which needs settling before any
  dependency's license can be assessed against it.
