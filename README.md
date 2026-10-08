# bellhop

```text
 _          _ _ _
| |__   ___| | | |__   ___  _ __
| '_ \ / _ \ | | '_ \ / _ \| '_ \
| |_) |  __/ | | | | | (_) | |_) |
|_.__/ \___|_|_|_| |_|\___/| .__/
                          |_|
```

A small lobby for your tmux sessions. Pick one, start a new one, or close one
you're done with. Bellhop works great over SSH, and you can use it from a local
terminal or a tmux popup too.

```text
                       bellhop
                      tmux lobby

 ╭─ Sessions (2) ───────────────────────────────────╮
 │   NAME                         WINDOWS  ATTACHED │
 ├──────────────────────────────────────────────────┤
 │ > api                                1         0 │
 │   frontend                           2         1 │
 ╰──────────────────────────────────────────────────╯

 ↑↓/jk move  hl first/last  Enter attach
 n new  d delete  r refresh  Esc exit
```

## Install

You'll need Python 3.11+ with curses, tmux 3.2+, and Make. No pip packages are
required. On Ubuntu, `sudo apt install python3 tmux make` covers the basics;
on macOS, `brew install python tmux` does the job.

From a checkout:

```sh
make install
~/.local/bin/bellhop
```

Files go into `~/.local`. Add `~/.local/bin` to your PATH to use the shorter
`bellhop` command. You can also try it without installing: `./bin/bellhop`.

For another location, use `make install PREFIX="$HOME/tools/bellhop"`.
Packagers can add `DESTDIR` to stage an installation.

## Using the lobby

| Key | Action |
| --- | --- |
| ↑ / `k`, ↓ / `j` | Move through sessions |
| `h`, `l` | Jump to first or last |
| Enter | Attach or switch |
| `n` | Create a session |
| `d` / Delete | Delete the selected session, after confirmation |
| `r` | Refresh |
| Esc / Ctrl-C | Return to your shell |

The new-session prompt lets you edit the name. Enter accepts it, Backspace
removes a character, Ctrl-U clears it, and Esc cancels. Names can contain
spaces, but not a colon or period, and can't start with a dash.

Deletion asks for a second confirmation: `y` closes the session and its
programs; any other key cancels. Sessions stay running when you disconnect.
Bellhop doesn't create or clean up sessions automatically.

The list refreshes on its own. The banner shrinks to a compact title in small
terminals, and `NO_COLOR` turns off the color palette.

## Open it from tmux

Add this to `~/.tmux.conf` after installing:

```tmux
run-shell '~/.local/share/bellhop/bellhop.tmux'
```

Reload with `tmux source-file ~/.tmux.conf`, then press your prefix followed by
**B** (usually Ctrl-B, then Shift-B). Esc closes the popup.

For a checkout or custom install, point `run-shell` at that copy of
`bellhop.tmux`. The helper also works as a TPM plugin entry point.

Optional settings go before the `run-shell` line:

```tmux
set -g @bellhop-key 'B'
set -g @bellhop-command '/absolute/path/to/bellhop'
```

The command setting takes an executable path without arguments. The binding
replaces any existing use of that key in tmux's prefix table.

Inside tmux, bellhop switches sessions instead of nesting them. Use the popup
when several clients share a session so only your client switches.

## Make it your SSH lobby

```sh
bellhop ssh enable
# Choose explicitly if needed: bellhop ssh enable --shell zsh
```

This enables the lobby for interactive SSH logins in Bash or Zsh. Esc still
lets you continue in a regular shell. To turn it off:

```sh
bellhop ssh disable
```

Bash uses your active login file. Zsh uses `.zlogin`, after `.zshrc` has set up
your PATH. Existing managed `.zprofile` hooks are moved when you enable again.

Changes to existing login files are backed up under
`~/.local/state/bellhop/backups` (or `$XDG_STATE_HOME/bellhop/backups`), with the
latest five backups kept per file. The hook skips local terminals, scripts,
and shells already inside tmux. Set `BELLHOP_DISABLE=1` before the hook runs to
bypass it.

## Remove it

From the checkout:

```sh
make uninstall
```

Use the same `PREFIX` you installed with. Uninstall also removes the managed
SSH hook. Remove any tmux config entry you added yourself.

## A few useful details

`bellhop --help` lists the CLI options. `--socket NAME` selects a separate tmux
server; `--client CLIENT` identifies a client to switch inside tmux. The lobby
needs a real terminal and a usable `TERM` setting.

Run `make check` for tests. See [CONTRIBUTING.md](CONTRIBUTING.md) for development,
and [LICENSE](LICENSE) for the MIT license.
