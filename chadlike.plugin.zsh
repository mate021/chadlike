# Oh My Zsh entry point; no HTTP or subprocesses in command hooks.
[[ -o interactive && -o zle && -z ${_CHADLIKE_LOADED-} ]] || return 0
[[ ${CHADLIKE_DISABLED:-0} == 1 ]] && return 0

# Resolve the source file, not the current directory or Oh My Zsh's caller.
typeset -g _CHADLIKE_ROOT=${${(%):-%x}:A:h}

chadlike() {
    emulate -L zsh
    local REPLY python=${commands[python3]-}
    if [[ ${1-} == inspect && -n ${2-} ]]; then
        _chadlike_classify "$2"
        set -- inspect "$REPLY" "${@:3}"
    fi
    [[ -n $python ]] || return 127
    command env -i HOME="$HOME" PATH=/usr/bin:/bin LANG=C.UTF-8 \
        XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}" CHADLIKE_CONFIG="${CHADLIKE_CONFIG-}" \
        "$python" -B "$_CHADLIKE_ROOT/chadlike.py" "$@"
}

# These helpers only return a member of a fixed vocabulary in REPLY. Tokens are
# temporary shell locals: no arguments, paths, or assignments are retained/sent.
_chadlike_skip_options() {
    local token flag
    integer index
    while (( pos <= ${#tokens} )) && [[ ${tokens[pos]} == -* ]]; do
        token=${tokens[pos]}
        (( pos++ ))
        [[ $token == -- ]] && break
        case "$name:$token" in
            sudo:--list|sudo:--validate|sudo:--edit|*:--help|*:--version|env:-S|env:--split-string*) return 1 ;;
            command:-*v*|command:-*V*) return 1 ;;
        esac
        if [[ $name == sudo && $token != --* ]]; then
            for (( index=2; index<=${#token}; index++ )); do
                flag=${token[index]}
                [[ $flag == [lve] ]] && return 1
                if [[ $flag == [ughpCTRD] ]]; then
                    (( index == ${#token} )) && (( pos++ ))
                    break
                fi
            done
        else
            case "$name:$token" in
                sudo:--user|sudo:--group|sudo:--host|sudo:--prompt|env:-u|env:--unset|env:-C|env:--chdir|dnf:--installroot|dnf:--releasever|dnf:--config|dnf:-c|dnf:--setopt|dnf:--enablerepo|dnf:--disablerepo|flatpak:--installation|systemctl:-H|systemctl:--host|systemctl:-M|systemctl:--machine|systemctl:--root|git:-C|git:-c|git:--git-dir|git:--work-tree|git:--namespace|docker:--context|docker:-c|docker:--host|docker:-H|docker:--config|docker:-f|docker:--file|docker:-p|docker:--project-name|docker:--project-directory|docker:--env-file|docker:--profile)
                    (( pos++ )) ;;
            esac
        fi
    done
    return 0
}

_chadlike_classify() {
    emulate -L zsh
    setopt extendedglob
    REPLY=unclassified
    # Conservatively skip substitutions and large input without expanding them.
    (( ${#1} <= 4000 )) || return 0
    [[ $1 != *'$('* && $1 != *'`'* ]] || return 0
    local -a tokens
    local token name sub
    integer pos=1 index
    tokens=("${(@z)1}")
    for token in "${tokens[@]}"; do
        case $token in
            ';'|'&'|'&&'|'|'|'||'|'('|')'|[0-9]#'>'*|[0-9]#'<'*|*$'\n'*) return 0 ;;
        esac
    done
    tokens=("${(@Q)tokens}")
    while (( pos <= ${#tokens} )); do
        token=${tokens[pos]}
        name=${token:t}
        if [[ $token == [a-zA-Z_][a-zA-Z_0-9]#=* ]]; then
            (( pos++ ))
        elif [[ $name == (sudo|env|command|builtin|noglob) ]]; then
            (( pos++ ))
            _chadlike_skip_options || return 0
        else
            break
        fi
    done
    (( pos <= ${#tokens} )) || return 0
    for (( index=pos+1; index<=${#tokens}; index++ )); do
        [[ ${tokens[index]} == -- ]] && break
        [[ ${tokens[index]} == (--help|--version) ]] && return 0
    done
    case $name in
        ssh|mkdir|rm|rmdir|cp|mv|curl|wget|tar|zip|unzip|nano|vim|nvim|clear)
            REPLY=$name; return 0 ;;
        dnf|flatpak|systemctl|git|docker) ;;
        *) return 0 ;;
    esac
    (( pos++ ))
    _chadlike_skip_options || return 0
    if [[ $name == docker ]]; then
        [[ ${tokens[pos]-} == compose ]] || return 0
        (( pos++ ))
        _chadlike_skip_options || return 0
    fi
    sub=${tokens[pos]-}
    case "$name $sub" in
        'dnf '(install|remove|upgrade|autoremove)|'flatpak '(install|uninstall|update|run)|'systemctl '(start|stop|restart)|'git '(clone|pull|commit|push)|'docker '(up|down|start|stop|restart|pull|build))
            REPLY="$name $sub" ;;
    esac
    if [[ $REPLY == 'flatpak uninstall' ]]; then
        for (( index=pos+1; index<=${#tokens}; index++ )); do
            token=${tokens[index]}
            [[ $token == -- ]] && break
            [[ $token == --unused ]] && REPLY='flatpak uninstall --unused'
        done
    fi
    return 0
}

_chadlike_preexec() {
    local original_status=$?
    emulate -L zsh
    _CHADLIKE_CATEGORY=''
    # Leading whitespace is an explicit privacy opt-out, even without history.
    [[ ${CHADLIKE_DISABLED:-0} != 1 && $1 != [[:space:]]* ]] || return $original_status
    local REPLY
    _chadlike_classify "$1"
    _CHADLIKE_CATEGORY=$REPLY
    _CHADLIKE_STARTED=$EPOCHREALTIME
    return $original_status
}

_chadlike_precmd() {
    local original_status=$?
    emulate -L zsh
    local LC_ALL=C packet
    if [[ -n $_CHADLIKE_CATEGORY && ${CHADLIKE_DISABLED:-0} != 1 ]]; then
        if kill -0 $_CHADLIKE_PID 2>/dev/null; then
            packet=$'metadata_v1\t'"$original_status"$'\t'"$(( EPOCHREALTIME - _CHADLIKE_STARTED ))"$'\t'"$_CHADLIKE_CATEGORY"$'\0'
            # Linux PIPE_BUF is 4096. Drop oversized commands instead of splitting frames.
            if (( ${#packet} <= 4000 )); then
                syswrite -o $_CHADLIKE_IN "$packet" 2>/dev/null || true
            fi
        elif [[ -z ${_CHADLIKE_DEAD_REPORTED-} ]]; then
            [[ -n $_CHADLIKE_EMERGENCY ]] && print -r -- "$_CHADLIKE_EMERGENCY"
            _CHADLIKE_DEAD_REPORTED=1
        fi
    fi
    _CHADLIKE_CATEGORY=''
    # zsh restores the command status around precmd dispatch; returning zero also
    # lets subsequent hook-array entries run after a failed user command.
    return 0
}

_chadlike_ready() {
    emulate -L zsh
    local chunk line stamp text
    if [[ -n ${2-} ]]; then
        zle -F "$1" 2>/dev/null
        return 0
    fi
    if sysread -i "$1" -s 4096 chunk 2>/dev/null; then
        _CHADLIKE_BUFFER+=$chunk
        while [[ $_CHADLIKE_BUFFER == *$'\n'* ]]; do
            line=${_CHADLIKE_BUFFER%%$'\n'*}
            _CHADLIKE_BUFFER=${_CHADLIKE_BUFFER#*$'\n'}
            stamp=${line%%$'\t'*}
            text=${line#*$'\t'}
            if [[ $stamp == emergency ]]; then
                _CHADLIKE_EMERGENCY=$text
                continue
            fi
            # Discard comments held while a foreground application owned the tty.
            [[ $stamp == <->.<-> && ${CHADLIKE_DISABLED:-0} != 1 ]] || continue
            (( EPOCHREALTIME - stamp < 15 )) || continue
            zle -I
            print -r -- "$text"
        done
        (( ${#_CHADLIKE_BUFFER} < 8192 )) || _CHADLIKE_BUFFER=''
    fi
    return 0
}

_chadlike_cleanup() {
    emulate -L zsh
    [[ -n ${_CHADLIKE_OUT-} ]] && zle -F $_CHADLIKE_OUT 2>/dev/null
    [[ -n ${_CHADLIKE_PID-} ]] && kill -TERM $_CHADLIKE_PID 2>/dev/null
    [[ -n ${_CHADLIKE_IN-} ]] && exec {_CHADLIKE_IN}>&-
    [[ -n ${_CHADLIKE_OUT-} ]] && exec {_CHADLIKE_OUT}>&-
    if [[ -n ${_CHADLIKE_DIR-} && $_CHADLIKE_DIR == */chadlike.* ]]; then
        command rm -f -- "$_CHADLIKE_DIR/events" "$_CHADLIKE_DIR/results" 2>/dev/null
        command rmdir -- "$_CHADLIKE_DIR" 2>/dev/null
    fi
    unset _CHADLIKE_PID _CHADLIKE_IN _CHADLIKE_OUT _CHADLIKE_DIR
    return 0
}

chadlike-off() {
    emulate -L zsh
    _chadlike_cleanup
    add-zsh-hook -d preexec _chadlike_preexec
    add-zsh-hook -d precmd _chadlike_precmd
    add-zsh-hook -d zshexit _chadlike_cleanup
    unset _CHADLIKE_LOADED _CHADLIKE_DEAD_REPORTED _CHADLIKE_EMERGENCY
    return 0
}

_chadlike_setup() {
    emulate -L zsh
    zmodload zsh/system && zmodload zsh/datetime || return 1
    autoload -Uz add-zsh-hook
    local python=${commands[python3]-} backend="$_CHADLIKE_ROOT/chadlike.py"
    [[ -n $python && -r $backend ]] || return 1
    local base=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}
    typeset -g _CHADLIKE_DIR
    _CHADLIKE_DIR=$(umask 077; command mktemp -d "$base/chadlike.XXXXXXXX") || return 1
    command mkfifo -m 600 "$_CHADLIKE_DIR/events" "$_CHADLIKE_DIR/results" || { _chadlike_cleanup; return 1; }
    typeset -g _CHADLIKE_IN _CHADLIKE_OUT _CHADLIKE_PID
    sysopen -rw -o nonblock,cloexec -u _CHADLIKE_IN "$_CHADLIKE_DIR/events" || { _chadlike_cleanup; return 1; }
    sysopen -rw -o nonblock,cloexec -u _CHADLIKE_OUT "$_CHADLIKE_DIR/results" || { _chadlike_cleanup; return 1; }
    # Do not copy exported credentials into the long-lived helper environment.
    command env -i HOME="$HOME" PATH=/usr/bin:/bin LANG=C.UTF-8 \
        XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}" CHADLIKE_CONFIG="${CHADLIKE_CONFIG-}" \
        "$python" -B "$backend" session "$_CHADLIKE_DIR" $$ </dev/null >/dev/null 2>&1 &!
    _CHADLIKE_PID=$!
    unset _CHADLIKE_COMMAND
    typeset -g _CHADLIKE_CATEGORY='' _CHADLIKE_BUFFER='' _CHADLIKE_EMERGENCY='' _CHADLIKE_LOADED=1
    typeset -gF _CHADLIKE_STARTED=0
    # First in the arrays captures status before other array hooks can change it.
    add-zsh-hook preexec _chadlike_preexec
    add-zsh-hook precmd _chadlike_precmd
    preexec_functions=(_chadlike_preexec ${preexec_functions:#_chadlike_preexec})
    precmd_functions=(_chadlike_precmd ${precmd_functions:#_chadlike_precmd})
    add-zsh-hook zshexit _chadlike_cleanup
    zle -F $_CHADLIKE_OUT _chadlike_ready
    syswrite -o $_CHADLIKE_IN $'startup\t0\t0\t\0' 2>/dev/null || true
    return 0
}

_chadlike_setup 2>/dev/null || true
