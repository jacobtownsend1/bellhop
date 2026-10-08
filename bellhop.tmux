#!/bin/sh
# Optional TPM plugin. Also works with: run-shell '/path/to/bellhop.tmux'
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
key=$(tmux show-option -gqv @bellhop-key)
command=$(tmux show-option -gqv @bellhop-command)
key=${key:-B}
if [ -z "$command" ]; then
    if [ -x "$root/bin/bellhop" ]; then
        command=$root/bin/bellhop
    elif [ -x "$root/../../bin/bellhop" ]; then
        command=$root/../../bin/bellhop
    else
        command=bellhop
    fi
fi

# The option is an executable path, not a shell command. Quote it accordingly.
quoted=$(printf '%s' "$command" | sed "s/'/'\\\\''/g")
# run-shell expands formats in the originating client's context. A popup's
# shell-command itself does not expand them. Pass argv directly to the popup.
popup="tmux display-popup -c #{q:client_name} -E -w 80% -h 70% -d #{q:pane_current_path} '$quoted' --client #{q:client_name}"
tmux bind-key "$key" run-shell "$popup"
