from pathlib import Path
from unittest.mock import patch
import subprocess

from tests.support import IsolatedTmux
from bellhop.tmux import Tmux, TmuxError, enclosing_tmux


class AdapterTests(IsolatedTmux):
    def setUp(self):
        super().setUp()
        self.adapter = Tmux(self.prefix, env=self.env)

    def test_empty_server(self):
        self.assertEqual(self.adapter.list_sessions(), [])

    def test_session_lifecycle(self):
        session = self.adapter.create_session('hello world', self.temp.name)
        self.assertEqual(session.name, 'hello world')
        self.assertEqual(session.windows, 1)
        self.assertEqual(session.clients, 0)
        self.assertEqual(self.adapter.list_sessions(), [session])
        self.assertEqual(self.tmux_command('display-message', '-p', '-t', session.id,
                                         '#{pane_current_path}').stdout.strip(), self.temp.name)
        self.adapter.kill_session(session)
        self.assertEqual(self.adapter.list_sessions(), [])

    def test_duplicate_name(self):
        first = self.adapter.create_session('same', self.temp.name)
        with self.assertRaises(TmuxError):
            self.adapter.create_session('same', self.temp.name)
        self.assertEqual(self.adapter.list_sessions(), [first])

    def test_invalid_names(self):
        for name in ['', '-bad', 'a.b', 'a:b', 'a\nb', 'a\tb', 'a' * 81]:
            with self.subTest(name=name), self.assertRaises(TmuxError):
                self.adapter.create_session(name, self.temp.name)
        self.assertEqual(self.adapter.list_sessions(), [])

    def test_disappeared_target(self):
        session = self.adapter.create_session('keeper', self.temp.name)
        from dataclasses import replace
        with self.assertRaises(TmuxError):
            self.adapter.kill_session(replace(session, id='$999'))
        self.assertEqual(len(self.adapter.list_sessions()), 1)

    def test_metacharacters_not_executed(self):
        name = '`touch SENTINEL`'
        session = self.adapter.create_session(name, self.temp.name)
        self.assertEqual(session.name, name)
        self.assertFalse((Path(self.temp.name) / 'SENTINEL').exists())

    def test_unexpected_tmux_error(self):
        failure = subprocess.CompletedProcess([], 1, '', 'permission denied')
        with patch('bellhop.tmux.subprocess.run', return_value=failure):
            with self.assertRaisesRegex(TmuxError, 'permission denied'):
                self.adapter.list_sessions()

    def test_default_name(self):
        self.assertEqual(self.adapter.default_name(), 'bellhop-1')
        self.adapter.create_session('bellhop-1', self.temp.name)
        self.assertEqual(self.adapter.default_name(), 'bellhop-2')

    def test_inside_context_never_attaches_even_without_clients(self):
        session = self.adapter.create_session('one', self.temp.name)
        self.adapter.env['TMUX_PANE'] = '%0'
        with self.assertRaisesRegex(TmuxError, 'client'):
            self.adapter.current_client()
        with patch('bellhop.tmux.subprocess.run', wraps=subprocess.run) as run:
            with self.assertRaises(TmuxError):
                self.adapter.connect(session.id)
        self.assertFalse(any('attach-session' in call.args[0] for call in run.call_args_list))

    def test_cross_server_never_nests(self):
        session = self.adapter.create_session('one', self.temp.name)
        self.adapter.env['TMUX'] = '/a/different/socket,123,0'
        with self.assertRaisesRegex(TmuxError, 'server'):
            self.adapter.connect(session.id)

    def test_unicode_names_preserve_callers_locale(self):
        self.env.pop('LC_ALL', None)
        self.adapter = Tmux(self.prefix, env=self.env)
        for name in ['界界', 'café']:
            with self.subTest(name=name):
                session = self.adapter.create_session(name, self.temp.name)
                self.assertEqual(session.name, name)
                self.assertIn(name, [s.name for s in self.adapter.list_sessions()])
        self.assertFalse('LC_ALL' in self.adapter.env)
        # Tmux permits names our creation prompt rejects; listing must handle them.
        self.assertEqual(self.tmux_command('new-session', '-d', '-s', 'a\u2028b').returncode, 0)
        self.assertIn('a\u2028b', [s.name for s in self.adapter.list_sessions()])

    def test_server_restart_cannot_delete_replacement(self):
        original = self.adapter.create_session('original', self.temp.name)
        self.stop_server()
        replacement = self.adapter.create_session('replacement', self.temp.name)
        self.assertEqual(original.id, replacement.id)
        with self.assertRaisesRegex(TmuxError, 'changed'):
            self.adapter.kill_session(original)
        self.assertEqual(self.adapter.list_sessions(), [replacement])

    def test_nonproc_ancestor_detection(self):
        results = [subprocess.CompletedProcess([], 0, '45 pts/2 python3\n', ''),
                   subprocess.CompletedProcess([], 0, '12 pts/2 /bin/sh\n', ''),
                   subprocess.CompletedProcess([], 0, '1 ? tmux: server\n', '')]
        with patch('bellhop.tmux.os.getpid', return_value=100), \
                patch('bellhop.tmux.os.getppid', return_value=45), \
                patch('bellhop.tmux.Path.read_text', side_effect=OSError), \
                patch('bellhop.tmux.shutil.which', return_value='/bin/ps'), \
                patch('bellhop.tmux.os.stat') as stat, \
                patch('bellhop.tmux.subprocess.run', side_effect=results):
            stat.return_value.st_rdev = 42
            self.assertTrue(enclosing_tmux())

    def test_ancestor_detection_stops_at_a_different_terminal(self):
        files = {
            '/proc/100/stat': '100 (python3) S 45 1 1 200 0',
            '/proc/45/stat': '45 (python3) S 12 1 1 100 0',
            '/proc/12/stat': '12 (tmux: server) S 1 1 1 0 0',
            '/proc/45/status': 'Name:\tpython3\nPPid:\t12\n',
            '/proc/12/status': 'Name:\ttmux: server\nPPid:\t1\n',
        }
        with patch('bellhop.tmux.os.getpid', return_value=100), \
                patch('bellhop.tmux.os.getppid', return_value=45), \
                patch('bellhop.tmux.Path.read_text', autospec=True,
                      side_effect=lambda path: files[str(path)]):
            self.assertFalse(enclosing_tmux())

    def test_nonproc_detection_accepts_abbreviated_bsd_tty_names(self):
        from types import SimpleNamespace
        results = [subprocess.CompletedProcess([], 0, '45 s012 python3\n', ''),
                   subprocess.CompletedProcess([], 0, '12 s012 sh\n', ''),
                   subprocess.CompletedProcess([], 0, '1 ?? tmux\n', '')]
        def stat(path):
            if path != '/dev/ttys012':
                raise FileNotFoundError(path)
            return SimpleNamespace(st_rdev=42)
        with patch('bellhop.tmux.os.getpid', return_value=100), \
                patch('bellhop.tmux.Path.read_text', side_effect=OSError), \
                patch('bellhop.tmux.shutil.which', return_value='/bin/ps'), \
                patch('bellhop.tmux.os.stat', side_effect=stat), \
                patch('bellhop.tmux.subprocess.run', side_effect=results):
            self.assertTrue(enclosing_tmux())
