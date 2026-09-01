#!/bin/bash
# misc/cdproj.func.sh
#
# Select a project from the ortask registry and load its directory stack into
# this shell. Only the shell can change the shell's own directory stack, which
# is the whole reason this half is not Python.
#
# The helper writes one thing to its --out file: the directory stack this shell
# should have afterwards, top entry first. Never a mode, never a file to edit.
# Editing happens inside the picker (press "e") and never reaches this function.
#
# Usage: cdproj [PROJECT] [cdproj options]
#        cdproj -s|--save [PROJECT]
# Load arguments are forwarded to "projmgr.py cdproj" after --out. Save is the
# one shell-side mode because only this shell can read its live directory stack.
#
# Tab completion for PROJECT lives in misc/ortask-completion.bash.

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

    # Name the tmux window after the directory we landed in. cdproj knows the
    # registry name of the project it just loaded, but has no way to say so:
    # --out carries directories and nothing else. The two agree unless a
    # project's stack starts somewhere other than its own root.
    if [[ -n "${TMUX-}" && -n "${have[0]##*/}" ]]; then
        tmux rename-window "${have[0]##*/}"
    fi

    dirs -v
}
