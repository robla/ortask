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
The former `doctor` alias is obsolete and is not accepted; use `repair
--dry-run` for read-only diagnosis.

Current verb set: `add`, `cdproj`, `info`, `init`, `list`, `log`, `migrate`,
`repair`, `rm`, `set-dirs`.

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
labels its rows `PROJ`, and shows priority, open-task count, and description.
The highlighted row's canonical directory and task file remain in the summary
line — see "Telling the two lists apart" below. Selecting a project opens that
project's Org task file; a project with no task file says so and stays put. Task
views show TODO and DONE rows by default; use `C-t` inside the task menu to
cycle visibility through `all -> TODO -> DONE`, or start with:

```sh
projmgr.py -i --todo-only
```

`ptui` now uses optional Org priority and description metadata from
`projects.org` as a project-steering dashboard. Its default order is priority
then project name; press `s` to cycle Priority, Alphabetical, and Modified
(newest task file first) without rewriting the index. Shift-Up/Down adjusts
project priority through
`unset -> C -> B -> A` in one session buffer; `C-/`, `C-r`, and `C-s` provide
undo, redo, and an exact-preimage-checked save. Dirty exit offers Save, Discard,
or Continue Editing, and `#projects.org#` preserves crash-recovery data.
The session polls `projects.org`: external writes refresh a clean project list,
while a dirty list merges changes to disjoint project sections in memory and
keeps the result dirty until `C-s`. Differing changes to one section open a
named Reload/Retry/Continue conflict view without overwriting either version.
Press `m` to edit priority, description, the task-file mirror, and custom
directories in one compact, preimage-checked workspace. A stale `TASK_FILE`
mirror is marked in the row and explained with recorded and resolved paths;
refreshing it remains an explicit workspace edit.
Registry symlinks remain authoritative for project membership and task-file
resolution. See [`docs/ptui.md`](ptui.md) for the full specification, metadata
shape, sort semantics, safety rules, and delivery order.

## `add`

**Status: implemented (`t0050`, `t0051`).**

`projmgr.py add` registers exactly one project: a registry subdirectory of
symlinks, and a section in the registry index for the project to keep its
settings in. `projects.org` is pmgr's own file and `add` maintains it, writing
the section named after the project being registered. Org task content is
`ortask.py`'s, and `add` does not touch it. `docs/projects.md` is the model.

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
   discovery convention in `docs/format.md`. A missing or ambiguous task file
   does not block registration; ambiguity produces a warning and `--file` can
   resolve it later.
4. Create `<registry>/<name>/`, where `name` is `--name` or the project
   directory basename.
5. Create a project symlink named after the real project directory.
6. If a task file was found, create a task-file symlink named after that file.
7. Write the project's index section, when `<registry>/projects.org` exists
   and holds no section for `name`: a `* NAME` heading, a `:PROPERTIES:` drawer
   carrying an empty `DESCRIPTION` and a `TASK_FILE` mirror of the task file
   just linked, and a `** Directories` stack. The write is atomic and appends
   only that section.

An existing `<registry>/<name>/` is an error unless `--force` is given. `--force`
repoints the known symlinks but leaves unrelated contents alone. Registration
never fails for want of a task file: the project symlink alone is what makes a
registry entry a project. It also never guesses among ambiguous Org files.

The section looks like this, and is ordinary Org that a person is expected to
edit:

```org
* inedit
:PROPERTIES:
:DESCRIPTION:
:TASK_FILE: ~/src/inedit/tasks.org
:END:
** Directories
   - ~/src/inedit
```

`Directories` starts as the project's effective stack: the entries from its own
`* Directories` section when the task file has one, resolved and written as `~`
list items, and the project root alone otherwise. The index wins outright over
a project's own list, so starting from anything narrower would shrink the stack
`cdproj` was already producing. Writing the section moves where the stack is
kept, not what it is — and from then on the index is the copy that counts, so
later edits to the project's own `* Directories` no longer take effect.
`DESCRIPTION` is written empty as the slot a person fills in; `TASK_FILE` is
left out when the project has no task file, and stays the non-normative mirror
`docs/config.md` describes.

A section that already exists is left as it stands — priority cookie,
description, and recorded directories included — so `--force` repoints symlinks
without disturbing settings. A registry with no index at all is not an error:
the symlinks are still written, the index step is skipped, and `add` reports
that it was, which keeps `add` usable before `pmgr migrate` as
`docs/projects.md` requires. `--dry-run` reports the section it would add
alongside the links. `pmgr repair` writes the same section for projects
registered before `add` did.

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

## `info`

**Status: implemented (`t0032`).**

`projmgr.py info [PROJECT | DIRECTORY]` reports the resolved project context —
what the registry knows about one project — and changes nothing.

```sh
projmgr.py info                       # infer the project from $PWD
projmgr.py info ortask                # by registered project name
projmgr.py info ~/src/elusync         # by project directory path
projmgr.py info --name                # print only the project name
projmgr.py info --path                # print only the project directory
projmgr.py info --file                # print only the task file path
projmgr.py info --format json         # machine-readable
```

### Selecting the project

The optional argument is a registered project name or a filesystem path. A name
that matches a registry entry wins; otherwise the argument is treated as a path.
With no argument, `info` resolves the project the way `add` does — it walks
upward from `$PWD` with `manager.project_root_for`, then matches that root
against the registry.

Three outcomes are distinct and must stay distinct, because callers act on them
differently:

| Outcome | Exit | stdout |
|---|---|---|
| One project matched | 0 | the report, or the single field |
| No project matched | 1 | nothing |
| The path matched more than one entry | 1 | nothing |

### The single-field forms

`--name`, `--path`, and `--file` exist for shell prompts, window titles, and
scripts. That use imposes rules the full report does not need:

- **Print one line to stdout, and nothing else, ever.** No label, no trailing
  commentary, no `~` collapsing (a path here is meant to be used, not read).
- **Say nothing on stderr when there is no answer.** A `PS1` that prints
  `no project matched` on every `cd /tmp` is unusable. Diagnose through the exit
  status alone; the full report is where a human goes to find out why.
- **They are mutually exclusive with each other and with `--format`.** Declare
  them in one `argparse` mutually exclusive group so a second flag is a usage
  error rather than a silent precedence rule nobody can predict.

`--name` prints the registry entry name — the `<registry>` subdirectory, which
is also the `projects.org` heading. `--path` prints the real project directory
(`manager.real_project_path`), resolved through the entry symlink. `--file`
prints the canonical task file (`manager.canonical_org_file`); a project with no
task file has no answer, so that is the no-match case, exit 1.

### The report

`--format plain` (the default) prints:

- **Project** — the registry entry name.
- **Directory** — the real project path, collapsed to `~`.
- **Task file** — the canonical task file, and whether the entry's link agrees
  with discovery (`manager.task_file_mirror_mismatch`).
- **Registry** — the registry location and this project's index heading as the
  index actually spells it, with its priority cookie if it has one
  (`* [#A] ortask`). Report the heading only when one was read: a project with
  no section, or a registry with no index at all, has no heading, and printing
  a plausible reconstruction of one invites a reader to go looking for a line
  that is not there.
- **Directories** — which source defines the stack and how many entries it has,
  or `unknown` when the index could not be read. "No directories" and "could
  not tell" are different answers and must print differently: an unmigrated
  registry has no readable stack, and reporting `none` there states as fact
  something `repair` correctly calls a problem.
  Name the source with the words the rest of the suite uses: **`private`** for
  the section in the registry index and **`project`** for the one in the task
  file, matching `manager.DirectorySource.label` and "Which list wins" in
  `docs/cdproj.md`. Do not introduce a third word for this.
- **Tasks** — open and completed top-level counts, from the same
  `manager.ProjectSnapshot` the navigator uses, so `info` and `ptui` cannot
  disagree.

`--format json` prints the same fields as one JSON object with those names
lowercased, so a caller never has to parse the plain form. "The same fields"
includes the same *absences*: whatever the plain form reports as unreadable or
unknown is `null` in JSON, never a zero. JSON is the form a script trusts
without a human reading it, so it is the worse of the two places to guess.

### What `info` is not

`info` is read-only and reports; `repair` diagnoses and can fix. Anything that
is *wrong* rather than merely true belongs in `repair`, so that a script can
rely on `info` never having an opinion.

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

`projmgr.py list` lists all projects in the registry, one line each: the
project's name, how much open work it has, and where it lives. `--verbose`
expands that into the top-level tasks within each project. It shows each Org
file as a resolved path, with `$HOME` collapsed to `~` when possible.

The summary is the default because a registry is a place to choose from. Every
project's task rows at once is a screenful per project, which buries the choice
the command exists to support; `--verbose` is there for when the tasks
themselves are the point.

```sh
projmgr.py                         # show help
projmgr.py help                    # show help
projmgr.py -i                      # open the interactive project browser
projmgr.py list
projmgr.py list --verbose                  # -v; the top-level tasks as well
projmgr.py --registry ~/tmpsorta/proj2026 list
projmgr.py list --all
projmgr.py list --format json
projmgr.py list --format names
```

Options:

- `-v`, `--verbose`: list each project's top-level tasks under its summary
  line, which is what `list` printed before the summary became the default.
- `--all`: include top-level `DONE` tasks as well as open ones. In the summary
  form the count then says what it is out of — `1 open of 2` — since the number
  of listed tasks and the number of open ones have stopped agreeing.
- `--format plain|json|names`: `names` prints one bare project name per line and
  nothing else, skipping task-file parsing. It exists so shell completion can
  ask for the registry's contents instead of globbing it — the marker rule that
  decides what counts as a project lives in Python, and a glob would offer the
  registry's own README and notes directories as if they were projects. See
  "Shell completion" below.

A project with no task file is listed with its project directory and
`(no task file)` in place of a count or task rows; a broken or ambiguous entry
is listed with its warning. See `docs/projects.md` for why.

Summary columns are sized from the listing itself, so one long project name
widens the table rather than pushing its own row out of line. A note standing
in for a count is allowed to overflow instead: one broken project should not
pad every other row out to the width of its complaint. The name column starts
at the width the interactive navigator uses, so a project sits in about the
same place on either surface.

Example plain output:

```text
Registry: ~/tmpsorta/proj2026

elweek          1 open          ~/tmpsorta/electorama-weekly/castabout.task.org
ortask          26 open         ~/src/ortask/todo.org
roblaconf       (no task file)  ~/confsrc/roblaconf
```

Example `--verbose` output:

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

## `repair`

**Status: partially implemented (`t0032`, `t0051.2`, `t0052.1.1`).** Writing a
missing project-index section is the first automated fix. Structured findings
now give every reported problem a recommendation. A unified action plan and
precise non-TTY handling remain tracked by `t0043` and `t0052`.

`projmgr.py repair` diagnoses the registry — broken symlinks, task-file
discoverability, index consistency — and repairs what it safely can, asking
first.

```sh
projmgr.py repair                              # report, then ask before each fix
projmgr.py repair --force                      # repair without asking
projmgr.py repair --dry-run                    # report only; never asks, never writes
projmgr.py --registry ~/tmpsorta/proj2026 repair
```

### What it reports

- A **note** is informational: a project with no task file, or a registry
  subdirectory that is not a project entry and is therefore ignored.
- A **problem** is something to fix: a broken or ambiguous project link, an
  unreadable task file, a file with no parseable task headings, duplicate task
  IDs, a missing or malformed registry index, or a registered project the index
  has no section for.

Notes alone never affect the exit status. Every problem must be followed by a
concrete suggestion. The suggestion may describe an automated repair that this
invocation can offer, name another command such as `pmgr migrate`, or identify
a specific path and manual action. Reporting a problem without a useful next
step is a command defect, not an acceptable fallback.

### Recommendations and repair plans

Diagnosis must produce a complete in-memory repair plan before any mutation.
Each finding records its problem text, recommended next step, and optional
automated action. Rendering and execution consume that same plan, so the
command cannot promise one change and perform another.

For example, a missing `projects.org` section should say that `pmgr repair`
can add the section and show its proposed directories. An unmigrated registry
should recommend `pmgr migrate`. A broken link or ambiguous task file that has
no automated repair should still recommend a concrete command or filesystem
change; it must not imply that `--force` can solve it.

`--dry-run` prints the same findings, suggestions, and proposed automated
actions as the bare command, but never prompts or writes. After an interactive
decline or a failed repair, the unresolved finding and its recommendation must
remain visible in the final output.

### Asking first

A verb named `repair` should repair — it just must not do it behind your back.
The bare form therefore reports the plan, then prompts immediately before each
automated action, defaulting to No, and applies only actions individually
confirmed. Finding a problem is never itself permission to write. Canceling
stops the remaining plan without undoing already confirmed, atomic repairs.
This mirrors the subtraction prompt `set-dirs` already shows.

**`--force`** skips the prompts and applies every available fix. `--force` is
the suite's existing word for "skip the safety" (`add --force` repoints links in
an existing entry; `rm --force` removes a whole entry), so `repair` uses the same
word rather than inventing `--fix`.

**`--dry-run`** reports and stops. It never prompts and never writes, which
makes it the safe scripting interface for diagnosis.

### Writing a missing index section

A project registered before `add` wrote index sections has none, and `repair`
offers it the same section `add` would have written, `Directories` included. It
prints the project and the directories the section would start with, then asks;
the paths are shown before the question so the answer is an informed one. This
is how a registry filled in before `t0051` catches up without re-registering
every project. `repair` re-diagnoses after its fixes, so it exits 0 when the
ones it applied were all that was wrong.

**When it has a fix to offer and cannot ask** — no TTY, no `--force` — `repair`
refuses and exits 1 rather than hanging on a prompt nobody can answer or
silently deciding to write. A non-interactive caller must then say which it
wants: `--dry-run` or `--force`.

The refusal is conditional on there being an automated action to confirm, and
that condition is the whole point of it. A run whose problems have only manual
recommendations has nothing to prompt about, so it reports and exits on the
normal rule — never 1. Today `pmgr repair` can add missing index sections; other
findings are diagnostic only. Reporting is not an interactive act, and a
command that only reports must stay usable from a script. Correcting the
current blanket non-TTY guard is tracked by `t0043`.

For the same reason `--force` says so when it had nothing to apply, rather than
exiting silently on a non-zero code.

### Exit status

One rule, shared with `ortask.py repair`:

| Condition | Exit |
|---|---|
| No problems found | 0 |
| Problems remain when the command finishes | 2 |
| The registry itself could not be read | 1 |
| A fix needed confirming and none could be asked for | 1 |

Exit 1 covers both "I could not read the subject" and "I would not act without
asking", which a caller cannot tell apart. That is tolerable only because the
second is reachable only when a fix exists and was declined the chance to run;
if callers turn out to need the distinction, split it rather than overloading
further.

"Remain" is what makes `--force` honest: a run that fixes everything exits 0, and
one that fixes four of five problems exits 2, because something is still wrong.
`--dry-run` fixes nothing, so any problem leaves it at 2.

### Index and migration state

With the registry index in place (`t0026`), `repair` also diagnoses migration
and index state. A missing index is a `run pmgr migrate` problem; so are an
unreadable or malformed index, stale or duplicate project sections, duplicate
direct-child `Directories` sections, and any legacy private file left beside an
index. Centralized config can outlive the entry it configures, which per-entry
files could not do; `repair` is what makes that visible. It remains usable
before migration and exits 2 rather than refusing to run.

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
| Row detail     | priority, open-task count, description | effective directory count and project location |

```text
Project navigator                                  Registry: ~/tmpsorta/proj2026
~/src/elusync  ·  todo.org

▶  1  PROJ    [A] elusync       3 open  Data synchronization tools
   2  PROJ    [ ] elweek        1 open  Weekly Electorama production
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

## Compatibility aliases

- `projadd` still dispatches to `add`; new documentation should use `add`.

`doctor` is obsolete rather than deprecated: it is no longer parsed or
completed. Use `repair --dry-run` for its former read-only behavior.

## Not planned

`scan` is **not** planned. It was specified once in a since-deleted
`docs/scan.md`, then folded into `projmgr.py list`; `docs/projects.md` records
why registry membership stays explicit rather than discovered. Registration
friction is answered by `add` instead.

## Safety

`list`, `log`, `info`, and `repair --dry-run` are read-only. `repair` without
`--dry-run` may write, but only after confirming each fix, and it refuses to
run unattended without `--force`. `add` creates directories and symlinks inside
the registry and writes its project's index section; `repair` writes that
section for a project registered before `add` did. `rm` removes only a registry
entry, and only its symlinks unless `--force` is given. `init` writes only
`ortask.ini`. `migrate` atomically writes `projects.org` before removing
validated legacy files. `set-dirs` edits only one bounded section in that
index. `cdproj` writes the file named by `--out`, and can open or initialize a
project's section in an already-migrated index when asked to edit it; it never
writes Org task content. `docs/projects.md` states the general rule these
follow: the registry, `ortask.ini`, and files named by an explicit `--out` are
the only things the project layer writes.
