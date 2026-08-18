# Project Directory Stack (`pcd`)

`pcd` is a shell function that picks a project from the ortask registry and
loads that project's working directories into the shell's directory stack. It is
the registry-aware successor to the historical `nowcd`/`cdnow`, which read one
global `nowdirs.txt` instead of per-project configuration.

It has two halves:

- `orgmgr.py pcd` — an inline picker that resolves one project's directory list
  and writes it to a file.
- `misc/pcd.func.sh` — a bash function that reads that file and runs
  `cd`/`pushd`. Only the shell can change the shell's own directory stack, so
  this half cannot move into Python.

## Project discovery

`pcd` adds no discovery rules of its own. It resolves the registry and scans
projects exactly as `orgmgr.py list` does — see `docs/orgmgr.md` — through
`manager.resolve_registry()` and `manager.discover_projects()`, and it reuses the
existing project picker (`projtui._project_view`) rather than adding a second
selector.

## `.projdirs`

A project's directory stack is a `.projdirs` file in the project root:

```text
# Directory stack for the ortask project
.
docs
tests
```

- One directory per line, in stack order.
- `#` comments and blank lines are ignored.
- Relative paths resolve against the project root; absolute paths are taken as
  given; `~` and `$VAR` are expanded.
- With no `.projdirs`, the stack is a single entry: the project root. Most
  projects should never need the file.

Only `.projdirs` is recognized. An alternate `projdirs.txt` spelling was
considered and dropped — it doubles the lookup in both the helper and the shell
function and buys nothing.

## `orgmgr.py pcd`

```sh
orgmgr.py pcd --out FILE [--edit]
```

`--out` is required and is the only result channel.

- Default: write the selected project's resolved directories to FILE, one
  absolute path per line; exit 0.
- `--edit`: write the path of the selected project's `.projdirs` to FILE,
  creating that file with a single `.` entry if it does not exist; exit 0.
- Cancel (`Esc`, `q`): exit 1 and leave FILE untouched.

Results must not go to stdout. `menu.interactive_select_available()` requires
`sys.stdout.isatty()` and the `Application` renders to stdout, so under `$(...)`
the picker would silently degrade to the numbered fallback and its output would
land in the captured value. Keeping one file-based channel also keeps everything
the shell would otherwise need to know about `.projdirs` — its name, location,
and default contents — on the Python side.

The picker follows the bounded inline contract in `docs/interactive.md`:
`full_screen=False`, content-sized within the usual ceiling, `↑↓`/`j`/`k` to
move, `Enter` to select, `Esc`/`q` to cancel. The highlighted project's resolved
stack previews in the footer; the session has one body control, so there is no
side pane to put it in.

## `pcd`

```sh
pcd [-a|--append] [-e|--edit] [-r|--registry PATH]
```

- Default (reset): report which directories the reset drops, run `dirs -c`, `cd`
  to the first project directory, `pushd` the rest, then print `dirs -v`.
- `-a`: leave the current stack alone and `pushd` the project directories onto
  it.
- `-e`: open the selected project's `.projdirs` in `$EDITOR`.
- Directories that do not exist produce a warning and are skipped.

```bash
# misc/pcd.func.sh (prototype)

pcd () {
    local orgmgr="${ORTASK_ORGMGR:-orgmgr.py}"
    local append=false edit=false reg="" args=()

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--append)   append=true; shift ;;
            -e|--edit)     edit=true; shift ;;
            -r|--registry) reg="$2"; shift 2 ;;
            *) echo "Usage: pcd [-a] [-e] [-r PATH]" >&2; return 1 ;;
        esac
    done

    # Prepare helper arguments
    [[ -n "$reg" ]] && args+=(--registry "$reg")
    local out; out="$(mktemp)"
    args+=(pcd --out "$out")
    [[ "$edit" == true ]] && args+=(--edit)

    # Run the interactive picker
    "$orgmgr" "${args[@]}"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        rm -f "$out"
        return $rc
    fi

    # Read selected targets
    local lines=()
    if [[ -f "$out" ]]; then
        readarray -t lines < "$out"
        rm -f "$out"
    fi

    [[ ${#lines[@]} -eq 0 ]] && return 0

    # Handle Edit Mode
    if [[ "$edit" == true ]]; then
        local editor_cmd=(${EDITOR:-vi})
        "${editor_cmd[@]}" "${lines[0]}"
        return 0
    fi

    # Save current directory stack
    local old_dirstack=()
    readarray -t old_dirstack < <(dirs -l -p)

    if [[ "$append" == true ]]; then
        echo "mode: append project directories to dirstack"
    else
        echo "mode: reset dirstack to project directories"
        
        # Show directories removed by the reset (inline diff)
        echo "removed by reset:"
        local old_dir removed=false
        for old_dir in "${old_dirstack[@]}"; do
            local found=false dir
            for dir in "${lines[@]}"; do
                [[ "$old_dir" == "$dir" ]] && found=true && break
            done
            if [[ "$found" == false ]]; then
                echo "  - ${old_dir/#$HOME/\~}"
                removed=true
            fi
        done
        [[ "$removed" == false ]] && echo "  (none)"

        dirs -c
    fi

    # Load new directory stack
    local i
    for ((i=0; i<${#lines[@]}; i++)); do
        local target_dir="${lines[$i]}"
        if [[ -d "$target_dir" ]]; then
            if [[ $i -eq 0 && "$append" == false ]]; then
                cd "$target_dir"
            else
                pushd "$target_dir" > /dev/null
            fi
        else
            echo "Warning: directory does not exist: $target_dir" >&2
        fi
    done

    echo "stack:"
    dirs -v
}
```

Four things the first draft of this design got wrong, worth not repeating:

- Build the helper invocation as an array. A command held in a string and
  expanded unquoted word-splits, and breaks on a registry path with spaces.
- Locate `orgmgr.py` through `PATH` or an override variable, not a hardcoded
  absolute path in a checked-in file.
- In append mode, do not `pushd .` first. Every entry is pushed, so the current
  directory is already preserved; `nowcd` needed that only because it `cd`s its
  first entry.
- Perform the directory diff inline to avoid dynamic scoping issues, passing complex
  arrays, or polluting the global shell namespace with auxiliary helper functions.

## Integration checklist

- Register `pcd` alphabetically in `orgmgr.py` — between `migrate` and
  `projadd` — in the parser, help, and dispatch tables, per repo policy.
- Document the verb in `docs/orgmgr.md`.
- Add completion for it in `misc/ortask-completion.bash`.
- `pcd` reads the registry and writes only the file named by `--out`. Like the
  rest of `orgmgr.py`, it never touches Org content.

## Open questions

- Should `.projdirs` be checked into a project, or kept local and gitignored?
  Checked in it becomes shared team configuration; the `nowdirs.txt` it descends
  from was strictly personal.
- Should `-e` apply the edited stack immediately, as `nowcd -e` did, or only
  edit?
- Is `pcd` worth a mention in `docs/ecosystem.md`?
