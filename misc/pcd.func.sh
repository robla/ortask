#!/bin/bash
# misc/pcd.func.sh
# Bash function to select an ortask project and switch to its directory stack.
# Follows the handrail-style UX and integrates with orgmgr.py.

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
        "${EDITOR:-vi}" "${lines[0]}"
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
