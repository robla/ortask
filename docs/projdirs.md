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

## `* Directories`

A project's directory stack is specified in an optional `* Directories` top-level section inside the project's `.org` task file:

```org
* Directories
# Directory stack for the ortask project
** file:~/src/ortask
** file:~/src/nowtools
** file:~/src/vergoog
```

- One directory per line in the body of the section, in stack order.
- Leading asterisks (e.g., `** `), optional `file:` prefixes, and optional Org-mode link brackets (`[[...]]`) are stripped/ignored when parsing.
- `#` comments and blank lines are ignored.
- Relative paths resolve against the resolved project root; absolute paths are taken as given; `~` and `$VAR` are expanded.
- With no `* Directories` section, the stack defaults to a single entry: the project root. Most projects should never need to define this section.

## `orgmgr.py pcd`

```sh
orgmgr.py pcd --out FILE [--edit]
```

`--out` is required and is the only result channel.

- Default: write the header "dirs" followed by the selected project's resolved directories to FILE, one absolute path per line; exit 0.
- `--edit` (or interactive `e` key press): write the header "edit" followed by the path of the selected project's `.org` task file to FILE, appending a `* Directories` section to the end of that file if it does not already exist; exit 0.
- Cancel (`Esc`, `q`): exit 1 and leave FILE untouched.

Results must not go to stdout. `menu.interactive_select_available()` requires `sys.stdout.isatty()` and the `Application` renders to stdout, so under `$(...)` the picker would silently degrade to the numbered fallback and its output would land in the captured value. Keeping one file-based channel also keeps everything the shell would otherwise need to know about the `.org` file and default section contents on the Python side.

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
- `-e`: open the selected project's `.org` task file in `$EDITOR`.
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

    local action="${lines[0]}"
    local dirs_to_load=()
    if [[ "$action" == "dirs" ]]; then
        dirs_to_load=("${lines[@]:1}")
    elif [[ "$action" == "edit" ]]; then
        dirs_to_load=()
    else
        dirs_to_load=("${lines[@]}")
    fi

    # Handle Edit Mode (either from CLI flag or TUI action)
    if [[ "$action" == "edit" || "$edit" == true ]]; then
        local target_file="${lines[1]}"
        if [[ "$action" != "edit" ]]; then
            target_file="${lines[0]}"
        fi
        local editor_cmd=(${EDITOR:-vi})
        "${editor_cmd[@]}" "$target_file"
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
            for dir in "${dirs_to_load[@]}"; do
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

    # Load new directory stack in reverse order to preserve their order in the dirstack
    local i
    local first_push=true
    for ((i=${#dirs_to_load[@]}-1; i>=0; i--)); do
        local target_dir="${dirs_to_load[$i]}"
        if [[ -d "$target_dir" ]]; then
            if [[ "$first_push" == true && "$append" == false ]]; then
                cd "$target_dir"
                first_push=false
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

- Since directories are now stored inside the `.org` file, they are automatically shared with the team. Should we support a private workspace-local override file (e.g. `.git/info/exclude` style) for developers who have different local paths?
- Should `-e` apply the edited stack immediately, as `nowcd -e` did, or only
  edit?
- Is `pcd` worth a mention in `docs/ecosystem.md`?
