# Project Directory Stack (pcd) Design

This document specifies the design for `pcd` (Project CD), a bash-integrated, handrail-style TUI utility for managing the terminal directory stack (`dirstack`) on a per-project basis. It leverages the project discovery and registry configuration of the `ortask` suite (`projtui.py` / `orgmgr.py`).

## Objective
Provide a lightweight shell utility that allows a developer to select a project from a menu using a compact, inline terminal interface, and then automatically configures the shell's directory stack to the directories associated with that project.

The behavior mirrors the historical `cdnow` function but is integrated with the `ortask` registry and follows the **Handrail UX** guidelines.

---

## Configuration & Project Discovery

`pcd` reads the same configuration files and follows the same discovery rules as `projtui.py` and `orgmgr.py`:

1. **Registry Resolution:** 
   - Uses `ortask.ini` (`~/.config/ortask/ortask.ini` or `$XDG_CONFIG_HOME/ortask/ortask.ini`) to find the `[projects] registry` directory path.
   - Defaults to `~/Projects` if no registry is defined.
2. **Project Scanning:**
   - Scans immediate subdirectories of the registry, skipping hidden directories and ignored folders (`.git`, `docs`, `__pycache__`, etc.).
   - Resolves target project root paths from symlinks in the registry.

### Project Directory Stack Config (`.projdirs` / `projdirs.txt`)
Each project specifies its own directory stack using a simple text file located in the root of the project directory.

- **File Name:** `.projdirs` (preferred) or `projdirs.txt`.
- **Location:** `project.path / .projdirs`
- **Format:**
  - Standard text format.
  - One directory path per line.
  - Paths may be:
    - **Relative:** Resolved relative to the project root directory (e.g., `.` or `docs` or `tests`).
    - **Absolute:** Fully qualified paths (e.g., `/var/log`).
  - Supports environment variables (e.g., `$VAR` or `$HOME`) and tilde expansion (`~`).
  - Comments (lines starting with `#`) and empty/whitespace-only lines are ignored.
- **Fallback Behavior:** If no `.projdirs` or `projdirs.txt` is found in the project root, the directory stack defaults to a single entry: the project's root directory (`project.path`).

#### Example `.projdirs` File for `ortask`:
```text
# Directory stack for the ortask project
.
docs
tests
```

---

## Handrail UX Selection Helper

To support a clean inline TUI selection without taking over the screen, we will add a `pcd` subcommand to `orgmgr.py`.

### Subcommand Specification: `orgmgr.py pcd`
```sh
orgmgr.py pcd [--out <output-file>] [--print-project-path]
```

- **Interactive Selection Menu:**
  - Renders inline (not full-screen) below the user's prompt using `prompt_toolkit`.
  - Content-sized height, capped at a configured ceiling (e.g., 10-15 rows).
  - Navigation using Up/Down arrow keys or `j`/`k`.
  - `Enter` selects the highlighted project and exits.
  - `Esc` or `q` cancels the menu and exits.
- **Visual Feedback:**
  - Highlighting a project shows a preview of its directory stack (e.g., paths resolved to absolute paths) in the status footer or side info pane.
- **Output:**
  - If `--out <output-file>` is supplied, the helper writes the selected project's resolved directories (absolute paths, one per line) to the output file on successful selection, and exits with status `0`.
  - If `--print-project-path` is supplied (used for editing), it writes the selected project's root path to stdout and exits with status `0`.
  - On cancellation (`Esc`, `q`), the helper exits with status `1` (or non-zero) and does not write to the output file.

---

## Bash Integration (`misc/pcd.func.sh`)

The user-facing bash function `pcd` wraps the Python selection helper and modifies the active shell's directory stack.

### Interface
```sh
pcd [-a|--append] [-e|--edit] [-r|--registry <path>]
```

- **Reset Mode (Default):**
  - Invokes `orgmgr.py pcd` to let the user select a project.
  - Saves the current directory stack.
  - Identifies which directories are being removed on reset and displays them (similar to `nowcd_show_removed_dirs`).
  - Clears the active shell directory stack using `dirs -c`.
  - Runs `cd` on the first directory in the project's directory stack.
  - Runs `pushd` on subsequent directories to populate the stack.
  - Prints the resulting stack via `dirs -v`.
- **Append Mode (`-a` / `--append`):**
  - Keeps the existing shell directory stack intact.
  - Appends the new project directories to the stack.
  - Runs `pushd` to populate the stack and prints `dirs -v`.
- **Edit Mode (`-e` / `--edit`):**
  - Lets the user select a project from the menu and opens the project's `.projdirs` configuration file in their default editor (`$EDITOR` or `nano`).

### Prototype Implementation for `misc/pcd.func.sh`
```bash
#!/bin/bash

# misc/pcd.func.sh
# Bash function to select an ortask project and switch to its directory stack.
# Follows the handrail-style UX and integrates with orgmgr.py.

function pcd_pretty_dir () {
    local dir="$1"
    if [[ "$dir" == "$HOME" ]]; then
        printf '~'
    elif [[ "$dir" == "$HOME/"* ]]; then
        printf '~/%s' "${dir#"$HOME"/}"
    else
        printf '%s' "$dir"
    fi
}

function pcd_dir_in_list () {
    local needle="$1"
    shift
    local dir
    for dir in "$@"; do
        if [[ "$needle" == "$dir" ]]; then
            return 0
        fi
    done
    return 1
}

function pcd_show_removed_dirs () {
    local old_dirs=("$@")
    local removed="false"
    echo "removed by reset:"
    local dir
    for dir in "${old_dirs[@]}"; do
        if ! pcd_dir_in_list "$dir" "${new_pcd_dirs[@]}"; then
            printf '  - '
            pcd_pretty_dir "$dir"
            printf '\n'
            removed="true"
        fi
    done
    if [[ "$removed" != "true" ]]; then
        echo "  (none)"
    fi
}

function pcd () {
    local append_mode="false"
    local edit_mode="false"
    local registry_override=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -a|--append)
                append_mode="true"
                shift
                ;;
            -e|--edit)
                edit_mode="true"
                shift
                ;;
            -r|--registry)
                registry_override="$2"
                shift 2
                ;;
            *)
                echo "Usage: pcd [-a|--append] [-e|--edit] [-r|--registry <path>]"
                return 1
                ;;
        esac
    done

    # Build the base python helper command
    local helper_cmd="python3 /home/robla/src/ortask/orgmgr.py"
    if [[ -n "$registry_override" ]]; then
        helper_cmd="$helper_cmd --registry $registry_override"
    fi

    # Handle Edit Mode
    if [[ "$edit_mode" == "true" ]]; then
        local selected_project_path
        selected_project_path=$( $helper_cmd pcd --print-project-path 2>/dev/null )
        local exit_code=$?
        if [[ $exit_code -ne 0 || -z "$selected_project_path" ]]; then
            return $exit_code
        fi
        
        local proj_dirs_file="${selected_project_path}/.projdirs"
        if [[ ! -f "$proj_dirs_file" && -f "${selected_project_path}/projdirs.txt" ]]; then
            proj_dirs_file="${selected_project_path}/projdirs.txt"
        fi
        
        # Ensure file exists or prompt to create it
        if [[ ! -f "$proj_dirs_file" ]]; then
            echo "Creating new directory stack config at: $proj_dirs_file"
            echo "." > "$proj_dirs_file"
        fi
        
        echo "Editing: $proj_dirs_file"
        ${EDITOR:-nano} "$proj_dirs_file"
        return 0
    fi

    # Run Selection Helper
    local temp_file
    temp_file=$(mktemp)
    
    # Execute the interactive selection helper
    $helper_cmd pcd --out "$temp_file"
    local exit_code=$?
    
    if [[ $exit_code -ne 0 ]]; then
        rm -f "$temp_file"
        return $exit_code
    fi

    if [[ ! -s "$temp_file" ]]; then
        echo "No project selected."
        rm -f "$temp_file"
        return 0
    fi

    # Read the selected directories
    local new_pcd_dirs=()
    while IFS= read -r line; do
        new_pcd_dirs+=("$line")
    done < "$temp_file"
    rm -f "$temp_file"

    if [[ ${#new_pcd_dirs[@]} -eq 0 ]]; then
        echo "Error: No directories loaded."
        return 1
    fi

    # Save the current directory stack
    local old_dirstack=()
    while IFS= read -r line; do
        old_dirstack+=("$line")
    done < <(dirs -l -p)

    if [[ "$append_mode" == "true" ]]; then
        echo "mode: append project directories to dirstack"
        pushd . > /dev/null
    else
        echo "mode: reset dirstack to project directories"
        pcd_show_removed_dirs "${old_dirstack[@]}"
        dirs -c
    fi

    # Apply the new directories to the stack
    local i
    for ((i=0; i<${#new_pcd_dirs[@]}; i++)); do
        local target_dir="${new_pcd_dirs[$i]}"
        if [[ ! -d "$target_dir" ]]; then
            echo "Warning: directory does not exist: $target_dir"
            continue
        fi
        if [[ $i -eq 0 && "$append_mode" != "true" ]]; then
            cd "$target_dir"
        else
            pushd "$target_dir" > /dev/null
        fi
    done

    echo "stack:"
    dirs -v
}
