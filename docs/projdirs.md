# Project Directory Stack (`pcd`)

`pcd` is a shell function that picks a project from the ortask registry and
loads that project's working directories into the shell's directory stack. It is
the registry-aware successor to `nowcd`/`cdnow`, which read one global
`nowdirs.txt` instead of per-project configuration.

It has two halves:

- `orgmgr.py pcd` — an inline picker that resolves one project's directory list
  and writes it to a file.
- `misc/pcd.func.sh` — a bash function that reads that file and runs
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
  root. Most projects should never need the section.

## `orgmgr.py pcd`

```sh
orgmgr.py pcd --out FILE
```

`--out` is required and is the only result channel. On selection, write the
resolved directories to FILE, one absolute path per line, and exit 0. On cancel,
exit nonzero and leave FILE untouched.

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

- `↵` resolves the highlighted project's directories, writes them, and exits 0.
- `e` opens the project's Org file in `$VISUAL`/`$EDITOR`, then returns to the
  picker so the edited stack can be selected immediately.
- `Esc`/`q` exits nonzero without writing.

`e` runs entirely inside the helper. Use `InlineMenuSession.suspend()`, which
already hands the terminal over and repaints the view afterward, and launch the
editor the way `projtui._open_editor()` does: `shlex.split()` on the variable,
`VISUAL` before `EDITOR`, per-editor line arguments. That path already works
with `EDITOR="emacs -nw"`; the shell must not re-solve it.

If the project has no `* Directories` section, `e` still just opens the file.
`orgmgr.py` does not write Org content — that is `ortask.py`'s job, per
`docs/orgmgr.md` — so it prints a one-line hint rather than bootstrapping a
section into the user's task file.

## `pcd`

`pcd` takes no arguments of its own. It forwards whatever it is given to
`orgmgr.py` and appends its own `--out`, so `pcd --registry ~/other` works
without the shell parsing anything, and a new helper flag never requires
re-sourcing.

```bash
# misc/pcd.func.sh
pcd () {
    local out; out="$(mktemp)" || return 1
    "${ORTASK_ORGMGR:-orgmgr.py}" "$@" pcd --out "$out" || { rm -f "$out"; return 1; }

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

    # First entry becomes the working directory; the rest stack beneath it in order.
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

## What this changes in the current implementation

- `misc/pcd.func.sh`: delete the argument-parsing loop, the append branch, the
  edit branch, and the `-f "${lines[0]}"` sniff. About 96 lines become about 35.
- `orgmgr.py`: drop the `--edit` flag and the code that appends a `* Directories`
  section to the user's Org file.
- `orgmgr.py`: move the roughly 60 lines of inline `* Directories` parsing out of
  `cmd_pcd` into the shared library — `core` for the Org syntax, `manager` for
  resolving entries against the project root — so it is unit-testable without
  driving the CLI, and so `ortask.py` can reuse it later.
- `orgmgr.py`: build the picker on `InlineMenuSession` rather than
  `menu.select_project_menu`. `cmd_pcd` currently depends on `_run_selector` and
  `select_project_menu`, which `t0011` exists to delete.
- Tests: the two-mode `--out` assertions collapse to one. Add direct unit tests
  for the `* Directories` parser covering plain paths, `**` prefixes, `file:`
  prefixes, `[[...]]` links, comments, `$VAR`, `~`, relative paths, section
  termination at the next `* ` heading, and the no-section fallback.

Keep the `in_executor=False` change in `menu.suspend()`. Running an interactive
child in a background executor thread is what broke `emacs -nw`, and the fix
applies to `ort -i` as much as to `pcd`.

## Open questions

- Should the directory list be shared or private? It now lives in the project's
  Org file, which is usually committed, while the `nowdirs.txt` it descends from
  was strictly personal. No override mechanism is proposed; the question is
  whether one is needed before this gets used on a shared repo.
- Should `pcd` offer a way to add the current directory to the highlighted
  project's stack, or is that squarely `ortask.py`'s business?
- Is `pcd` worth a mention in `docs/ecosystem.md`?
