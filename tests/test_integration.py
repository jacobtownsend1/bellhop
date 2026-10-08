from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.support import TerminalProcess
from bellhop.integration import enable_ssh, disable_ssh, IntegrationError, START, END

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.executable = ROOT / 'bin/bellhop'
        self.addCleanup(self.temp.cleanup)
        self.state = self.home / '.state'
        setting = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.state)})
        setting.start()
        self.addCleanup(setting.stop)

    def test_bash_login_precedence(self):
        (self.home / '.profile').write_text('# profile\n')
        (self.home / '.bash_login').write_text('# login\n')
        (self.home / '.bash_profile').write_text('# bash\n')
        path = enable_ssh('bash', self.home, self.executable)
        self.assertEqual(path, self.home / '.bash_profile')
        self.assertEqual((self.home / '.profile').read_text(), '# profile\n')

    def test_zsh_zdotdir(self):
        directory = self.home / 'zsh'
        directory.mkdir()
        with patch.dict(os.environ, {'ZDOTDIR': str(directory)}):
            self.assertEqual(enable_ssh('zsh', self.home, self.executable), directory / '.zlogin')

    def test_backup_preserved_and_idempotent(self):
        path = self.home / '.profile'
        original = '# user content\nexport KEEP=1'  # no final newline
        path.write_text(original)
        path.chmod(0o640)
        enable_ssh('bash', self.home, self.executable)
        first = path.read_text()
        enable_ssh('bash', self.home, self.executable)
        self.assertEqual(path.read_text(), first)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertTrue(any(p.read_text() == original for p in (self.state / 'bellhop/backups').glob('*.bak')))
        path.write_text(first + '# added later\n')
        disable_ssh('bash', self.home)
        self.assertIn('# added later\n', path.read_text())
        self.assertIn(original, path.read_text())
        after = path.read_text()
        disable_ssh('bash', self.home)
        self.assertEqual(path.read_text(), after)

    def test_malformed_owned_block(self):
        path = self.home / '.profile'
        original = '# >>> bellhop SSH lobby >>>\nimportant stuff\n'
        path.write_text(original)
        with self.assertRaises(IntegrationError):
            disable_ssh('bash', self.home)
        self.assertEqual(path.read_text(), original)

    def test_disable_finds_old_bash_startup_file(self):
        enable_ssh('bash', self.home, self.executable)
        (self.home / '.bash_profile').write_text('# newer file\n')
        disable_ssh('bash', self.home)
        self.assertNotIn('>>> bellhop', (self.home / '.profile').read_text())

    def test_symlink_startup_file_is_preserved(self):
        real = self.home / 'dotprofile'
        real.write_text('# keep\n')
        (self.home / '.profile').symlink_to(real)
        enable_ssh('bash', self.home, self.executable)
        self.assertTrue((self.home / '.profile').is_symlink())
        self.assertIn('>>> bellhop', real.read_text())

    def test_non_utf8_profile_cli_reports_error_without_changes(self):
        profile = self.home / '.profile'
        content = b'# legacy profile\n\xff\n'
        profile.write_bytes(content)
        env = dict(os.environ, HOME=str(self.home), SHELL='/bin/bash')
        for action in ('enable', 'disable'):
            result = subprocess.run([sys.executable, str(self.executable), 'ssh', action,
                                     '--shell', 'bash'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('UTF-8', result.stderr)
            self.assertEqual(profile.read_bytes(), content)

    def test_backups_are_bounded_and_kept_outside_dotfiles(self):
        dotfiles = self.home / 'dotfiles'
        dotfiles.mkdir()
        real = dotfiles / 'profile'
        (self.home / '.profile').symlink_to(real)
        for number in range(8):
            real.write_text('# edit ' + str(number) + '\n')
            enable_ssh('bash', self.home, self.executable)
        backups = list((self.state / 'bellhop/backups').glob('*.bak'))
        self.assertEqual(len(backups), 5)
        self.assertTrue(any(p.read_text() == '# edit 7\n' for p in backups))
        self.assertEqual(list(dotfiles.iterdir()), [real])
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in backups))

    def test_zsh_migrates_old_profile_hook_to_zlogin(self):
        profile = self.home / '.zprofile'
        profile.write_text('# my profile\n' + START + '\necho old-hook\n' + END + '\n')
        with patch.dict(os.environ, {'ZDOTDIR': str(self.home)}):
            result = enable_ssh('zsh', self.home, self.executable)
            self.assertEqual(result, self.home / '.zlogin')
            self.assertEqual(profile.read_text(), '# my profile\n')
            disable_ssh('zsh', self.home)
            self.assertNotIn(START, result.read_text())


class HookTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('zsh'), 'zsh is not installed')
    def test_zsh_hook_runs_after_zshrc_sets_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            fake_bin = home / 'bin'
            fake_bin.mkdir()
            marker = home / 'tmux-invoked'
            tmux = fake_bin / 'tmux'
            tmux.write_text('#!/bin/sh\nprintf called >> ' + shlex.quote(str(marker)) +
                            '\nexec ' + shlex.quote(shutil.which('tmux')) + ' "$@"\n')
            tmux.chmod(0o755)
            (home / '.zshrc').write_text('export PATH=' + shlex.quote(str(fake_bin)) + ':$PATH\n')
            with patch.dict(os.environ, {'ZDOTDIR': str(home)}):
                enable_ssh('zsh', home, ROOT / 'bin/bellhop')
            env = {'HOME': tmp, 'ZDOTDIR': tmp, 'PATH': os.environ['PATH'],
                   'SHELL': shutil.which('zsh'), 'TERM': 'xterm-256color',
                   'LANG': 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8',
                   'SSH_CONNECTION': 'test', 'TMUX_TMPDIR': tmp}
            terminal = TerminalProcess([shutil.which('zsh'), '-d', '-ilc', 'exit 0'], env)
            try:
                terminal.wait_text('No sessions yet')
                self.assertTrue(marker.exists())
                terminal.send(b'\x1b')
                terminal.wait(lambda: terminal.process.poll() is not None)
                self.assertEqual(terminal.process.returncode, 0)
            finally:
                terminal.close()

    def test_bash_hook_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            marker = home / 'invoked'
            command = home / 'fake bellhop'
            command.write_text('#!/bin/sh\nprintf invoked >> ' + shlex.quote(str(marker)) + '\n')
            command.chmod(0o755)
            hook = ROOT / 'share/ssh-hook.sh'
            script = '_bellhop_executable=' + shlex.quote(str(command)) + '; . ' + shlex.quote(str(hook)) + '; exit 0'
            base = dict(os.environ, HOME=tmp, TERM='xterm-256color', SSH_CONNECTION='test')
            for variable in ['TMUX', 'TMUX_PANE', 'BELLHOP_DISABLE', 'SSH_TTY', 'ZDOTDIR']:
                base.pop(variable, None)
            cases = [(['--noprofile', '--norc', '-ilc'], {}, True),
                     (['--noprofile', '--norc', '-ic'], {}, False),
                     (['--noprofile', '--norc', '-lc'], {}, False),
                     (['--noprofile', '--norc', '-ilc'], {'SSH_CONNECTION': ''}, False),
                     (['--noprofile', '--norc', '-ilc'], {'TMUX': '/tmp/x,1,0'}, False),
                     (['--noprofile', '--norc', '-ilc'], {'TMUX_PANE': '%1'}, False),
                     (['--noprofile', '--norc', '-ilc'], {'BELLHOP_DISABLE': '1'}, False),
                     (['--noprofile', '--norc', '-ilc'], {'TERM': 'dumb'}, False)]
            for flags, updates, expected in cases:
                with self.subTest(flags=flags, updates=updates):
                    marker.unlink(missing_ok=True)
                    terminal = TerminalProcess(['bash'] + flags + [script], dict(base, **updates))
                    try:
                        terminal.wait(lambda: terminal.process.poll() is not None)
                        self.assertEqual(terminal.process.returncode, 0)
                        self.assertEqual(marker.exists(), expected)
                    finally:
                        terminal.close()
            # A missing program also leaves a functioning login shell.
            command.unlink()
            terminal = TerminalProcess(['bash', '--noprofile', '--norc', '-ilc', script], base)
            try:
                terminal.wait(lambda: terminal.process.poll() is not None)
                self.assertEqual(terminal.process.returncode, 0)
            finally:
                terminal.close()
