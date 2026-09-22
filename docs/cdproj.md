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
Warning: directory does not exist: /home/alex/src/atlas/tasks.org
Warning: directory does not exist: edit
```

A single-meaning channel cannot fail that way. It also means new helper features
never require re-sourcing the shell function.

## `* Directories`

A project's directory stack lives in an optional `* Directories` top-level
section in the project's Org task file:

```org
* Directories
** file:~/src/atlas
** file:~/src/atlas/docs
** file:~/work/newsletter
```

- One directory per line, in stack order: the first entry becomes the working
  directory.
- Entries are ordinary Org subheadings. Leading asterisks, a `file:` prefix, and
  `[[...]]` link brackets are all optional and stripped when present, so a plain
  `~/src/atlas` line works too.
- `#` comments and blank lines are ignored.
- Relative paths resolve against the real project root; absolute paths are taken
  as given; `~` and `$VAR` are expanded.
- The section ends at the next top-level (`* `) heading.
- With no `* Directories` section, the stack is a single entry: the project
  root. A section that exists but lists nothing falls back the same way, since
  an empty stack would read as "do nothing" in the shell function.

## Private and shared lists

A project can have two directory lists:

| Label     | Where it lives                          | Shared? |
|-----------|-----------------------------------------|---------|
| `private` | the registry, outside the project's repo | No      |
| `project` | the project's own Org task file          | Usually |

The private list lives in the registry rather than in the project, so it is
never part of the project's own repository and needs no per-project
`.gitignore` entry. Both locations use the same entry format and the same
parser.

**Status (2026-08-21): migration and consumer cutover are implemented.**
`cdproj` reads private stacks from `<registry>/projects.org`, with a
`Directories` section under the top-level heading named for the registry
entry:

```org
# ~/Projects/projects.org
* atlas
  Release automation and deployment notes.
** Directories
   - ~/src/atlas
   - ~/src/atlas/docs
```

Only the location changes. Every resolution rule below still applies, and the
label stays `private`. The cutover has no dual-read period: after `t0026`, an
absent index produces `registry not migrated; run pmgr migrate`, and a legacy
file beside the index is an incomplete-migration error. Only `pmgr migrate`
reads the old files. A migrated index may omit a project section normally; that
project simply has no private list.

### Which list wins

The `private` list wins. When both files define a `* Directories` section, the
stack is the private list, in the private list's order. The project list is not
merged in — it is only checked against.

| `private` | `project` | Resulting stack | Warning |
|-----------|-----------|-----------------|---------|
| —                | —                | the project root alone      | no |
| —                | defines a stack  | the project list, its order | no |
| defines a stack  | —                | the private list, its order | no |
| defines a stack  | defines a stack  | the private list, its order | one per project entry the private list lacks |

The warning names every directory the project list has and the private list
does not, and it names the private file it checked:

```text
atlas: in tasks.org but not projects.org: ~/src/atlas/docs
```

It is a warning, not an error. The stack is still written and the exit status is
still 0.

Merging was the previous behavior and it was wrong in one specific way: it let
the *shared* list push directories into a stack the user had deliberately
curated, with no way to say "no, not that one." Overriding silently would be
wrong in the opposite way — a project that adds a directory you would want in
your stack is exactly the case worth hearing about. Warning keeps the private
list authoritative and still reports that the shared list moved.

Comparison rules:

- Entries are compared *after* resolution — `~`, `$VAR`, and
  relative-to-project-root expansion, then `Path.resolve()` — so `docs`,
  `./docs`, and `~/src/atlas/docs` are one entry rather than three.
- The winning list is deduplicated, first occurrence kept.
- An empty `Directories` section counts as a list that exists. An empty private
  section therefore wins, the stack falls back to the project root, and every
  project entry is reported. Emptying the list is a choice; silently reverting
  to the shared list would undo it. Under the index this needs one more
  distinction: a project with *no section at all* in `projects.org` has no
  private list, while a project whose section has an empty `Directories`
  heading has an empty one.
- The private list may name directories the project list does not. Those are
  never reported — that is what the private list is for.

Warnings go to stderr, after the picker has exited, so `erase_when_done=True`
does not take them with it. The `--out` file still carries nothing but
directories.

All three routes into a stack — the picker, `cdproj PROJECT`, and the numbered
fallback — resolve identically and warn identically.

Neither the index nor a migration leftover is a candidate for task-file
discovery. `directories-private.org` remains excluded by name, and
`projects.org` sits at the registry root rather than inside an entry, where
`discover_projects` never looks. What standing *in* the registry and running
`ort` picks up is covered in `docs/config.md`.

Per-entry private files follow the `*-private.org` naming convention from
`docs/projects.md`, so a registry kept under version control needs one
`.gitignore` line to cover them:

```gitignore
*-private.org
```

`projects.org` is deliberately not covered by that line. Whether to track the
index is the registry owner's call: it holds machine-local paths, but it also
holds the prose about why each project is registered, which is a reasonable
thing for a registry repo to carry.

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
`full_screen=False`, content-sized within the usual ceiling. It titles itself
`Change directory` and labels its rows `CD`, so it is not mistaken for the
`ptui` navigator built from the same list. Each row shows the effective stack
size before the project directory, keeping paths column-aligned. A trailing
`*` on the count means the registry-defined custom stack wins.

```text
* = custom · ↑↓/jk · ↵ select · e edit · Esc/q cancel
```

- `↵` resolves the highlighted project's directories, writes them, and exits 0.
  When both lists exist the private one wins outright; see "Which list wins".
- `e` asks which list to edit — always both candidates, so the private index is
  discoverable — opens it in `$VISUAL`/`$EDITOR`, then returns to the picker so
  the edited stack can be selected immediately.
- `Esc`/`q` exits nonzero without writing.

`e` runs entirely inside the helper, through `InlineMenuSession.suspend()`,
which hands the terminal over and repaints the view afterward. The argv comes
from `core.editor_argv()`: `VISUAL` before `EDITOR`, `shlex.split()` so
`EDITOR="emacs -nw"` works, and a line argument only for editors known to take
one. The shell must not re-solve any of this.

Editing the private list requires a migrated index. It opens `projects.org` at
the project's heading; if that project has no section yet, the helper adds a
project heading with a direct-child `** Directories` skeleton before launching
the editor. It never creates the index implicitly. The project's task file is
never written. `ortask.py` owns Org task content, so a task file with no
`* Directories` section is reported rather than bootstrapped.

## `cdproj`

For loads, `cdproj` forwards whatever it is given to
`projmgr.py cdproj --out FILE`, so `cdproj myproject` resolves the project
directly and `cdproj --registry ~/other` works without the shell parsing those
arguments. The one shell-side branch is `-s`/`--save`, which captures data only
the current shell can provide.

```bash
# misc/cdproj.func.sh
cdproj () {
    if [[ "${1-}" == "-s" || "${1-}" == "--save" ]]; then
        shift
        if (( $# > 1 )); then
            echo "usage: cdproj -s [PROJECT]" >&2
            return 2
        fi

        local project_args=()
        (( $# == 1 )) && project_args=(--project "$1")

        local stack=()
        readarray -t stack < <(dirs -l -p)
        (( ${#stack[@]} )) || { echo "cdproj: directory stack is empty" >&2; return 1; }

        "${ORTASK_PROJMGR:-projmgr.py}" set-dirs "${project_args[@]}" "${stack[@]}"
        return $?
    fi

    local out; out="$(mktemp)" || return 1
    "${ORTASK_PROJMGR:-projmgr.py}" cdproj --out "$out" "$@" || { rm -f "$out"; return 1; }

    local want=()
    readarray -t want < "$out"
    rm -f "$out"
    (( ${#want[@]} )) || return 0

    # Summarize the swap, the way nowcd did: what leaves, then what arrives.
    # Anything in both lists is unremarkable, and "dirs -v" below shows where
    # everything ended up, so only the difference is worth a line.
    local old=() gone=() arrived=() cur other label
    readarray -t old < <(dirs -l -p)
    for cur in "${old[@]}"; do
        for other in "${want[@]}"; do [[ "$cur" == "$other" ]] && continue 2; done
        gone+=("${cur/#$HOME/\~}")
    done
    for cur in "${want[@]}"; do
        for other in "${old[@]}"; do [[ "$cur" == "$other" ]] && continue 2; done
        arrived+=("${cur/#$HOME/\~}")
    done
    if (( ${#gone[@]} + ${#arrived[@]} )); then
        # The label heads its first path and the rest hang under it, so the
        # paths line up in one column however many there are of each.
        label="dropped:"
        for cur in "${gone[@]}"; do
            printf '%-8s  %s\n' "$label" "$cur"; label=""
        done
        label="added:"
        for cur in "${arrived[@]}"; do
            printf '%-8s  %s\n' "$label" "$cur"; label=""
        done
        echo
    fi

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

## Completion

`cdproj <Tab>` and `cdproj -s <Tab>` complete registered project names, so the
exact-match `PROJECT` argument does not have to be typed from memory. The names
come from `projmgr.py list --format names`; `misc/ortask-completion.bash`
registers a `_cdproj_complete` for the function and must be sourced alongside
this file.

The names deliberately come from Python rather than from a glob of the registry.
A registry holds more than projects — its own README, a notes directory — and
only the outward-symlink marker rule in `ortasklib/manager.py` separates the
two, so a `ls`-based completion would offer entries that `cdproj` then rejects.

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

- `orglib.syntax.parse_directories()` — the Org syntax. Returns the raw entries
  under a `Directories` heading, or `None` when there is no such section, so a
  candidate location can be told apart from a real source. Bare paths, list
  bullets, `file:` prefixes, and `[[...]]` brackets all parse. It keeps the
  top-level behavior used by project task files.
- `orglib.syntax.parse_project_directories()` — the source-backed registry
  lookup. It accepts only the named project's unique direct-child
  `** Directories` section and rejects duplicate project or section headings.
- `orglib.Document.directories(project)` — the public `orglib` boundary for
  that lookup. It distinguishes missing project, missing section, and empty
  section and exposes exact project and section spans. `manager` uses this
  boundary for index reads and bounded section initialization.
- `core.editor_argv()` — the editor argv, shared with `taskui._open_editor()`.
  It takes an optional line number, which is how `e` opens the index at the
  right project.
- `manager.directory_candidates()` / `directory_sources()` — the candidate
  locations, private first, and the subset of them that defines a stack. The
  index is the only private candidate; a missing or incompletely migrated
  index is an error rather than a fallback.
- `manager.resolve_directories()` — `~`, `$VAR`, and relative-to-project-root
  expansion. Separate from parsing, because resolving needs a project root that
  `core` has no opinion about.
- `_CdprojSession.resolve_stack()` — which list wins, in what order, and what to
  warn about. The one place a stack is resolved: the picker, `cdproj PROJECT`,
  and the numbered fallback all reach it through `write_selection()`, so they
  cannot answer the question differently.
- `_CdprojSession.report()` — warnings then errors, on stderr, after any picker
  has exited.
- `projmgr.cmd_cdproj` — views and output only. It runs on
  `InlineMenuSession`, with a numbered fallback for pipes.

## Saving the directory stack (`cdproj -s` / `pmgr set-dirs`)

**Status: implemented (`t0031`).**

`cdproj` loads a stack; this is the other direction. Once a stack has been
arranged in the shell with `cd`, `pushd`, and `popd`, it can be written back to
the project's private list:

```bash
cdproj -s [PROJECT]     # or cdproj --save [PROJECT]
```

The shell half captures its own live stack with `dirs -l -p` and passes it to:

```sh
projmgr.py set-dirs [--project PROJECT] DIRECTORY...
projmgr.py set-dirs [--project PROJECT] --stdin [--missing keep|remove]
                    [--dry-run]
```

`-s` is the one thing the shell function has to know about, and it is worth
saying why, given that `-a` and `-e` were removed for adding shell-side
parsing. Those two asked the shell to carry a *mode* into Python. `-s` carries
*data* Python cannot otherwise obtain: the live directory stack belongs to the
shell, and no key inside the picker can read it. The `--out` protocol is
untouched — `set-dirs` writes nothing to `--out` and the shell function does
not `cd`. The shell maps its optional positional project to
`set-dirs --project PROJECT`; the Python command does not overload its first
directory as a possible project name.

### Which project

With `--project PROJECT`, use that registry entry, matched exactly as
`cdproj PROJECT` matches. Without it:

1. walk up from `$PWD` for a VCS marker or a task file (`project_root_for()`);
2. compare that root against the registered projects (`discover_projects()`),
   by resolved path;
3. exactly one match selects the project.

No match is an error naming the resolved root, with the registered project
names listed. Two matches — the same directory registered twice under different
names — is also an error, since guessing between them would write to the wrong
section.

### Where the stack comes from

Directories arrive as positional arguments, one per argument. `--stdin` reads
them from standard input instead, one per line, for scripting; it is mutually
exclusive with positional directories. Zero directories, including empty
`--stdin`, is an error. Clearing a private list needs a future explicit
operation rather than an easy-to-mistype empty invocation.

`cdproj -s` passes the stack as arguments rather than on stdin, deliberately:
stdin has to stay free for the subtraction prompt below. `--stdin` therefore
implies non-interactive, and in that mode `--missing` supplies the answer the
prompt would have asked for, defaulting to `keep`.

### What gets written

Into the project's section of `<registry>/projects.org`:

- **The index must already exist and migration must be complete.** `set-dirs`
  never creates the migration marker and never reads a legacy private file.

- **Paths under `$HOME` are written with `~`.** `dirs -l -p` prints them
  expanded; storing them expanded would tie the index to one machine's home.
- **Entries are written as list items** (`   - ~/src/atlas`) under a
  `** Directories` heading. Reading still accepts subheadings, bare paths, and
  `file:` links, per `* Directories` above; writing picks one form.
- **Order is the shell's stack order,** top entry first — the same order
  `cdproj` will replay.
- **Duplicates are dropped,** first occurrence kept, comparing resolved paths.
- **A missing project section is created** at the end of the file, as a
  top-level heading named for the registry entry with a `** Directories`
  subheading under it. A missing `Directories` heading inside an existing
  section is added at the end of that section, so prose written under the
  project heading stays where the user put it.
- **Nothing outside that `Directories` subtree changes.** Other projects'
  sections, the prose in this one, and the file's own `* Tasks` section are
  untouched, byte for byte. Same atomic replace as every other write.

### Additions and subtractions

Comparison is by resolved path, the same rule `cdproj` uses to decide what to
warn about, so `~/src/atlas` and `/home/alex/src/atlas` are one entry.

- **Additions** — directories in the live stack that the private list lacks —
  are written without asking. That is the point of the verb.
- **Subtractions** — directories the private list has that the live stack lacks
  — are not assumed. `popd` and "I am working elsewhere today" look identical
  from here, and only one of them means "forget this directory."

So subtractions prompt, listing them:

- `[r]emove` — the private list becomes the live stack exactly.
- `[k]eep` — additions are written and the missing entries stay, appended after
  the live stack in their existing relative order.
- `[c]ancel` — nothing is written; exit nonzero.

With no subtractions there is no prompt. With `--stdin`, or with no terminal to
prompt on, `--missing` decides and the choice is reported on stderr; it
defaults to `keep`, because keeping a directory the user still lists is the
recoverable mistake.

`--dry-run` prints the section that would be written and exits without touching
the file, matching `init --dry-run` and `rm --dry-run`. It still validates the
migration state and requires at least one input directory.

## Open questions

- Should `-a` (append) come back as a picker key? Because the output file means
  "the stack you want afterward" rather than "this project's directories", that
  is a Python-side change plus one input argument carrying the current stack,
  with the shell function unchanged.
- Should the interactive navigator (`ptui`) or `cdproj` picker support saving
  the live shell stack directly with a single keybinding (e.g. `s` or `w`)?
  Same shape as the `-a` answer above: the picker cannot see the shell's stack,
  so the shell function would have to pass it in as an input argument, and the
  key would then write it through the same `set-dirs` path.
