from pathlib import Path
import os
import re
import fcntl
import shlex
import signal
import struct
import subprocess
import sys
import termios

from tests.support import IsolatedTmux, TerminalProcess, wait_until
from bellhop.tmux import Tmux

ROOT = Path(__file__).resolve().parents[1]


class TerminalTests(IsolatedTmux):
    def setUp(self):
        super().setUp()
        self.adapter = Tmux(self.prefix, env=self.env)
        self.terminals = []

    def terminal(self, command, env=None):
        process = TerminalProcess(command, env or self.env)
        self.terminals.append(process)
        return process

    def lobby(self):
        return self.terminal([str(ROOT / 'bin/bellhop'), '--socket', self.prefix[2]])

    def tearDown(self):
        for terminal in self.terminals:
            terminal.close()
        super().tearDown()

    def test_escape_restores_terminal_and_creates_nothing(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        terminal.send(b'\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(termios.tcgetattr(terminal.slave), terminal.before)
        self.assertEqual(self.adapter.list_sessions(), [])

    def test_create_attach_and_disconnect(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        terminal.send(b'n\x15ndhjkl\r')
        terminal.wait(lambda: bool(self.adapter.list_sessions()) and
                      self.adapter.list_sessions()[0].clients == 1)
        session = self.adapter.list_sessions()[0]
        self.assertEqual(session.name, 'ndhjkl')
        # Detaching leaves the session's shell running.
        terminal.send(b'\x02d')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(self.adapter.list_sessions()[0].id, session.id)
        self.assertEqual(self.adapter.list_sessions()[0].clients, 0)

    def test_delete_cancel_then_confirm(self):
        self.adapter.create_session('alpha', self.temp.name)
        terminal = self.lobby()
        terminal.wait_text('alpha')
        terminal.send(b'd')
        terminal.wait_text('Kills all its programs')
        terminal.send(b'\x1b')
        terminal.wait(lambda: b'Esc exit' in terminal.output)
        self.assertEqual(len(self.adapter.list_sessions()), 1)
        terminal.send(b'dy')
        def deleted():
            # The server may be in the middle of exiting after its last session.
            from bellhop.tmux import TmuxError
            try:
                return self.adapter.list_sessions() == []
            except TmuxError:
                return False
        terminal.wait(deleted)
        terminal.send(b'\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(terminal.process.returncode, 0)

    def test_vim_navigation_attach(self):
        for name in ['alpha', 'beta', 'gamma']:
            self.adapter.create_session(name, self.temp.name)
        terminal = self.lobby()
        terminal.wait_text('alpha')
        terminal.send(b'lhjkj\r')
        terminal.wait(lambda: any(s.clients for s in self.adapter.list_sessions()))
        attached = next(s for s in self.adapter.list_sessions() if s.clients)
        self.assertEqual(attached.name, 'beta')
        terminal.send(b'\x02d')
        terminal.wait(lambda: terminal.process.poll() is not None)

    def attach(self, session):
        terminal = self.terminal(self.prefix + ['attach-session', '-t', session.id])
        terminal.wait(lambda: next(s.clients for s in self.adapter.list_sessions()
                                   if s.id == session.id) > 0)
        return terminal

    def invoke_in_pane(self, session, command):
        self.tmux_command('send-keys', '-t', session.id, '-l', shlex.join(command))
        self.tmux_command('send-keys', '-t', session.id, 'Enter')

    def test_inside_tmux_switches_without_nesting(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        second = self.adapter.create_session('beta', self.temp.name)
        terminal = self.attach(first)
        self.invoke_in_pane(first, [str(ROOT / 'bin/bellhop')])
        terminal.wait_text('bellhop')
        terminal.send(b'l\r')
        terminal.wait(lambda: next(s.clients for s in self.adapter.list_sessions()
                                   if s.id == second.id) == 1)
        self.assertEqual(len(self.adapter.list_sessions()), 2)

    def test_inside_detection_with_stripped_environment(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        second = self.adapter.create_session('beta', self.temp.name)
        terminal = self.attach(first)
        self.invoke_in_pane(first, ['env', '-u', 'TMUX', '-u', 'TMUX_PANE',
                                   str(ROOT / 'bin/bellhop'), '--socket', self.prefix[2]])
        terminal.wait_text('bellhop')
        terminal.send(b'l\r')
        terminal.wait(lambda: next(s.clients for s in self.adapter.list_sessions()
                                   if s.id == second.id) == 1)

    def test_shared_session_requires_explicit_client(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        self.adapter.create_session('beta', self.temp.name)
        one, two = self.attach(first), self.attach(first)
        one.wait(lambda: self.adapter.list_sessions()[0].clients == 2)
        self.invoke_in_pane(first, [str(ROOT / 'bin/bellhop')])
        one.wait_text('More than one client')
        self.assertEqual(self.adapter.list_sessions()[0].clients, 2)
        self.assertEqual(self.adapter.list_sessions()[1].clients, 0)

    def test_popup_targets_originating_client(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        second = self.adapter.create_session('beta', self.temp.name)
        one, two = self.attach(first), self.attach(first)
        one.wait(lambda: self.adapter.list_sessions()[0].clients == 2)
        # Load the real plugin, using this isolated server's socket.
        socket = self.tmux_command('display-message', '-p', '#{socket_path}').stdout.strip()
        env = dict(self.env, TMUX=socket + ',1,0')
        result = subprocess.run(['sh', str(ROOT / 'bellhop.tmux')], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        one.send(b'\x02B')
        one.wait_text('tmux lobby')
        one.send(b'l\r')
        one.wait(lambda: next(s.clients for s in self.adapter.list_sessions()
                             if s.id == second.id) == 1)
        clients = self.tmux_command('list-clients', '-F', '#{client_name} #{session_name}').stdout
        self.assertIn(os.ttyname(one.slave) + ' beta', clients)
        self.assertIn(os.ttyname(two.slave) + ' alpha', clients)

    def test_popup_escape_preserves_session(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        terminal = self.attach(first)
        self.tmux_command('bind-key', 'B', 'run-shell',
                          'tmux display-popup -c #{q:client_name} -E -d #{q:pane_current_path} ' +
                          shlex.quote(str(ROOT / 'bin/bellhop')) + ' --client #{q:client_name}')
        terminal.send(b'\x02B')
        terminal.wait_text('tmux lobby')
        terminal.send(b'\x1b')
        terminal.wait(lambda: 'python' not in self.tmux_command('list-panes', '-a', '-F',
                                                             '#{pane_current_command}').stdout)
        self.assertEqual(self.adapter.list_sessions()[0].clients, 1)
        self.assertEqual(self.adapter.list_sessions()[0].windows, 1)

    def test_plugin_quoted_command_and_directory(self):
        directory = Path(self.temp.name) / "work ' space"
        directory.mkdir()
        wrapper = Path(self.temp.name) / "bellhop's wrapper"
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(ROOT / 'bin/bellhop')) + ' "$@"\n')
        wrapper.chmod(0o755)
        first = self.adapter.create_session('alpha', str(directory))
        terminal = self.attach(first)
        self.tmux_command('set-option', '-g', '@bellhop-command', str(wrapper))
        self.tmux_command('set-option', '-g', '@bellhop-key', 'H')
        socket = self.tmux_command('display-message', '-p', '#{socket_path}').stdout.strip()
        result = subprocess.run(['sh', str(ROOT / 'bellhop.tmux')],
                                env=dict(self.env, TMUX=socket + ',1,0'),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        terminal.send(b'\x02H')
        terminal.wait_text('tmux lobby')
        terminal.send(b'n\r')
        terminal.wait(lambda: len(self.adapter.list_sessions()) == 2)
        created = next(s for s in self.adapter.list_sessions() if s.name == 'bellhop-1')
        self.assertEqual(self.tmux_command('display-message', '-p', '-t', created.id,
                                         '#{pane_current_path}').stdout.strip(), str(directory))

    def test_running_job_survives_client_disconnect(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        terminal.send(b'n\x15job\r')
        terminal.wait(lambda: bool(self.adapter.list_sessions()) and self.adapter.list_sessions()[0].clients == 1)
        first = self.adapter.list_sessions()[0]
        sentinel = Path(self.temp.name) / 'finished'
        self.invoke_in_pane(first, ['sh', '-c', 'sleep 0.3; printf done > ' + shlex.quote(str(sentinel))])
        terminal.close()
        self.terminals.remove(terminal)
        wait_until(sentinel.exists)
        self.assertEqual(sentinel.read_text(), 'done')
        self.assertEqual(self.adapter.list_sessions()[0].clients, 0)

    def test_resize_and_escape(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        fcntl.ioctl(terminal.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 3, 15, 0, 0))
        os.kill(terminal.process.pid, signal.SIGWINCH)
        terminal.wait_text('Resize')
        terminal.send(b'\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(terminal.process.returncode, 0)

    def test_window_fallback_returns_to_previous_pane(self):
        first = self.adapter.create_session('alpha', self.temp.name)
        terminal = self.attach(first)
        self.tmux_command('bind-key', 'B', 'run-shell',
                          'tmux new-window -t #{q:session_id}: -n bellhop -c #{q:pane_current_path} ' +
                          shlex.quote(str(ROOT / 'bin/bellhop')) + ' --client #{q:client_name}')
        terminal.send(b'\x02B')
        terminal.wait_text('tmux lobby')
        self.assertEqual(self.adapter.list_sessions()[0].windows, 2)
        terminal.send(b'\x1b')
        terminal.wait(lambda: self.adapter.list_sessions()[0].windows == 1)
        self.assertEqual(self.adapter.list_sessions()[0].clients, 1)

    def test_cross_server_stripped_environment_refuses_to_nest(self):
        import uuid
        other_prefix = ['tmux', '-L', 'bellhop-test-' + uuid.uuid4().hex, '-f', '/dev/null']
        other = Tmux(other_prefix, env=self.env)
        try:
            other.create_session('other', self.temp.name)
            first = self.adapter.create_session('alpha', self.temp.name)
            terminal = self.attach(first)
            self.invoke_in_pane(first, ['env', '-u', 'TMUX', '-u', 'TMUX_PANE',
                                       str(ROOT / 'bin/bellhop'), '--socket', other_prefix[2]])
            terminal.wait_text('Cannot identify the current tmux client')
            self.assertEqual(other.list_sessions()[0].clients, 0)
            self.assertEqual(self.adapter.list_sessions()[0].clients, 1)
        finally:
            subprocess.run(other_prefix + ['kill-server'], env=self.env,
                           capture_output=True)

    def test_attach_preserves_unicode_terminal(self):
        self.env.pop('LC_ALL', None)
        session = self.adapter.create_session('unicode', self.temp.name)
        terminal = self.lobby()
        terminal.wait_text('tmux lobby')
        terminal.send(b'\r')
        terminal.wait(lambda: self.adapter.list_sessions()[0].clients == 1)
        self.invoke_in_pane(session, ['printf', '界界'])
        terminal.wait_text('界界')

    def test_confirmation_survives_server_replacement_safely(self):
        original = self.adapter.create_session('original', self.temp.name)
        terminal = self.lobby()
        terminal.wait_text('original')
        terminal.send(b'd')
        terminal.wait_text('Kills all its programs')
        self.stop_server()
        replacement = self.adapter.create_session('replacement', self.temp.name)
        self.assertEqual(original.id, replacement.id)
        terminal.send(b'y')
        terminal.wait_text('identity changed')
        self.assertEqual(self.adapter.list_sessions(), [replacement])
        terminal.send(b'\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)

    def test_name_prompt_enables_cursor_and_hides_it_on_cancel(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        before = len(terminal.output)
        terminal.send(b'n')
        terminal.wait(lambda: b'\x1b[?25h' in terminal.output[before:])
        before = len(terminal.output)
        terminal.send(b'\x1b')
        terminal.wait(lambda: b'\x1b[?25l' in terminal.output[before:])
        terminal.send(b'\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(termios.tcgetattr(terminal.slave), terminal.before)

    def test_color_and_no_color_terminal_modes(self):
        for disabled in (False, True):
            with self.subTest(no_color=disabled):
                env = dict(self.env)
                if disabled:
                    env['NO_COLOR'] = '1'
                terminal = self.terminal([str(ROOT / 'bin/bellhop'), '--socket', self.prefix[2]], env)
                terminal.wait_text('No sessions yet')
                cyan = re.search(rb'\x1b\[(?:[0-9]+;)*36(?:;[0-9]+)*m|\x1b\[(?:[0-9]+;)*38;5;6m', terminal.output)
                self.assertEqual(bool(cyan), not disabled)
                terminal.send(b'\x1b')
                terminal.wait(lambda: terminal.process.poll() is not None)
                self.assertEqual(terminal.process.returncode, 0)

    def test_name_cursor_recovers_after_shrink_and_restore(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        before = len(terminal.output)
        terminal.send(b'n')
        terminal.wait(lambda: b'\x1b[?25h' in terminal.output[before:])
        before = len(terminal.output)
        fcntl.ioctl(terminal.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 3, 15, 0, 0))
        os.kill(terminal.process.pid, signal.SIGWINCH)
        terminal.wait(lambda: b'\x1b[?25l' in terminal.output[before:])
        before = len(terminal.output)
        fcntl.ioctl(terminal.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        os.kill(terminal.process.pid, signal.SIGWINCH)
        terminal.wait(lambda: b'\x1b[?25h' in terminal.output[before:])
        terminal.wait(lambda: b'bellhop-1' in terminal.output[before:])
        terminal.send(b'\x1b\x1b')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(termios.tcgetattr(terminal.slave), terminal.before)

    def test_separate_terminal_launched_from_tmux_opens_lobby(self):
        first = self.adapter.create_session('origin', self.temp.name)
        terminal = self.attach(first)
        script = (
            'import os, sys; sys.path.insert(0, ' + repr(str(ROOT)) + '); '
            'from tests.support import TerminalProcess; '
            'env=dict(os.environ); env.pop("TMUX",None); env.pop("TMUX_PANE",None); '
            't=TerminalProcess(' + repr([str(ROOT / 'bin/bellhop'), '--socket', self.prefix[2]]) + ',env); '
            't.wait_text("tmux lobby"); t.send(b"\\x1b"); '
            't.wait(lambda:t.process.poll() is not None); '
            'assert t.process.returncode==0; t.close(); print("SEPARATE"+"_TERMINAL"+"_OK")'
        )
        self.invoke_in_pane(first, [sys.executable, '-c', script])
        terminal.wait_text('SEPARATE_TERMINAL_OK')
        self.assertEqual(self.adapter.list_sessions()[0].clients, 1)

    def test_installed_plugin_resolves_launcher_outside_path(self):
        prefix = Path(self.temp.name) / 'installed prefix'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), 'install',
                                 '--prefix', str(prefix)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.adapter.create_session('origin', self.temp.name)
        terminal = self.attach(first)
        socket = self.tmux_command('display-message', '-p', '#{socket_path}').stdout.strip()
        result = subprocess.run(['sh', str(prefix / 'share/bellhop/bellhop.tmux')],
                                env=dict(self.env, TMUX=socket + ',1,0'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        binding = self.tmux_command('list-keys', '-T', 'prefix', 'B').stdout
        self.assertIn(str(prefix), binding)
        terminal.send(b'\x02B')
        terminal.wait_text('tmux lobby')
        terminal.send(b'\x1b')

    def test_ctrl_c_exits_name_entry_and_restores_terminal(self):
        terminal = self.lobby()
        terminal.wait_text('No sessions yet')
        before = len(terminal.output)
        terminal.send(b'n')
        terminal.wait(lambda: b'\x1b[?25h' in terminal.output[before:])
        terminal.send(b'\x03')
        terminal.wait(lambda: terminal.process.poll() is not None)
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(termios.tcgetattr(terminal.slave), terminal.before)

    def test_separate_terminal_with_inherited_environment_attaches_its_own_client(self):
        first = self.adapter.create_session('a-origin', self.temp.name)
        second = self.adapter.create_session('b-target', self.temp.name)
        terminal = self.attach(first)
        script = '\n'.join([
            'import os, sys',
            'sys.path.insert(0, ' + repr(str(ROOT)) + ')',
            'from tests.support import TerminalProcess',
            'from bellhop.tmux import Tmux',
            'env=dict(os.environ)',
            'adapter=Tmux(' + repr(self.prefix) + ',env=env)',
            't=TerminalProcess(' + repr([str(ROOT / 'bin/bellhop')]) + ',env)',
            'try:',
            '    t.wait_text("tmux lobby")',
            '    t.send(b"l\\r")',
            '    t.wait(lambda: all(s.clients == 1 for s in adapter.list_sessions()))',
            '    assert t.process.poll() is None',
            '    t.send(b"\\x02d")',
            '    t.wait(lambda: t.process.poll() is not None)',
            '    assert t.process.returncode == 0',
            'finally:',
            '    t.close()',
            'print("INHERITED"+"_ENV"+"_OK")',
        ])
        self.invoke_in_pane(first, [sys.executable, '-c', script])
        terminal.wait_text('INHERITED_ENV_OK')
        sessions = self.adapter.list_sessions()
        self.assertEqual(next(s.clients for s in sessions if s.id == first.id), 1)
        self.assertEqual(next(s.clients for s in sessions if s.id == second.id), 0)
