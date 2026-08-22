# projmgr.py

`projmgr.py` is the project-layer command for the ortask suite. It is distinct
from `ortask.py`: `ortask.py` operates on one local Org task file, while
`projmgr.py` manages a registry of projects and their Org task files across the
filesystem. Registered subcommands, help output, completions, and
command-reference sections remain alphabetical.

The intended aliases are `pmgr` for the command and `ptui` for its interactive
form, `projmgr.py -i`. `pmgr` with no subcommand shows help, as `ort` does.

This command was named `orgmgr.py` (`orgm`) until 2026-08-19. `projadd`
survives as a deprecated alias for `add`. `migrate` converts legacy private
directory files into the registry index; it no longer dispatches to `init`.

Current verb set: `add`, `cdproj`, `doctor`, `init`, `list`, `log`, `migrate`,
`rm`, `set-dirs`.

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
pmgr -i
```

`ptui` is the short alias. The browser shows the project list first using the
same highlight-bar selector as task lists when a TTY is available;
non-interactive runs keep the numbered fallback. It is built from the same
project list `cdproj` shows, but it identifies itself as `Project navigator`,
labels its rows `PROJ`, and gives each project's open task count — see
"Telling the two lists apart" below. Selecting a project opens that
project's Org task file; a project with no task file says so and stays put. Task views
show TODO and DONE rows by default; use `C-t` inside the task menu to cycle
visibility through `all -> TODO -> DONE`, or start with:

```sh
projmgr.py -i --todo-only
```

The planned direction is to make `ptui` a project-steering dashboard rather
than only a launcher. Optional metadata in `projects.org` will provide an Org
priority and short description for each project. The default list order will
be priority then project name, with alphabetical and task-file-modified views
available without rewriting the index. Pressing `m` will open a buffered
metadata workspace; Shift-Up/Down will provide a fast priority adjustment, and
all saves will remain atomic bounded edits of the selected project section.
Registry symlinks remain authoritative for project membership and task-file
resolution. See [`docs/ptui.md`](ptui.md) for the full specification, metadata
shape, sort semantics, safety rules, and delivery order.

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

## `cdproj`

`projmgr.py cdproj` writes a selected project's directory stack to a file for the
`cdproj` shell function in `misc/cdproj.func.sh` to apply to the calling shell.
See `docs/cdproj.md` for the whole design.

```sh
projmgr.py cdproj --out FILE [PROJECT]
projmgr.py --registry ~/tmpsorta/proj2026 cdproj --out FILE [PROJECT]
```

Options:

- `PROJECT`: optional name of a registered project to resolve immediately
  without launching the picker. The match is exact; TAB completes it from the
  registry, see "Shell completion" below.
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

A project's stack comes from a `Directories` section in either the project's
own Org task file or the private list in the registry. When both define one, the
private list wins outright and sets the order; any directory the project's list
has and the private list lacks is reported on stderr as a warning, without
changing the stack or the exit status. With neither, the stack is the project
root alone. Resolving or editing the private list requires a migrated registry
index and never falls back to a legacy file.

`cdproj` reads the project's Org content and never writes it. The picker may
initialize or open one project section in an already-migrated registry index
when explicitly asked to edit the private list.

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

With the registry index in place (`t0026`), `doctor` also diagnoses migration
and index state. A missing index is a `run pmgr migrate` problem; so are an
unreadable or malformed index, stale or duplicate project sections, duplicate
direct-child `Directories` sections, and any legacy private file left beside an
index. Centralized config can outlive the entry it configures, which per-entry
files could not do; `doctor` is what makes that visible. It remains usable
before migration and exits 2 rather than refusing to run.

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
Task `t0026.2` reclaimed that name for a real data migration; it did not change
`init`.

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
projmgr.py list --format names
```

Options:

- `--all`: include top-level `DONE` tasks as well as open ones.
- `--format plain|json|names`: `names` prints one bare project name per line and
  nothing else, skipping task-file parsing. It exists so shell completion can
  ask for the registry's contents instead of globbing it — the marker rule that
  decides what counts as a project lives in Python, and a glob would offer the
  registry's own README and notes directories as if they were projects. See
  "Shell completion" below.

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

## `log`

`projmgr.py log` reads the registry-wide disposable activity stream. It never
uses events as project or task state.

```sh
projmgr.py log --since today
projmgr.py log --project elweek --limit 20
projmgr.py --registry ~/Projects log --format json
```

`--project NAME` narrows the default all-project view. `--since` and `--until`
accept `today`, `yesterday`, `week`, `NNd`, `NNh`, a date, or an ISO timestamp;
the end is exclusive. `--day-start HH:MM` shifts workday boundaries, and
`--limit N` selects the newest matches while preserving chronological output.
Formats are `plain`, byte-preserving `json`, and date-grouped `org`. A missing
log directory or no matches is successful empty output. See `docs/logging.md`
for configuration, event fields, and privacy considerations.

## `migrate`

**Status: implemented (`t0026.2`).**

`projmgr.py migrate [--dry-run]` converts per-entry
`directories-private.org` files into `<registry>/projects.org`. The index's
existence marks the registry migrated.

```sh
projmgr.py migrate --dry-run
projmgr.py migrate
```

Migration validates every legacy file first, nests each accepted document
under its project heading without discarding prose or comments, writes the
complete index atomically, and only then removes the old files. It
conservatively rejects Org keyword lines whose scope cannot be preserved by
nesting. An empty set of legacy private files still produces an empty index
marker. `--dry-run` prints the proposed index and removal plan.

The command is resumable across interruption between the index write and old
file cleanup: it removes a leftover only when that file agrees with the
corresponding index section. Any conflict stops without overwriting either
version. Once the cutover lands, this is the only command allowed to read the
legacy files. See `docs/config.md` for the full validation and migration-gate
contract.

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

## `set-dirs`

**Status: implemented (`t0031.1`).**

`projmgr.py set-dirs [--project PROJECT] DIRECTORY...` writes a directory stack
into the project's private list — the other direction from `cdproj`, which
reads one.

```sh
projmgr.py set-dirs --project ortask ~/src/ortask ~/src/ortask/docs
projmgr.py set-dirs ~/src/ortask ~/src/ortask/docs  # infer current project
dirs -l -p | projmgr.py set-dirs --stdin --missing remove
```

Omitting `--project` selects the project containing `$PWD`, by walking up for a
project root and matching it against the registry; no match, or more than one,
is an error rather than a guess. Directories arrive as arguments, or on stdin
with `--stdin`. The two inputs are mutually exclusive, and zero directories is
an error in either form. Paths under `$HOME` are stored with `~`.

Directories the live list adds are written without asking. Directories it drops
are not assumed to be unwanted, so `set-dirs` prompts to remove them, keep them,
or cancel; `--missing keep|remove` answers that without a prompt, which is what
`--stdin` needs, since it has taken stdin. A fully migrated index is required;
`set-dirs` never creates one or reads a legacy private file. Only the project's
own direct-child `Directories` subtree is rewritten. `docs/cdproj.md` has the
full specification, including the `cdproj -s` shell wrapper that supplies the
live stack.

## Telling the two lists apart

`ptui` and `cdproj` are built from one project list, one row builder, and one
highlight anchor, because a project is a project either way. That made them
look alike enough to be confusing, so each surface names itself and says
something different in its third column:

|                | `ptui`              | `cdproj`           |
|----------------|---------------------|--------------------|
| Title          | `Project navigator` | `Change directory` |
| Row label      | `PROJ`              | `CD`               |
| Third column   | open task count     | effective directory count and project location |

```text
Project navigator                                  Registry: ~/tmpsorta/proj2026
~/src/elusync  ·  todo.org

▶  1  PROJ    elusync       3 open
   2  PROJ    elweek        1 open
```

```text
Change directory                                   Registry: ~/tmpsorta/proj2026
~/src/elusync  ·  todo.org

▶  1  CD      elusync        3 dir*  ~/src/elusync
   2  CD      elweek         4 dir   ~/tmpsorta/electorama-weekly

* = custom · ↑↓/jk · ↵ select · e edit · Esc/q cancel
```

The count is the number of open top-level tasks, the same rows `list` prints,
so the two never disagree. In `cdproj`, the count is the effective stack size
after path resolution, deduplication, and project-root fallback. A trailing `*`
means the registry-defined custom stack wins; an unmarked count comes from the
project Org file or the project root. The UI says “custom” rather than exposing
the registry implementation term “private.”

Both labels stay in `menu.PROJECT_ROW_LABELS`, which is what colors a row as a
project rather than a task. Renaming one without adding it there would quietly
turn its rows gray.

The second line is shared: whichever project is highlighted, both menus name
its directory and its task file there, and the registry sits right-aligned on
the title line. See "Do not obscure the location of stuff" in
`docs/interactive.md`. `cdproj` shows the directory in its rows as well, which
is a little redundant — but the task file is not redundant there, since it is
one of the two files that can define a `* Directories` section.

In the numbered dashboards there is no highlight to describe, so the navigator
puts the location back in its rows, with the count in parentheses:

```text
┃ # ┃ Project     ┃ Location                               ┃
│ 1 │ elusync     │ ~/src/elusync  (3 open)                │
```

## Shell completion

`misc/ortask-completion.bash` registers completion for `projmgr.py`,
`./projmgr.py`, `pmgr`, `ptui`, and the `cdproj` shell function. Subcommands and
options complete from static lists; the arguments that name a project —
`cdproj PROJECT`, `rm NAME`, `set-dirs --project PROJECT`, and the shell's
`cdproj -s PROJECT` — complete from `list --format names`, so a TAB sees
exactly the projects the registry holds.

`cdproj` needs its own completion function rather than sharing `pmgr`'s. The
shell function supplies `cdproj --out FILE` itself on loads and `set-dirs` on
saves, so there is no user-entered subcommand in the words for
`_projmgr_complete` to find.

Completion runs `projmgr.py` (or `$ORTASK_PROJMGR`, the same variable
`misc/cdproj.func.sh` uses) once per TAB, about 150ms. Failures are silent: a
missing registry or an unreadable one yields no completions rather than an error
in the middle of the prompt.

## Deprecated aliases

`projadd` still dispatches to `add`; new documentation should use `add`.

## Not planned

`scan` is **not** planned. It was specified once in a since-deleted
`docs/scan.md`, then folded into `projmgr.py list`; `docs/projects.md` records
why registry membership stays explicit rather than discovered. Registration
friction is answered by `add` instead.

## Safety

`list`, `log`, and `doctor` are read-only. `add` creates directories and symlinks only
inside the registry; `rm` removes only a registry entry, and only its symlinks
unless `--force` is given. `init` writes only `ortask.ini`. `migrate` atomically
writes `projects.org` before removing validated legacy files. `set-dirs` edits
only one bounded section in that index. `cdproj` writes the file named by
`--out`, and can open or initialize a project's section in an already-migrated
index when asked to edit it; it never writes Org task content.
`docs/projects.md` states the general rule these follow: the registry,
`ortask.ini`, and files named by an explicit `--out` are the only things the
project layer writes.
