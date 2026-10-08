"""Manage tmux sessions and select the current client."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional


class TmuxError(Exception):
    """A command failed or a session name is invalid."""


@dataclass(frozen=True)
class Session:
    id: str
    name: str
    windows: int
    clients: int
    server_pid: int = 0
    created: int = 0


def _process_info(pid):
    """Return parent PID, process name, and controlling terminal device."""
    try:
        data = (Path('/proc') / str(pid) / 'stat').read_text()
        name = data[data.index('(') + 1:data.rindex(')')]
        fields = data[data.rindex(')') + 2:].split()
        number = int(fields[4])
        tty = os.makedev((number >> 8) & 0xfff,
                         (number & 0xff) | ((number >> 12) & 0xfff00)) if number else None
        return int(fields[1]), name, tty
    except (OSError, ValueError, IndexError):
        if not shutil.which('ps'):
            raise TmuxError('Cannot verify terminal ancestry; install ps or preserve TMUX.')
        try:
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'ppid=', '-o', 'tty=', '-o', 'comm='],
                                    capture_output=True, text=True, timeout=2)
            pieces = result.stdout.strip().split(None, 2)
            if len(pieces) != 3:
                return None
            terminal = pieces[1]
            tty = None
            if terminal.strip('?'):
                try:
                    tty = os.stat('/dev/' + terminal).st_rdev
                except FileNotFoundError:
                    tty = os.stat('/dev/tty' + terminal).st_rdev
            return int(pieces[0]), Path(pieces[2]).name, tty
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise TmuxError('Cannot verify terminal ancestry; preserve TMUX.') from error


def enclosing_tmux():
    """Look for tmux only within this terminal's process ancestry."""
    own = _process_info(os.getpid())
    if own is None or own[2] is None:
        return False
    pid, _, terminal = own
    visited = set()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        info = _process_info(pid)
        if info is None:
            return False
        parent, name, tty = info
        # A genuine pane's immediate tmux server has no controlling terminal.
        if name == 'tmux' or name.startswith('tmux:'):
            return True
        if tty != terminal:
            return False
        pid = parent
    return False


class Tmux:
    def __init__(self, argv_prefix=None, env=None):
        self.prefix = argv_prefix or ['tmux']
        self.env = os.environ.copy() if env is None else env.copy()

    def _run(self, *args):
        try:
            result = subprocess.run(self.prefix + list(args), env=self.env,
                                    capture_output=True, text=True,
                                    encoding='utf-8', errors='replace')
        except OSError as error:
            raise TmuxError(str(error)) from error
        return result

    @staticmethod
    def _check(result):
        if result.returncode:
            raise TmuxError(result.stderr.strip() or 'tmux command failed')
        return result.stdout

    def list_sessions(self):
        result = self._run('list-sessions', '-F',
                           '#{session_id} #{session_windows} #{session_attached} #{pid} #{session_created} #{session_name}')
        if result.returncode and ('no server running on ' in result.stderr or
                                 ('error connecting to ' in result.stderr and
                                  'No such file or directory' in result.stderr)):
            return []
        output = self._check(result)
        sessions = []
        for line in output.rstrip('\n').split('\n'):
            fields = line.split(' ', 5)
            if len(fields) != 6:
                raise TmuxError('Could not read tmux session information')
            try:
                sessions.append(Session(fields[0], fields[5], int(fields[1]), int(fields[2]),
                                        int(fields[3]), int(fields[4])))
            except ValueError as error:
                raise TmuxError('Could not read tmux session information') from error
        return sorted(sessions, key=lambda s: (s.name.casefold(), s.id))

    def default_name(self):
        names = {s.name for s in self.list_sessions()}
        number = 1
        while 'bellhop-' + str(number) in names:
            number += 1
        return 'bellhop-' + str(number)

    def create_session(self, name, cwd):
        if (not name or len(name) > 80 or name.startswith('-') or
                any(not c.isprintable() or c in '.:' for c in name)):
            raise TmuxError('Use 1–80 printable characters; no colon, period, or leading dash.')
        session_id = self._check(self._run('new-session', '-d', '-P', '-F', '#{session_id}',
                                          '-s', name, '-c', cwd)).strip()
        for session in self.list_sessions():
            if session.id == session_id:
                return session
        raise TmuxError('The new session exited before it could be attached')

    def kill_session(self, session):
        # Check the server identity and delete through the same connection.
        # IDs are not reused within a server, but a restarted server can reuse $0.
        if (not session.id.startswith('$') or not session.id[1:].isdigit() or
                session.server_pid <= 0 or session.created <= 0):
            raise TmuxError('Session identity changed; refresh and try again.')
        condition = '#{&&:#{==:#{pid},%d},#{==:#{session_created},%d}}' % (
            session.server_pid, session.created)
        result = self._check(self._run('if-shell', '-F', '-t', session.id, condition,
                                      'kill-session -t ' + session.id,
                                      'display-message -p "Session identity changed; refresh and try again."'))
        if result.strip():
            raise TmuxError(result.strip())

    def current_client(self, explicit=None):
        """Resolve a switch target, refusing ambiguity rather than nesting."""
        current_session = None
        hinted_session = None
        try:
            tty = os.ttyname(0)
        except OSError:
            tty = None
        inside = tty is None and bool(self.env.get('TMUX') or self.env.get('TMUX_PANE'))
        # The pane TTY catches invocation inside tmux even after env -u TMUX.
        panes = self._run('list-panes', '-a', '-F', '#{pane_id} #{pane_tty} #{session_id}')
        if not panes.returncode:
            for line in panes.stdout.splitlines():
                fields = line.split(' ')
                if len(fields) == 3 and fields[0] == self.env.get('TMUX_PANE'):
                    hinted_session = fields[2]
                if len(fields) == 3 and tty is not None and fields[1] == tty:
                    inside = True
                    current_session = fields[2]
                    break
        if not inside:
            inside = enclosing_tmux()
        if inside and current_session is None:
            current_session = hinted_session
        if inside and self.env.get('TMUX'):
            expected_socket = self.env['TMUX'].rsplit(',', 2)[0]
            selected_socket = self._check(self._run('display-message', '-p', '#{socket_path}')).strip()
            if os.path.realpath(expected_socket) != os.path.realpath(selected_socket):
                raise TmuxError('Cannot switch between tmux servers from inside tmux; nesting is disabled.')
        if explicit:
            clients = self._check(self._run('list-clients', '-F', '#{client_name}')).splitlines()
            if explicit not in clients:
                raise TmuxError('The originating tmux client is no longer attached.')
            return explicit
        if not inside:
            return None
        if current_session is None and self.env.get('TMUX'):
            current_session = '$' + self.env['TMUX'].rsplit(',', 1)[-1]
        if current_session is None:
            raise TmuxError('Cannot identify the current tmux client; use the popup binding or --client.')
        clients = self._check(self._run('list-clients', '-t', current_session,
                                       '-F', '#{client_name}')).splitlines()
        if len(clients) > 1:
            raise TmuxError('More than one client is attached; use the popup binding or --client.')
        if not clients:
            raise TmuxError('No attached tmux client; nesting is disabled.')
        return clients[0]

    def connect(self, session_id: str, client: Optional[str] = None) -> int:
        client = self.current_client(client)
        if client:
            args = ['switch-client', '-t', session_id, '-c', client]
            self._check(self._run(*args))
            return 0
        try:
            socket = self._check(self._run('display-message', '-p', '#{socket_path}')).strip()
            environment = self.env.copy()
            environment.pop('TMUX', None)
            environment.pop('TMUX_PANE', None)
            result = subprocess.run(self.prefix + ['-S', socket, 'attach-session', '-t', session_id], env=environment)
        except OSError as error:
            raise TmuxError(str(error)) from error
        if result.returncode:
            raise TmuxError('Could not attach to session; it may have exited')
        return 0
