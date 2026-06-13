# orgmgr.py

`orgmgr.py` is a proposed manager for Org files across the user's filesystem.
It is distinct from `ortask.py`.

The `ortask.py` CLI defaults to local behavior.  It reads or edits one
selected Org task file in the current working directory. Its verbs
such as `list`, `show`, `add`, `done`, and `open` operate against a
specific file resolved by `--file`, `ORTASK_FILE`, or local file
lookup.

`orgmgr.py` is the global operations command for the ortask suite of
tools.  orgmgr.py knows about many Org files, groups them into
projects, and can build or maintain an index of the Org files the user
wants managed. It should eventually support project-level verbs such
as `projadd` and `projrm`.

## First Verb: list

`orgmgr.py list` should list all known projects and the top-level tasks within
each project.

```sh
orgmgr.py list
orgmgr.py list --projdir ~/tmpsorta/proj2026
orgmgr.py list --all
orgmgr.py list --format plain
orgmgr.py list --format json
```

The first implementation should use the same project list as `projtui.py`.
That means `--projdir` overrides configuration, the global config can point to
`~/tmpsorta/proj2026`, and the default is `~/Projects`.

## Project Discovery

Reuse `projtui.py` discovery rules:

- scan immediate subdirectories of the configured project directory
- skip hidden directories and infrastructure directories such as `.git`,
  `docs`, and `__pycache__`
- choose each project's Org file using the same naming order as `projtui.py`
- do not recurse arbitrarily through project trees

For directory-based discovery, `orgmgr.py list` still reuses `projtui.py`'s
`projdir` rules during the transition. The durable source of projects is the
shared registry written by `projadd` (see *Verb: projadd* and *Shared Project
Registry* below). Longer term, `orgmgr.py` and `projtui.py` refer only to that
registry.

## Task Selection

For each discovered project, show only top-level tasks:

- parse the selected Org file with the same parser as `ortask.py`
- include only `TODO` tasks by default
- include only direct children of `* Tasks` (`level == 2`)
- omit subtasks from the main list output
- with `--all`, include top-level `DONE` tasks too

If a project has an Org file but no parseable `* Tasks` section, show the
project with a short note in plain output and include a warning field in JSON.

## Plain Output

Suggested default:

```text
elweek  elweek/TODO-ElWeek.org
  [TODO] tw26W24 Week of June 8's tasks for ElectoramaWeekly

ortask  ortask/todo.org
  [TODO] t0001 Remove AI slop from docs/taskwarrior.md
  [TODO] t0002 create .org file if none exist in directory when using 'ort add'
```

Use one blank line between projects. Keep task IDs visible.

## Verb: projadd

`orgmgr.py projadd` registers a project in the shared project registry so it
appears in `orgmgr.py list` and in `projtui.py` without the user editing config
by hand. It is the intended way to grow the global project list.

`projadd` refuses to run until the shared registry has been initialized by
`orgmgr.py migrate` (see *Verb: migrate*). If the shared `ortask.ini` registry
does not yet exist, `projadd` prints a friendly error and exits non-zero without
creating any config:

```text
orgmgr.py projadd: project registry not set up yet.
Run `orgmgr.py migrate` once to create ~/.config/ortask/ortask.ini
(migrating any existing projtui.py `projdir` config), then re-run projadd.
```

This makes moving off the deprecated `projdir` config a deliberate, one-time
step rather than something `projadd` does silently.

```sh
orgmgr.py projadd                         # register the current directory
orgmgr.py projadd ~/src/ortask
orgmgr.py projadd ~/src/ortask --name ortask
orgmgr.py projadd ./elweek --file TODO-ElWeek.org
orgmgr.py projadd ~/src/ortask --dry-run
```

### Behavior

1. **Resolve the project directory.** The optional positional `PATH` defaults to
   the current working directory. If `PATH` is a directory, it is the project
   root. If `PATH` points directly at an Org file, its parent directory is the
   project root and that file is used as the task file (skipping discovery).
2. **Discover the task file.** Within the project root, run the *same* task-file
   discovery that `ortask.py` uses — `resolve_org_file()` evaluated with the
   project root as the working directory. An explicit `--file` wins; otherwise
   probe the well-known names in order (`TODO*.org` preferred per
   `docs/format.md`, then `todo.org`, `tasks.org`, then the first `*.org`
   alphabetically, warning if several match). This guarantees `projadd`,
   `ortask.py`, and `projtui.py` all agree on which file holds a project's
   `* Tasks` subtree.
3. **Derive the project name.** Use `--name` if given; otherwise the basename of
   the project root (e.g. `elweek`, `ortask`).
4. **Validate before writing.** The discovered file must exist, parse, and
   contain a `* Tasks` section. If no task file is found, or the file has no
   parseable tasks, `projadd` prints an error and exits non-zero without
   touching config. Like `list`, `projadd` never creates the Org file, adds IDs,
   or runs `repair`.
5. **Write the registry entry.** Add the project to the shared registry (see
   *Shared Project Registry*) as `name = path`. Paths are stored as given
   (tilde-preserved when the user passed `~`). The resolved task file is reported
   to the user, and is stored explicitly when discovery was ambiguous or when
   `--file` was used, so the tools never have to re-guess.

### Options

- `--name NAME` — registry key to use instead of the directory basename.
- `--file FILE` — use this Org file directly instead of running discovery; the
  stored entry points at the file.
- `--force` — overwrite an existing entry with the same name (otherwise a name
  collision is an error).
- `--dry-run` — print the entry that would be written and the resolved task
  file, but do not modify config. Exit 0.

### Conflicts

- A name that already exists is an error unless `--force` is given.
- If the same path is already registered under a different name, warn but
  proceed (a project may legitimately be reachable by more than one name during
  migration).

## Verb: migrate

`orgmgr.py migrate` performs the one-time transition from the deprecated
`projtui.py` `projdir` config to the shared `[projects]` registry. It is a
prerequisite for `projadd`: until `migrate` has created the shared `ortask.ini`,
`projadd` refuses to run. Running `migrate` is what establishes the registry,
whether or not there is an old `projdir` to import.

```sh
orgmgr.py migrate                          # import projdir (if any), else init empty registry
orgmgr.py migrate --projdir ~/tmpsorta/proj2026
orgmgr.py migrate --dry-run
```

### Behavior

1. **Bail out if already migrated.** If `ortask.ini` already contains a
   `[projects]` section, report that migration has already happened and exit 0
   without changes (idempotent). `--force` re-runs and merges newly discovered
   projects into the existing registry.
2. **Locate the source.** Read `projdir` from `~/.config/ortask/projtui.ini`
   (honoring `$XDG_CONFIG_HOME`), unless overridden by `--projdir`. If neither a
   `projtui.ini` `projdir` nor `--projdir` is available there is nothing to
   import; `migrate` still proceeds to step 4 to establish an empty registry.
3. **Discover projects.** For each immediate subdirectory of `projdir`, run the
   same `ortask.py` task-file discovery used by `projadd` and `list`. Record an
   entry for every subdirectory that resolves to a file with a parseable
   `* Tasks` section. Skip hidden and infrastructure directories (`.git`,
   `docs`, `__pycache__`), reporting skipped entries only under a verbose flag.
4. **Write the shared registry.** Create `~/.config/ortask/ortask.ini` with a
   `[projects]` section holding the discovered entries (empty if there was
   nothing to import), using the same atomic temp-file-then-rename strategy as
   other config writes. Only config is written; no Org file is touched.
5. **Leave projtui.ini in place.** `migrate` does not delete the old config. It
   prints a summary of what was imported and a note that `[projtui] projdir` is
   now deprecated in favor of the registry.

### Options

- `--projdir DIR` — source workspace directory to import, overriding
  `projtui.ini`.
- `--force` — re-run even when a `[projects]` registry already exists, merging
  in any newly discovered projects. Existing entries are preserved; on a name
  collision the existing entry wins and a warning is printed.
- `--dry-run` — print the registry that would be written without modifying any
  config. Exit 0.

### Safety

`migrate` is read-only with respect to Org content (it creates and edits no Org
file) and never deletes `projtui.ini`. It only creates or extends the shared
config registry, atomically.

## Shared Project Registry

`projadd` writes to a shared, suite-wide config rather than a projtui-specific
one, because the registry is meant to be read by every ortask tool (`orgmgr.py`
and `projtui.py` today, more later).

Location, honoring `$XDG_CONFIG_HOME`:

```text
$XDG_CONFIG_HOME/ortask/ortask.ini      # when XDG_CONFIG_HOME is set
~/.config/ortask/ortask.ini             # otherwise
```

The registry is a `[projects]` section mapping each project name to a path:

```ini
[projects]
elweek = ~/tmpsorta/proj2026/elweek
ortask = ~/src/ortask
```

A registry value may be a directory (the tools run `ortask.py` discovery inside
it at read time, so renaming the task file within the project is picked up) or a
direct path to an Org file (used as-is). Config writes use the same atomic
temp-file-then-rename strategy as `ortask.py` edits, and only the config file is
ever modified — never any Org file.

## Deprecating projtui's projdir config

`projtui.py` currently keeps its own config at `~/.config/ortask/projtui.ini`:

```ini
[projtui]
projdir = ~/tmpsorta/proj2026
```

That `projdir` model is intentionally narrow: it points at a single workspace
directory and treats each immediate subdirectory as a project. It cannot express
a registry of individually chosen projects in different locations, which is what
`projadd` produces. It is therefore too projtui-specific to be the home of the
global project list.

Migration plan:

1. **Introduce** the shared `ortask.ini` `[projects]` registry described above
   as the primary source of projects for both `orgmgr.py` and `projtui.py`.
2. **Gate writes behind `migrate`.** The registry is created only by
   `orgmgr.py migrate` (see *Verb: migrate*), which imports any existing
   `projdir` projects. `projadd` refuses to run until the registry exists, so
   the move off `projdir` is an explicit, one-time choice rather than a silent
   side effect.
3. **Read both, registry wins.** During the transition, `projtui.py` reads the
   shared `[projects]` registry *and* still honors `[projtui] projdir` (and
   `--projdir`). Projects discovered under `projdir` are merged in; on a name
   collision an explicit `[projects]` entry wins.
4. **Warn.** When `projdir` is the only configured source (no registry yet),
   `projtui.py` prints a one-line deprecation note pointing at
   `orgmgr.py migrate`.
5. **Remove later.** A future major version drops `[projtui] projdir` support
   once the registry is the norm. `--projdir` may remain as an ad-hoc override
   for scanning an unregistered directory.

## Future Verbs

`projadd` is specified above. Other potential verbs:

- `projrm`: remove a project from the registry
- `scan`: refresh the registry from configured roots
- `doctor`: report missing files, duplicate IDs, parser failures, or stale
  registry entries

These verbs manage the global project registry and Org-file inventory. They must
not replace local task editing verbs in `ortask.py`.

## Safety

`orgmgr.py list` must be read-only with respect to Org content: it must not run
`repair`, create missing files, add IDs, or rewrite Org files. If duplicate IDs
are found within a project file, report that project as invalid and continue
listing other projects.

`orgmgr.py projadd` may write the shared config registry, but it is held to the
same Org-content safety rules: it never creates a task file, never edits Org
content, and refuses to register a project whose task file is missing or
unparseable. It also refuses to run until `orgmgr.py migrate` has initialized
the registry. Config writes are atomic.

`orgmgr.py migrate` likewise only creates or extends the shared config registry,
atomically. It creates and edits no Org file and never deletes `projtui.ini`.
