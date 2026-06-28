# orgmgr.py

`orgmgr.py` is the global operations command for the ortask suite. It is
distinct from `ortask.py`: `ortask.py` operates on one local Org task file,
while `orgmgr.py` manages a registry of project Org files across the filesystem.

## Registry Model

The project registry is a real directory on disk. Each immediate subdirectory is
one project, and each project directory contains symlinks to the real project
directory and, optionally, the project task file.

`ortask.ini` stores the registry directory path:

```ini
[projects]
registry = ~/tmpsorta/proj2026
```

Config location honors `$XDG_CONFIG_HOME`:

```text
$XDG_CONFIG_HOME/ortask/ortask.ini
~/.config/ortask/ortask.ini
```

The old `[projects] projdir` key and `projtui.ini` are obsolete. Tools no longer
read them. If `ortask.ini` has no `[projects] registry` value, the default
registry is `~/Projects`.

Example registry layout:

```text
~/tmpsorta/proj2026/
  elweek/
    elweek           -> /home/robla/tmpsorta/electorama-weekly
    TODO-ElWeek.org  -> /home/robla/tmpsorta/electorama-weekly/TODO-ElWeek.org
  ortask/
    ortask           -> /home/robla/src/ortask
    todo.org         -> /home/robla/src/ortask/todo.org
```

The registry is inspectable with ordinary shell tools. `orgmgr.py` is a
convenience layer over that directory, not a database owner.

## `list`

`orgmgr.py list` lists all projects in the registry and the top-level tasks
within each project. It shows each Org file as a resolved path, with `$HOME`
collapsed to `~` when possible.

```sh
orgmgr.py                         # show help
orgmgr.py help                    # show help
orgmgr.py -i                      # open the interactive project browser
orgmgr.py list
orgmgr.py --registry ~/tmpsorta/proj2026 list
orgmgr.py list --all
orgmgr.py list --format json
```

With the common alias, `orgm -i` is the short form for opening the same
registry-scoped project browser. This is the user-facing replacement for
starting `projtui.py` directly; `projtui.py` remains the implementation shim for
now.

Example plain output:

```text
Registry: ~/tmpsorta/proj2026

ortask  ~/src/ortask/todo.org
  [TODO] t0001 Remove AI slop from docs/taskwarrior.md
  [TODO] t0002 create .org file if none exist in directory when using 'ort add'
```

Discovery rules:

- Resolve the registry from `--registry`, then `[projects] registry`, then
  `~/Projects`.
- Scan only immediate subdirectories of the registry.
- Skip hidden directories and infrastructure directories such as `.git`, `docs`,
  and `__pycache__`.
- Prefer a `.org` symlink/file inside the project registry subdirectory.
- If no task-file link exists, follow the project directory symlink and run the
  local `ortask.py` task-file discovery there.

Task selection rules:

- Parse the selected Org file with the shared ortask parser.
- Show only root-level parsed tasks. In a `* Tasks` file this means direct
  children of `* Tasks`; otherwise it means the shallowest parsed task heading
  level.
- Show only `TODO` tasks by default.
- With `--all`, include top-level `DONE` tasks too.
- Warn per project, rather than crashing, for unreadable files, files with no
  parseable task headings, or duplicate task IDs.

## `-i`, `--interactive`

`orgmgr.py -i` opens the interactive project browser for the configured
registry. It uses the same registry resolution as `list`: `--registry`, then
`[projects] registry`, then `~/Projects`.

```sh
orgmgr.py -i
orgmgr.py --registry ~/tmpsorta/proj2026 -i
orgm -i
```

The browser shows the project list first using the same highlight-bar selector
as task lists when a TTY is available; non-interactive runs keep the numbered
fallback. Selecting a project opens that project's Org task file. Task views
show TODO and DONE rows by default; use `C-t` inside the task menu to cycle
visibility through `all -> TODO -> DONE`, or start with:

```sh
orgmgr.py -i --todo-only
```

## `projadd`

`orgmgr.py projadd` adds exactly one project to the registry by creating a
registry subdirectory of symlinks. It never edits Org content.

```sh
orgmgr.py projadd
orgmgr.py projadd ~/tmpsorta/electorama-weekly --name elweek
orgmgr.py --registry ~/tmpsorta/proj2026 projadd ~/src/ortask
orgmgr.py projadd ~/src/ortask --file todo.org
orgmgr.py projadd ~/src/ortask --dry-run
```

Behavior:

1. Resolve the registry (`--registry` > `[projects] registry` > `~/Projects`).
2. Resolve the project directory from the optional `PATH` argument, defaulting
   to the current directory.
3. Resolve the task file from `--file` or by the shared local task-file
   discovery convention in `docs/format.md`.
4. Create `<registry>/<name>/`, where `name` is `--name` or the project
   directory basename.
5. Create a project symlink named after the real project directory.
6. If a task file was found, create a task-file symlink named after that file.

An existing `<registry>/<name>/` is an error unless `--force` is given. `--force`
repoints the known symlinks but leaves unrelated contents alone.

## `migrate`

`orgmgr.py migrate` now only writes the registry path into `ortask.ini`. It does
not read or delete `projtui.ini`.

```sh
orgmgr.py migrate --registry ~/tmpsorta/proj2026
orgmgr.py migrate --dry-run
```

Behavior:

- Use `--registry` when provided.
- Otherwise keep the existing `[projects] registry` value when present.
- Otherwise record the default `~/Projects`.
- Write only `ortask.ini` atomically.

This command is still useful for bootstrapping a fresh config, but it is no
longer a compatibility bridge from the old projtui-specific config.

## Future Verbs

- `projrm NAME`: remove a project’s registry subdirectory and symlinks without
  touching the real project or Org file.
- `doctor`: report broken symlinks, missing task files, duplicate task IDs, or
  registry entries that are not valid projects.

These verbs manage the registry inventory. They must not replace local task
editing verbs in `ortask.py`.

## Safety

`list` is read-only with respect to Org content. `projadd` creates directories
and symlinks only inside the registry. `migrate` writes only `ortask.ini`.
