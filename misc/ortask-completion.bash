# Bash completion for ortask.py and the common "ort" alias.
#
# Source-tree usage:
#   source /path/to/ortask/misc/ortask-completion.bash
#
# Debian packages can install this to:
#   /usr/share/bash-completion/completions/ortask.py

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

    local subcommands="help list show add done open repair apply"
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
            list)
                COMPREPLY=( $(compgen -W "$list_opts" -- "$cur") )
                ;;
            add)
                COMPREPLY=( $(compgen -W "$add_opts" -- "$cur") )
                ;;
            show|done|open)
                COMPREPLY=( $(compgen -W "$id_opts" -- "$cur") )
                ;;
            repair)
                COMPREPLY=( $(compgen -W "$repair_opts" -- "$cur") )
                ;;
            apply)
                COMPREPLY=( $(compgen -W "$apply_opts" -- "$cur") )
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
