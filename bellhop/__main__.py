"""Handle Bellhop's command-line options."""

import argparse
from pathlib import Path
import os
import shutil
import sys

from bellhop import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bellhop — a small lobby for tmux sessions.')
    parser.add_argument('--version', action='version', version='bellhop ' + __version__)
    parser.add_argument('--socket', help='tmux socket name (tmux -L)')
    parser.add_argument('--client', help='tmux client to switch when opened in a popup')
    commands = parser.add_subparsers(dest='command')
    ssh = commands.add_parser('ssh', help='opt-in SSH login integration')
    ssh.add_argument('action', choices=['enable', 'disable'])
    ssh.add_argument('--shell', choices=['bash', 'zsh'],
                     default=Path(os.environ.get('SHELL', 'bash')).name)
    args = parser.parse_args(argv)
    from bellhop.tmux import Tmux, TmuxError
    from bellhop.integration import enable_ssh, disable_ssh, IntegrationError
    try:
        if args.command == 'ssh':
            if args.action == 'enable':
                if not shutil.which('tmux'):
                    raise IntegrationError('Install tmux before enabling the SSH lobby.')
                import curses  # Verify runtime availability before editing login files.
                path = enable_ssh(args.shell, Path.home(), Path(sys.argv[0]))
                print('Enabled SSH lobby in ' + str(path) + '.')
            else:
                disable_ssh(args.shell, Path.home())
                print('Disabled bellhop SSH lobby.')
            return 0
        from bellhop.ui import run_lobby
        tmux = Tmux(['tmux', '-L', args.socket] if args.socket else None)
        client = tmux.current_client(args.client)
        message = ''
        while True:
            target = run_lobby(tmux, message)
            if target is None:
                return 0
            try:
                return tmux.connect(target, client)
            except TmuxError as error:
                message = str(error)
    except (TmuxError, IntegrationError, RuntimeError, ImportError, OSError) as error:
        print('bellhop: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
