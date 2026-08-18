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

pcd_pretty_dir () {
    local dir="$1"
    if [[ "$dir" == "$HOME" ]]; then
        printf '~'
    elif [[ "$dir" == "$HOME/"* ]]; then
        printf '~/%s' "${dir#"$HOME"/}"
    else
        printf '%s' "$dir"
    fi
}

pcd_dir_in_list () {
    local needle="$1"
    shift
    local dir
    for dir in "$@"; do
        [[ "$needle" == "$dir" ]] && return 0
    done
    return 1
}

pcd () {
    local orgmgr="${ORTASK_ORGMGR:-orgmgr.py}"
    local append_mode=false
    local edit_mode=false
    local registry=""
    local args=()

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--append)   append_mode=true; shift ;;
            -e|--edit)     edit_mode=true; shift ;;
            -r|--registry) registry="$2"; shift 2 ;;
            *) echo "Usage: pcd [-a|--append] [-e|--edit] [-r|--registry PATH]" >&2; return 1 ;;
        esac
    done

    # Prepare helper arguments
    [[ -n "$registry" ]] && args+=(--registry "$registry")
    local out; out="$(mktemp)"
    args+=(pcd --out "$out")
    [[ "$edit_mode" == true ]] && args+=(--edit)

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

    if [[ ${#lines[@]} -eq 0 ]]; then
        echo "No project selected." >&2
        return 0
    fi

    # Handle Edit Mode
    if [[ "$edit_mode" == true ]]; then
        local target_file="${lines[0]}"
        "${EDITOR:-vi}" "$target_file"
        return 0
    fi

    # Save current directory stack
    local old_dirstack=()
    readarray -t old_dirstack < <(dirs -l -p)

    if [[ "$append_mode" == true ]]; then
        echo "mode: append project directories to dirstack"
    else
        echo "mode: reset dirstack to project directories"
        
        # Show directories removed by the reset
        local old_dir
        local removed_any=false
        for old_dir in "${old_dirstack[@]}"; do
            if ! pcd_dir_in_list "$old_dir" "${lines[@]}"; then
                if [[ "$removed_any" == false ]]; then
                    echo "removed by reset:"
                    removed_any=true
                fi
                printf '  - '
                pcd_pretty_dir "$old_dir"
                printf '\n'
            fi
        done
        if [[ "$removed_any" == false ]]; then
            echo "removed by reset:"
            echo "  (none)"
        fi

        dirs -c
    fi

    # Load new directory stack
    local i
    for ((i=0; i<${#lines[@]}; i++)); do
        local target_dir="${lines[$i]}"
        if [[ ! -d "$target_dir" ]]; then
            echo "Warning: directory does not exist: $target_dir" >&2
            continue
        fi

        if [[ $i -eq 0 && "$append_mode" == false ]]; then
            cd "$target_dir"
        else
            pushd "$target_dir" > /dev/null
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
- Pass both the old and new directory lists to the "removed by reset" helper as
  arguments, rather than having it read one of them out of its caller's locals
  by bash dynamic scoping. (Solved here by using a simple item-in-list helper
  `pcd_dir_in_list` which takes a single item and list arguments).

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
