# Project Directory Stack (`cdproj`)

`cdproj` is a shell function that picks a project from the ortask registry and
loads that project's working directories into the shell's directory stack. It is
the registry-aware successor to `nowcd`/`cdnow`, which read one global
`nowdirs.txt` instead of per-project configuration.

It has two halves:

- `projmgr.py cdproj` — an inline picker that resolves one project's directory list
  and writes it to a file.
- `misc/cdproj.func.sh` — a bash function that reads that file and runs
  `cd`/`pushd`. Only the shell can change the shell's own directory stack, so
  this half cannot move into Python.

## The rule that keeps this simple

**The file written by `--out` has exactly one meaning: the directory stack the
shell should have when this is over, top entry first.** It is never a mode
header, never a file to edit, never a flag. The shell reads a list of
directories and applies it. That is the whole protocol.

Everything else the helper can do — editing, previewing, cancelling — happens
inside the helper and never reaches the shell. Cancelling means exiting nonzero
so the shell does nothing.

This rule is the lesson of the first implementation. Editing was routed back
through the same channel, first as an `edit`/`dirs` header line and then by
testing whether the first path was a regular file. Both made the file's meaning
depend on something outside the file, and both broke the moment a shell had
sourced one version of the function while running another version of the helper:

```text
Warning: directory does not exist: /home/robla/src/elusync/todo.org
Warning: directory does not exist: edit
```

A single-meaning channel cannot fail that way. It also means new helper features
never require re-sourcing the shell function.

## `* Directories`

A project's directory stack lives in an optional `* Directories` top-level
section in the project's Org task file:

```org
* Directories
** file:~/src/ortask
** file:~/src/elusync
** file:~/tmpsorta/electorama-weekly
```

- One directory per line, in stack order: the first entry becomes the working
  directory.
- Entries are ordinary Org subheadings. Leading asterisks, a `file:` prefix, and
  `[[...]]` link brackets are all optional and stripped when present, so a plain
  `~/src/ortask` line works too.
- `#` comments and blank lines are ignored.
- Relative paths resolve against the real project root; absolute paths are taken
  as given; `~` and `$VAR` are expanded.
- The section ends at the next top-level (`* `) heading.
- With no `* Directories` section, the stack is a single entry: the project
  root. A section that exists but lists nothing falls back the same way, since
  an empty stack would read as "do nothing" in the shell function.

## Private and shared lists

A project can have two directory lists:

| Label     | Location                                        | Shared? |
|-----------|-------------------------------------------------|---------|
| `private` | `<registry>/<project>/directories-private.org`   | No      |
| `project` | the project's Org task file                      | Usually |

The private list lives in the registry rather than in the project, so it is
never part of the project's own repository and needs no per-project
`.gitignore` entry. Both files use the same `* Directories` format and the same
parser.

When both files define a stack, `cdproj` automatically merges them, placing public (project) items on top of the stack and private items below, deduplicating any duplicate entries (retaining the first occurrence).

`directories-private.org` is excluded from task-file discovery, so a project
whose registry entry has no task-file symlink will not mistake it for one.

Private files follow the `*-private.org` naming convention from
`docs/projects.md`, so a registry kept under version control needs exactly one
`.gitignore` line to cover this file and any later ones:

```gitignore
*-private.org
```

## `projmgr.py cdproj`

```sh
projmgr.py cdproj --out FILE [PROJECT]
```

- `--out` is required and is the only result channel. On selection, write the
  resolved directories to FILE, one absolute path per line, and exit 0. On cancel,
  exit nonzero and leave FILE untouched.
- `PROJECT` (optional): resolve and write the specified project's stack directly
  without launching the picker. Exit nonzero if the project name is not registered.

Results must not go to stdout. `menu.interactive_select_available()` requires
`sys.stdout.isatty()` and the `Application` renders to stdout, so under `$(...)`
the picker would silently degrade to the numbered fallback and its output would
land in the captured value.

There is no `--edit`. Editing is a key in the picker, not a mode of the command.

## The picker

The picker follows the bounded inline contract in `docs/interactive.md`:
`full_screen=False`, content-sized within the usual ceiling.

```text
↑↓/jk · ↵ select · e edit · Esc/q cancel
```

- `↵` resolves the highlighted project's directories, writes them, and exits 0, automatically merging public and private lists (public on top) when both exist.
- `e` asks which list to edit — always both candidates, so the private file is
  discoverable — opens it in `$VISUAL`/`$EDITOR`, then returns to the picker so
  the edited stack can be selected immediately.
- `Esc`/`q` exits nonzero without writing.

`e` runs entirely inside the helper, through `InlineMenuSession.suspend()`,
which hands the terminal over and repaints the view afterward. The argv comes
from `core.editor_argv()`: `VISUAL` before `EDITOR`, `shlex.split()` so
`EDITOR="emacs -nw"` works, and a line argument only for editors known to take
one. The shell must not re-solve any of this.

Editing the private file creates it, with a `* Directories` skeleton, if it does
not exist yet — it lives in the registry, which `projmgr.py` owns. The project's
task file is never written. `ortask.py` owns Org content, per `docs/orgmgr.md`,
so a task file with no `* Directories` section is reported rather than
bootstrapped.

## `cdproj`

`cdproj` takes optional arguments and forwards whatever it is given to
`projmgr.py cdproj --out FILE`, so `cdproj myproject` resolves the project directly,
and `cdproj --registry ~/other` works without the shell parsing anything.

```bash
# misc/cdproj.func.sh
cdproj () {
    local out; out="$(mktemp)" || return 1
    "${ORTASK_PROJMGR:-projmgr.py}" cdproj --out "$out" "$@" || { rm -f "$out"; return 1; }

    local want=()
    readarray -t want < "$out"
    rm -f "$out"
    (( ${#want[@]} )) || return 0

    # Report what this drops, the way nowcd did.
    local old=() cur keep dropped=false
    readarray -t old < <(dirs -l -p)
    echo "dropped:"
    for cur in "${old[@]}"; do
        for keep in "${want[@]}"; do [[ "$cur" == "$keep" ]] && continue 2; done
        echo "  - ${cur/#$HOME/\~}"; dropped=true
    done
    [[ $dropped == true ]] || echo "  (none)"

    local have=() d i
    for d in "${want[@]}"; do
        if [[ -d "$d" ]]; then have+=("$d"); else echo "missing: $d" >&2; fi
    done
    (( ${#have[@]} )) || { echo "no usable directories" >&2; return 1; }

    # The first entry becomes the working directory. "pushd" would put each
    # argument on top and invert the list, so the rest go in with "pushd -n",
    # which inserts just below the top without changing directory.
    dirs -c
    cd "${have[0]}" || return 1
    for ((i = ${#have[@]} - 1; i > 0; i--)); do
        pushd -n "${have[$i]}" > /dev/null
    done
    dirs -v
}
```

Stack order matches file order, top first. `pushd` puts its argument on top, so
building the stack forward would invert it. `pushd -n` inserts just below the
top without changing directory, so walking the tail backwards leaves entry 0 as
the working directory and everything else in file order beneath it. This also
keeps the `cd` target equal to the final working directory, instead of
transiting through the deepest entry on the way there.

## Why `-a` and `-e` are gone

`-e` is redundant. Editing is `e` in the picker, which is both more discoverable
and the thing that removes edit-mode from the shell protocol entirely.

`-a` (append) is worth dropping too. Under `nowcd` there was one global
directory list, so append meant "add my standard directories to whatever I am
doing." With per-project stacks, append means "work on two projects at once,"
which is a real thing to want but a bad fit for a flag you must type before the
menu tells you what the projects are. If it comes back, it should come back as a
key in the picker.

Because the output file means "the stack you want afterward" rather than "this
project's directories," append can later be added as a picker key plus one input
argument telling the helper the current stack, with the apply logic in the shell
function unchanged.

## Layers

- `core.parse_directories()` — the Org syntax. Returns the raw entries under a
  top-level `* Directories` heading, or `None` when there is no such section, so
  a candidate location can be told apart from a real source. Bare paths, list
  bullets, `file:` prefixes, and `[[...]]` brackets all parse.
- `core.editor_argv()` — the editor argv, shared with `projtui._open_editor()`.
- `manager.directory_candidates()` / `directory_sources()` — the two locations,
  private first, and the subset of them that defines a stack.
- `manager.resolve_directories()` — `~`, `$VAR`, and relative-to-project-root
  expansion. Separate from parsing, because resolving needs a project root that
  `core` has no opinion about.
- `projmgr.cmd_cdproj` — views and output only. It runs on `InlineMenuSession`, not
  the one-shot `select_project_menu`/`_run_selector` path that `t0011` exists to
  delete, with a numbered fallback for pipes.

## Open questions

- Should `cdproj` offer a way to add the current directory to the highlighted
  project's stack? Deferred; it may be `ortask.py`'s business rather than the
  project layer's, since it writes Org content. Writing the *private* list is
  the project layer's, since that file lives in the registry.
- Should `-a` (append) come back as a picker key? Because the output file means
  "the stack you want afterward" rather than "this project's directories", that
  is a Python-side change plus one input argument carrying the current stack,
  with the shell function unchanged.
