# Bash completion for ortask.py/projmgr.py, the common "ort"/"pmgr" aliases, and
# the "cdproj" shell function from misc/cdproj.func.sh.
#
# Source-tree usage:
#   source /path/to/ortask/misc/ortask-completion.bash
#
# Debian packages can install this to:
#   /usr/share/bash-completion/completions/ortask.py
#
# Set ORTASK_PROJMGR if projmgr.py is not on PATH, the same variable
# misc/cdproj.func.sh uses.

_ortask_complete()
{
    local cur prev words cword command
    COMPREPLY=()

    if type _get_comp_words_by_ref >/dev/null 2>&1; then
        _get_comp_words_by_ref -n : cur prev words cword
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    fi

    local subcommands="add apply archive done help init list open repair show"
    local global_opts="-i --interactive --file --help"
    local list_opts="--todo --done --all --root-only --items --format --file --help"
    local add_opts="--parent --file --help"
    local repair_opts="--dry-run --file --help"
    local apply_opts="--template --week --date --dry-run --file --help"
    local id_opts="--file --help"

    case "$prev" in
        --format)
            COMPREPLY=( $(compgen -W "plain json org" -- "$cur") )
            return 0
            ;;
        --template)
            COMPREPLY=( $(compgen -W "weekly" -- "$cur") )
            return 0
            ;;
        --file)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
        --items|--parent|--week|--date)
            return 0
            ;;
    esac

    # Find the first non-option word after the executable/alias.
    command=""
    local i
    for ((i = 1; i < cword; i++)); do
        case "${words[i]}" in
            --file|--items|--parent|--format|--template|--week|--date)
                ((i++))
                ;;
            --help|--todo|--done|--all|--root-only|--dry-run)
                ;;
            -*)
                ;;
            *)
                command="${words[i]}"
                break
                ;;
        esac
    done

    if [[ -z "$command" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=( $(compgen -W "$global_opts" -- "$cur") )
        else
            COMPREPLY=( $(compgen -W "$subcommands $global_opts" -- "$cur") )
        fi
        return 0
    fi

    if [[ "$cur" == -* ]]; then
        case "$command" in
            add)
                COMPREPLY=( $(compgen -W "$add_opts" -- "$cur") )
                ;;
            apply)
                COMPREPLY=( $(compgen -W "$apply_opts" -- "$cur") )
                ;;
            archive|done|init|open|show)
                COMPREPLY=( $(compgen -W "$id_opts" -- "$cur") )
                ;;
            list)
                COMPREPLY=( $(compgen -W "$list_opts" -- "$cur") )
                ;;
            repair)
                COMPREPLY=( $(compgen -W "$repair_opts" -- "$cur") )
                ;;
            *)
                COMPREPLY=()
                ;;
        esac
    else
        COMPREPLY=()
    fi
}

complete -o default -F _ortask_complete ortask.py
complete -o default -F _ortask_complete ./ortask.py
complete -o default -F _ortask_complete ort

# Registered project names, for the arguments that take one.
#
# A registry holds more than projects -- its own README, a docs directory -- and
# only the outward-symlink marker rule in ortasklib/manager.py can tell them
# apart, so this asks projmgr.py rather than globbing the registry. "list
# --format names" skips task-file parsing and costs about 150ms.
_projmgr_projects()
{
    local registry=() i
    for ((i = 1; i < ${#COMP_WORDS[@]}; i++)); do
        if [[ "${COMP_WORDS[i]}" == "--registry" ]]; then
            registry=(--registry "${COMP_WORDS[i+1]}")
            break
        fi
    done
    # --registry goes before the subcommand: every subparser that accepts it
    # uses argparse.SUPPRESS so the global option is the one that always works.
    "${ORTASK_PROJMGR:-projmgr.py}" "${registry[@]}" list --format names 2>/dev/null
}


_projmgr_complete()
{
    local cur prev words cword command
    COMPREPLY=()

    if type _get_comp_words_by_ref >/dev/null 2>&1; then
        _get_comp_words_by_ref -n : cur prev words cword
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    fi

    # projadd remains a deprecated alias for add; migrate is a distinct verb.
    local subcommands="add cdproj doctor help init list migrate projadd rm set-dirs"
    local global_opts="-i --interactive --registry --todo-only --help"
    local add_opts="--name --file --registry --force --dry-run --help"
    local doctor_opts="--registry --help"
    local init_opts="--registry --force --dry-run --help"
    local list_opts="--all --format --help"
    local migrate_opts="--registry --dry-run --help"
    local cdproj_opts="--out --registry --help"
    local rm_opts="--registry --force --dry-run --help"
    local set_dirs_opts="--project --stdin --missing --registry --dry-run --help"

    case "$prev" in
        --format)
            COMPREPLY=( $(compgen -W "plain json names" -- "$cur") )
            return 0
            ;;
        --missing)
            COMPREPLY=( $(compgen -W "keep remove" -- "$cur") )
            return 0
            ;;
        --project)
            COMPREPLY=( $(compgen -W "$(_projmgr_projects)" -- "$cur") )
            return 0
            ;;
        --registry|--file|--name|--out)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
    esac

    command=""
    local i
    for ((i = 1; i < cword; i++)); do
        case "${words[i]}" in
            --registry|--file|--name|--format|--project|--missing)
                ((i++))
                ;;
            -i|--interactive|--todo-only|--help|--all|--force|--dry-run|--stdin)
                ;;
            -*)
                ;;
            *)
                command="${words[i]}"
                break
                ;;
        esac
    done

    if [[ -z "$command" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=( $(compgen -W "$global_opts" -- "$cur") )
        else
            COMPREPLY=( $(compgen -W "$subcommands $global_opts" -- "$cur") )
        fi
        return 0
    fi

    if [[ "$cur" == -* ]]; then
        case "$command" in
            add|projadd)
                COMPREPLY=( $(compgen -W "$add_opts" -- "$cur") )
                ;;
            cdproj)
                COMPREPLY=( $(compgen -W "$cdproj_opts" -- "$cur") )
                ;;
            doctor)
                COMPREPLY=( $(compgen -W "$doctor_opts" -- "$cur") )
                ;;
            init)
                COMPREPLY=( $(compgen -W "$init_opts" -- "$cur") )
                ;;
            list)
                COMPREPLY=( $(compgen -W "$list_opts" -- "$cur") )
                ;;
            migrate)
                COMPREPLY=( $(compgen -W "$migrate_opts" -- "$cur") )
                ;;
            rm)
                COMPREPLY=( $(compgen -W "$rm_opts" -- "$cur") )
                ;;
            set-dirs)
                COMPREPLY=( $(compgen -W "$set_dirs_opts" -- "$cur") )
                ;;
            *)
                COMPREPLY=()
                ;;
        esac
    else
        case "$command" in
            cdproj|rm)
                COMPREPLY=( $(compgen -W "$(_projmgr_projects)" -- "$cur") )
                ;;
            set-dirs)
                COMPREPLY=( $(compgen -d -- "$cur") )
                ;;
            *)
                COMPREPLY=()
                ;;
        esac
    fi
}

complete -o default -F _projmgr_complete projmgr.py
complete -o default -F _projmgr_complete ./projmgr.py
complete -o default -F _projmgr_complete pmgr
complete -o default -F _projmgr_complete ptui


# "cdproj" is a shell function, not an alias: it supplies the subcommand and
# --out itself, so what the user types is only what follows them. It cannot
# reuse _projmgr_complete, which expects to find the subcommand in the words.
_cdproj_complete()
{
    local cur prev
    COMPREPLY=()

    if type _get_comp_words_by_ref >/dev/null 2>&1; then
        _get_comp_words_by_ref -n : cur prev
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
    fi

    if [[ "$prev" == "--registry" ]]; then
        COMPREPLY=( $(compgen -d -- "$cur") )
        return 0
    fi

    if [[ "$cur" == -* ]]; then
        local options="--registry --help"
        (( COMP_CWORD == 1 )) && options="-s --save $options"
        COMPREPLY=( $(compgen -W "$options" -- "$cur") )
        return 0
    fi

    # Save accepts at most one project and does not expose the load-only
    # --registry option. Once that project is present there is nothing else to
    # complete.
    if [[ "${COMP_WORDS[1]}" == "-s" || "${COMP_WORDS[1]}" == "--save" ]]; then
        if (( COMP_CWORD == 2 )); then
            COMPREPLY=( $(compgen -W "$(_projmgr_projects)" -- "$cur") )
        fi
        return 0
    fi

    COMPREPLY=( $(compgen -W "$(_projmgr_projects)" -- "$cur") )
}

# No "-o default" here: cdproj takes a project name, so falling back to a list
# of files on a typo would only be noise.
complete -F _cdproj_complete cdproj
