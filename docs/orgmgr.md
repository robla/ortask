# orgmgr.py

`orgmgr.py` is a manager for Org files across the user's filesystem. It is
distinct from `ortask.py`.

The `ortask.py` CLI defaults to local behavior.  It reads or edits one
selected Org task file in the current working directory. Its verbs
such as `list`, `show`, `add`, `done`, and `open` operate against a
specific file resolved by `--file`, `ORTASK_FILE`, or local file
lookup.

`orgmgr.py` is the global operations command for the ortask suite of
tools.  It groups projects together through a single **master project
directory** (the "projdir"): one subdirectory per project, each holding
symlinks to the project and, optionally, its task file. It should
eventually support project-level verbs such as `projadd` and `projrm`.

**Status:** `list`, `migrate`, and `projadd` are implemented per this document.
`ortask.ini` records the master projdir; `migrate` adopts that path from the
legacy `projtui.ini` and then deletes it; `projadd` creates a projdir
subdirectory of symlinks. `projrm` and the other future verbs are not yet
implemented.

## The master project directory

The registry of projects is a real directory on disk — the **projdir** — not a
list inside a config file. `ortask.ini` records only *where* the projdir is;
the projects themselves are its subdirectories. Keeping the registry on the
filesystem is deliberate: you can inspect and edit it directly with bash
(`ls`, `cd`, `readlink`, `cat */*.org`), and `orgmgr.py` is a convenience layer
over that directory rather than its owner.

### ortask.ini

Location, honoring `$XDG_CONFIG_HOME`:

```text
$XDG_CONFIG_HOME/ortask/ortask.ini      # when XDG_CONFIG_HOME is set
~/.config/ortask/ortask.ini             # otherwise
```

It holds essentially one setting — the path to the projdir:

```ini
[projects]
projdir = ~/tmpsorta/proj2026
```

The value is tilde-preserved. If `ortask.ini` is absent or has no `projdir`,
tools fall back to the legacy `~/.config/ortask/projtui.ini` `[projtui] projdir`,
then to the default `~/Projects`.

### Layout

Each immediate subdirectory of the projdir is one project; the subdirectory
name is the project name. Inside it are one or two symlinks, each **named after
its target's basename**:

```text
~/tmpsorta/proj2026/
  elweek/
    elweek           -> /home/robla/src/elweek                   # link to the project (required)
    TODO-ElWeek.org  -> /home/robla/src/elweek/TODO-ElWeek.org   # link to the task file (optional)
  ortask/
    ortask           -> /home/robla/src/ortask                   # project link only; task file discovered inside
```

- The **project link** points at the project's real directory; its name mirrors
  that directory's basename.
- The **task-file link** points at the `.org` file that holds the project's
  `* Tasks` subtree; its name mirrors the file's basename, so it keeps a `.org`
  extension and stays greppable. `projadd` creates it automatically from
  discovery; it is optional only in the sense that a project added to the projdir
  *by hand* may omit it.
- When the task-file link is absent, tools follow the project link and discover
  the `.org` inside the real project directory (`TODO.org`-first; see
  `docs/format.md`).

Because these are ordinary symlinks, you can add, remove, or repoint projects by
hand at any time; the verbs below are just shortcuts for common edits.

## First Verb: list

`orgmgr.py list` lists all projects under the projdir and the top-level tasks
within each.

```sh
orgmgr.py list
orgmgr.py list --projdir ~/tmpsorta/proj2026
orgmgr.py list --all
orgmgr.py list --format plain
orgmgr.py list --format json
```

`--projdir` overrides the configured projdir; otherwise the projdir is resolved
from `ortask.ini`, then `projtui.ini`, then the `~/Projects` default.

## Project Discovery

- resolve the projdir (`--projdir` > `ortask.ini` > `projtui.ini` > `~/Projects`)
- scan its immediate subdirectories; skip hidden directories and infrastructure
  directories such as `.git`, `docs`, and `__pycache__`
- treat each remaining subdirectory as a project named after the subdirectory
- select the project's task file: use a `.org` entry inside the subdirectory if
  one is present (the task-file link), otherwise follow the project link and run
  `ortask.py` single-directory discovery inside the real project directory
- do not recurse arbitrarily through project trees

This discovery is shared with `projtui.py`, so the interactive and
non-interactive tools agree on the project list and each project's task file.

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

`orgmgr.py projadd` registers a single project by creating a per-project
subdirectory of symlinks under the master projdir. The result is a real
directory you can inspect and edit from bash. It registers exactly one
directory; it never scans the target's subdirectories and never edits Org
content.

```sh
orgmgr.py projadd                          # add the current directory as a project
orgmgr.py projadd ~/src/elweek
orgmgr.py projadd ~/src/elweek --name elweek
orgmgr.py projadd ~/src/elweek --file TODO-ElWeek.org
orgmgr.py projadd ~/src/elweek --projdir ~/tmpsorta/proj2026
orgmgr.py projadd ~/src/elweek --dry-run
```

### Behavior

1. **Resolve the master projdir.** `--projdir` > `ortask.ini` `[projects]
   projdir` > legacy `projtui.ini` > default `~/Projects`. The projdir directory
   is created if it does not already exist.
2. **Resolve the project directory.** The optional positional `PATH` (default:
   the current directory) is the project's real directory. `projadd` never looks
   inside its subdirectories.
3. **Resolve the task file.** Unless `--file FILE` names one (relative to the
   project directory unless absolute), `projadd` runs `ortask.py` single-directory
   discovery in the project directory — `TODO.org` / `TODO*.org` first (per
   `docs/format.md`), then `todo.org`/`tasks.org`, then the first `*.org`. A file
   should contain a `* Tasks` section: an explicit `--file` without one is an
   error, while an auto-discovered file without one is skipped with a warning. If
   no task file is found at all, the project is still added with just the project
   link (the same state reached by adding a project to the projdir by hand).
4. **Derive the project name.** Use `--name` if given; otherwise the basename of
   the project directory.
5. **Create the project subdirectory and symlinks** under `<projdir>/<name>/`:
   - a project link named after the project directory's basename, pointing at the
     project directory (absolute target)
   - if a task file was resolved, a task-file link named after the file's
     basename, pointing at that file (absolute target)

   Any unrelated existing contents of the subdirectory are left untouched.

### Options

- `--name NAME` — project subdirectory name (default: project directory basename).
- `--file FILE` — task file to link, instead of running discovery.
- `--projdir DIR` — master projdir to add into (overrides config/default).
- `--force` — repoint the symlinks in an existing project subdirectory
  (otherwise an existing `<projdir>/<name>/` is an error).
- `--dry-run` — print the subdirectory and symlinks that would be created; make
  no changes. Exit 0.

### Conflicts

- An existing `<projdir>/<name>/` subdirectory is an error unless `--force`,
  which repoints its links.
- If a different subdirectory already links to the same project directory, warn
  but proceed (a project may legitimately be reachable under more than one name).

## Verb: migrate

`orgmgr.py migrate` is one-time setup: it records the master projdir in
`ortask.ini`, adopting the projdir from the legacy `projtui.ini` when present.
It un-deprecates the projdir model — the projdir is the canonical store and
`ortask.ini` simply remembers where it is.

```sh
orgmgr.py migrate                          # adopt projtui.ini's projdir into ortask.ini
orgmgr.py migrate --projdir ~/tmpsorta/proj2026
orgmgr.py migrate --dry-run
```

### Behavior

1. **Determine the projdir to record.** Use `--projdir` if given. Otherwise, if
   `ortask.ini` already records a projdir and `--force` is not given, keep that
   value. Otherwise read the legacy `projtui.ini` `[projtui] projdir`, falling
   back to the `~/Projects` default.
2. **Write `ortask.ini`.** Set `[projects] projdir = <path>` (tilde-preserved)
   via an atomic temp-file-then-rename. Only config is written; no Org file or
   projdir content is touched.
3. **Remove `projtui.ini`.** Delete the now-obsolete
   `~/.config/ortask/projtui.ini` if it exists, then print the recorded projdir.
   Once the projdir lives in `ortask.ini`, `projtui.ini` is no longer needed.

### Options

- `--projdir DIR` — projdir to record, overriding `projtui.ini` and any existing
  value.
- `--force` — re-derive the projdir from `projtui.ini`/default even when
  `ortask.ini` already records one.
- `--dry-run` — print what would be written and removed; change nothing. Exit 0.

`migrate` is optional: `list` and `projadd` already fall back to `projtui.ini`
and the `~/Projects` default. Its purpose is to make the projdir explicit in
`ortask.ini` and retire the legacy `projtui.ini`.

## Relationship to projtui's projdir config

The projdir model is **not** deprecated — it is the design. Historically the
projdir lived in `projtui.py`'s own config:

```ini
# ~/.config/ortask/projtui.ini
[projtui]
projdir = ~/tmpsorta/proj2026
```

Going forward, the canonical home for this setting is `ortask.ini`
`[projects] projdir`, which every tool in the suite reads. Resolution order
everywhere is:

```text
--projdir  >  ortask.ini [projects] projdir  >  projtui.ini [projtui] projdir  >  ~/Projects
```

`projtui.ini` keeps working as a fallback until you migrate; `migrate` copies
its value into `ortask.ini` and then deletes `projtui.ini`.

## Future Verbs

- `projrm NAME`: remove a project's subdirectory (and its symlinks) from the
  projdir. Removes only the links/subdirectory — never the real project or its
  Org file.
- `scan` / `doctor`: report broken symlinks, projects whose task file is missing
  or has duplicate IDs, or projdir subdirectories that are not valid projects.

These verbs manage the projdir inventory. They must not replace the local task
editing verbs in `ortask.py`.

## Safety

`orgmgr.py list` must be read-only with respect to Org content: it must not run
`repair`, create missing files, add IDs, or rewrite Org files. If duplicate IDs
are found within a project file, report that project as invalid and continue
listing other projects.

`orgmgr.py projadd` creates only directories and symlinks under the projdir. It
never creates or edits an Org file, never writes through a symlink, and refuses
(without `--force`) to overwrite an existing project subdirectory. The projdir
directory itself is created if missing.

`orgmgr.py migrate` writes only `ortask.ini` (atomically) and deletes the
obsolete `projtui.ini`. It creates and edits no Org file and touches no projdir
content.
