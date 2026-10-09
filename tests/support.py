import os
import errno
import fcntl
import pty
import pickle
import select
import signal
import socket as socket_module
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import uuid


class IsolatedTmux(unittest.TestCase):
    def setUp(self):
        # macOS's default temporary path can exceed the Unix socket limit
        # once tmux adds its UID directory and our unique socket name.
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath('/tmp'))
        self.prefix = ['tmux', '-L', 'bellhop-test-' + uuid.uuid4().hex, '-f', '/dev/null']
        self.env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                    'HOME': self.temp.name, 'SHELL': '/bin/sh',
                    'TERM': 'xterm-256color', 'LANG': 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8',
                    'TMUX_TMPDIR': self.temp.name}

    def tmux_command(self, *args):
        return subprocess.run(self.prefix + list(args), env=self.env,
                              text=True, capture_output=True)

    def stop_server(self):
        socket = self.tmux_command('display-message', '-p', '#{socket_path}')
        self.assertEqual(socket.returncode, 0, socket.stderr)
        result = self.tmux_command('kill-server')
        self.assertEqual(result.returncode, 0, result.stderr)
        # kill-server can return before the listener closes. The socket FILE
        # remains afterward, so wait for connection refusal, not file removal.
        deadline = time.monotonic() + 3
        while True:
            with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as probe:
                probe.settimeout(.1)
                try:
                    probe.connect(socket.stdout.strip())
                except OSError as error:
                    if error.errno in (errno.ECONNREFUSED, errno.ENOENT):
                        return
            if time.monotonic() >= deadline:
                self.fail('Test tmux server still listens after shutdown')
            time.sleep(.01)

    def tearDown(self):
        self.tmux_command('kill-server')
        self.temp.cleanup()


def wait_until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Condition timed out')


class TerminalProcess:
    """A real controlling terminal with bounded reads and deterministic cleanup."""
    def __init__(self, command, env, size=(24, 100)):
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', *size, 0, 0))
        self.before = termios.tcgetattr(self.slave)
        # BSD sets PENDIN while reprocessing input after ICANON is restored.
        # It is transient kernel state, not a terminal mode to restore.
        self.before[3] &= ~getattr(termios, 'PENDIN', 0)
        self.output = b''
        self._after = None
        self._closed = False
        self._snapshot, snapshot_writer = os.pipe()

        def terminal_session():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        # A shell normally owns the terminal session. Keep a supervisor in
        # that role so Darwin does not revoke the tty before we inspect it.
        helper = os.path.join(os.path.dirname(__file__), 'terminal_child.py')
        try:
            self.process = subprocess.Popen(
                [sys.executable, helper, str(snapshot_writer), *command],
                env=env, stdin=self.slave, stdout=self.slave, stderr=self.slave,
                preexec_fn=terminal_session, pass_fds=(snapshot_writer,))
        finally:
            os.close(snapshot_writer)

    def send(self, keys):
        os.write(self.master, keys)

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if select.select([self.master], [], [], .03)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    pass
        raise AssertionError('Terminal condition timed out: ' + repr(self.output[-1000:]))

    def wait_text(self, text):
        self.wait(lambda: text.encode() in self.output)

    @property
    def after(self):
        """Terminal modes after the command exits, before Darwin revokes it."""
        if self.process.poll() is None:
            raise RuntimeError('Command is still running')
        if self._after is None:
            with os.fdopen(self._snapshot, 'rb') as snapshot:
                self._snapshot = None
                self._after = pickle.load(snapshot)
                self._after[3] &= ~getattr(termios, 'PENDIN', 0)
        return self._after

    def close(self):
        if self._closed:
            return
        try:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    # Darwin drains terminal output during session-leader exit.
                    # Read it while waiting instead of blocking in Popen.wait.
                    self.wait(lambda: self.process.poll() is not None, timeout=2)
                except AssertionError:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.wait(lambda: self.process.poll() is not None, timeout=2)
        finally:
            os.close(self.master)
            os.close(self.slave)
            if self._snapshot is not None:
                os.close(self._snapshot)
                self._snapshot = None
            self._closed = True
