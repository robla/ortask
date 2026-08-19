#!/bin/bash
# misc/pcd.func.sh
#
# Select a project from the ortask registry and load its directory stack into
# this shell. Only the shell can change the shell's own directory stack, which
# is the whole reason this half is not Python.
#
# The helper writes one thing to its --out file: the directory stack this shell
# should have afterwards, top entry first. Never a mode, never a file to edit.
# Editing happens inside the picker (press "e") and never reaches this function.
#
# Usage: pcd [orgmgr options]
# Arguments are forwarded to orgmgr.py untouched, so a new helper flag never
# requires re-sourcing this file.

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
