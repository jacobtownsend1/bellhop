from pathlib import Path
import os
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.support import IsolatedTmux, TerminalProcess


class SocketPathTests(unittest.TestCase):
    def test_socket_fits_macos_limit_with_long_default_temp_directory(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as root:
            long_temp = Path(root) / ('long-temp-' * 8)
            long_temp.mkdir()
            fixture = IsolatedTmux()
            with patch('tempfile.tempdir', str(long_temp)):
                fixture.setUp()
            try:
                created = fixture.tmux_command('new-session', '-d', '-s', 'probe')
                self.assertEqual(created.returncode, 0, created.stderr)
                socket = fixture.tmux_command('display-message', '-p', '#{socket_path}')
                self.assertEqual(socket.returncode, 0, socket.stderr)
                # macOS sockaddr_un.sun_path has 104 bytes, including the NUL.
                self.assertLess(len(socket.stdout.strip().encode()), 104)
            finally:
                fixture.tearDown()


class TerminalHarnessTests(unittest.TestCase):
    def test_records_terminal_modes_before_session_leader_exits(self):
        terminal = TerminalProcess([sys.executable, '-c', 'print("finished")'], os.environ.copy())
        try:
            terminal.wait(lambda: terminal.process.poll() is not None)
            self.assertEqual(terminal.process.returncode, 0)
            self.assertEqual(terminal.after, terminal.before)
        finally:
            terminal.close()

    def test_close_drains_shutdown_output(self):
        script = ('import os, signal, time\n'
                  'def finish(*args):\n'
                  '    os.write(1, b"x" * 200000)\n'
                  '    raise SystemExit(0)\n'
                  'signal.signal(signal.SIGTERM, finish)\n'
                  'print("ready", flush=True)\n'
                  'while True: time.sleep(1)\n')
        terminal = TerminalProcess([sys.executable, '-c', script], os.environ.copy())
        try:
            terminal.wait_text('ready')
            terminal.close()
            self.assertEqual(terminal.process.returncode, 0)
        finally:
            if terminal.process.poll() is None:
                terminal.close()

    def test_preserves_command_killed_by_sigkill(self):
        terminal = TerminalProcess(
            [sys.executable, '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)'],
            os.environ.copy())
        try:
            terminal.wait(lambda: terminal.process.poll() is not None)
            self.assertEqual(terminal.process.returncode, -signal.SIGKILL)
        finally:
            terminal.close()
