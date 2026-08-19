# projmgr.py

`projmgr.py` is the project-layer command for the ortask suite. It is distinct
from `ortask.py`: `ortask.py` operates on one local Org task file, while
`projmgr.py` manages a registry of projects and their Org task files across the
filesystem. Registered subcommands, help output, completions, and
command-reference sections remain alphabetical.

The intended aliases are `pmgr` for the command and `ptui` for its interactive
form, `projmgr.py -i`. `pmgr` with no subcommand shows help, as `ort` does.

This command was named `orgmgr.py` (`orgm`) until 2026-08-19. `migrate` and
`projadd` survive as deprecated aliases for `init` and `add`.

Verbs: `add`, `cdproj`, `doctor`, `init`, `list`, `rm`.

## Registry Model

`docs/projects.md` is the source of truth for the registry model: what marks a
directory as a project, why the registry is symlinks rather than a scan, what
may live in a registry entry, and why a task file is optional. This section
records only what `projmgr.py` needs in order to find it.

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

Resolution order is `--registry`, then `[projects] registry`, then `~/Projects`.
The old `[projects] projdir` key and `projtui.ini` are obsolete; tools no longer
read them.

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

The registry is inspectable with ordinary shell tools. `projmgr.py` is a
convenience layer over that directory, not a database owner, and must stay
correct when the registry is edited by hand.

## `-i`, `--interactive`

`projmgr.py -i` opens the interactive project browser for the configured
registry. It uses the same registry resolution as `list`: `--registry`, then
`[projects] registry`, then `~/Projects`.

```sh
projmgr.py -i
projmgr.py --registry ~/tmpsorta/proj2026 -i
orgm -i
```

`ptui` is the short alias. The browser shows the project list first using the
same highlight-bar selector as task lists when a TTY is available;
non-interactive runs keep the numbered fallback. It is the same project list
`cdproj` shows, differing only in what `Enter` does. Selecting a project opens that
project's Org task file; a project with no task file says so and stays put. Task views
show TODO and DONE rows by default; use `C-t` inside the task menu to cycle
visibility through `all -> TODO -> DONE`, or start with:

```sh
projmgr.py -i --todo-only
```

## `add`

`projmgr.py add` registers exactly one project by creating a registry
subdirectory of symlinks. It never edits Org content. `docs/projects.md` is the
model.

```sh
projmgr.py add                                   # the project you are in
projmgr.py add ~/tmpsorta/electorama-weekly --name elweek
projmgr.py --registry ~/tmpsorta/proj2026 add ~/src/ortask
projmgr.py add ~/src/ortask --file todo.org
projmgr.py add ~/src/ortask --dry-run
```

Behavior:

1. Resolve the registry (`--registry` > `[projects] registry` > `~/Projects`).
2. Resolve the project directory. With no argument, walk upward from the current
   directory for the nearest ancestor holding a task file or a VCS directory, and
   print the choice — so running it from `~/src/ortask/docs` registers
   `~/src/ortask`. An explicit path is taken literally and walks nothing.
3. Resolve the task file from `--file` or by the shared local task-file
   discovery convention in `docs/format.md`. A project with no task file is
   still registered.
4. Create `<registry>/<name>/`, where `name` is `--name` or the project
   directory basename.
5. Create a project symlink named after the real project directory.
6. If a task file was found, create a task-file symlink named after that file.

An existing `<registry>/<name>/` is an error unless `--force` is given. `--force`
repoints the known symlinks but leaves unrelated contents alone. Registration
never fails for want of a task file: the project symlink alone is what makes a
registry entry a project.

## `doctor`

`projmgr.py doctor` reports registry problems and changes nothing.

```sh
projmgr.py doctor
projmgr.py --registry ~/tmpsorta/proj2026 doctor
```

It prints two kinds of line. A **note** is informational: a project with no task
file, or a registry subdirectory that is not a project entry and is therefore
ignored. A **problem** is something to fix: a broken or ambiguous project link,
an unreadable task file, a file with no parseable task headings, or duplicate
task IDs.

Exit status is 0 when no problems are found and 2 when any are, matching
`ortask.py repair --dry-run`. Notes alone do not make it 2.

## `init`

`projmgr.py init` records the registry path in `ortask.ini`. It writes nothing
else.

```sh
projmgr.py init --registry ~/tmpsorta/proj2026
projmgr.py init --dry-run
```

Behavior:

- Use `--registry` when provided.
- Otherwise keep the existing `[projects] registry` value when present.
- Otherwise record the default `~/Projects`.
- Write only `ortask.ini`, atomically.

This was called `migrate` until 2026-08-19, when it had not migrated anything
for two months — `projtui.ini` and the `projdir` key were removed long before.
`migrate` remains as a deprecated alias.

## `list`

`projmgr.py list` lists all projects in the registry and the top-level tasks
within each project. It shows each Org file as a resolved path, with `$HOME`
collapsed to `~` when possible.

```sh
projmgr.py                         # show help
projmgr.py help                    # show help
projmgr.py -i                      # open the interactive project browser
projmgr.py list
projmgr.py --registry ~/tmpsorta/proj2026 list
projmgr.py list --all
projmgr.py list --format json
```

A project with no task file is listed with its project directory and
`(no task file)` in place of task rows; a broken or ambiguous entry is listed
with its warning. See `docs/projects.md` for why.

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
- Read only immediate subdirectories of the registry.
- Skip hidden directories, and any subdirectory that is not a project entry —
  see the marker rule in `docs/projects.md`.
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

## `cdproj`

`projmgr.py cdproj` opens an inline project picker and writes the selected project's
directory stack to a file, for the `cdproj` shell function in `misc/cdproj.func.sh` to
apply to the calling shell. See `docs/cdproj.md` for the whole design.

```sh
projmgr.py cdproj --out FILE
projmgr.py --registry ~/tmpsorta/proj2026 cdproj --out FILE
```

Options:

- `--out FILE`: required. The only result channel.
- `--registry PATH`: override the resolved registry.

The output file has exactly one meaning: the directory stack the calling shell
should have afterwards, one absolute path per line, top entry first. It is never
a mode header and never a file to edit, so the shell function only ever reads a
list of directories.

Behavior:

- `↵` writes the highlighted project's stack and exits 0.
- `e` edits a directory list in `$VISUAL`/`$EDITOR` and returns to the picker.
- `Esc`/`q` exits nonzero, leaving FILE untouched.

A project's stack comes from a `* Directories` section in either the project's
Org task file or a private `directories-private.org` in the project's registry
subdirectory. When both define one, `cdproj` automatically merges them. With neither, the
stack is the project root alone.

`cdproj` reads Org content and never writes it. The one file it creates is the
private list in the registry, which `projmgr.py` owns, and only when asked to
edit it.

## `rm`

`projmgr.py rm NAME` removes one project's registry entry. The real project
directory and its Org file are never touched.

```sh
projmgr.py rm elweek
projmgr.py rm elweek --dry-run
projmgr.py rm elweek --force
```

Everything a registry entry normally holds is a symlink, so removing it destroys
nothing. Anything else in the entry is real data that exists nowhere else:

- A regular file — a private directory list, say — makes `rm` stop and name it.
  `--force` deletes it along with the entry.
- A real subdirectory makes `rm` stop and refuse outright. Remove it by hand;
  this command does not delete trees.

Removing the entry directory by hand is equally valid.

## Deprecated aliases

`migrate` and `projadd` still work and dispatch to `init` and `add`. They remain
registered and completed so muscle memory and older notes keep working; new
documentation should use the current names.

## Not planned

`scan` is **not** planned. It was specified once in a since-deleted
`docs/scan.md`, then folded into `projmgr.py list`; `docs/projects.md` records
why registry membership stays explicit rather than discovered. Registration
friction is answered by `add` instead.

## Safety

`list` and `doctor` are read-only. `add` creates directories and symlinks only
inside the registry; `rm` removes only a registry entry, and only its symlinks
unless `--force` is given. `init` writes only `ortask.ini`. `cdproj` writes the file
named by `--out`, and creates a project's private directory list inside the
registry when asked to edit it; it never writes Org task content.
`docs/projects.md` states the general rule these follow: the registry,
`ortask.ini`, and files named by an explicit `--out` are the only things the
project layer writes.
