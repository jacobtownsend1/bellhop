# Sourced by the optional login block; never exec away the caller's shell.
case $- in
    *i*) ;;
    *) return 0 ;;
esac
[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ] || return 0
[ -z "${TMUX:-}${TMUX_PANE:-}" ] || return 0
[ "${BELLHOP_DISABLE:-0}" != 1 ] || return 0
[ -t 0 ] && [ -t 1 ] || return 0
[ -n "${TERM:-}" ] && [ "$TERM" != dumb ] || return 0
if [ -n "${BASH_VERSION:-}" ]; then
    shopt -q login_shell || return 0
elif [ -n "${ZSH_VERSION:-}" ]; then
    [[ -o login ]] || return 0
else
    return 0
fi
if [ -x "${_bellhop_executable:-}" ]; then
    "$_bellhop_executable" || :
else
    printf '%s\n' 'bellhop: installed executable is missing; continuing to the shell.' >&2
fi
