import os
import errno
import fcntl
import pty
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
        self.temp = tempfile.TemporaryDirectory()
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
        self.output = b''

        def terminal_session():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.process = subprocess.Popen(command, env=env, stdin=self.slave,
                                        stdout=self.slave, stderr=self.slave,
                                        preexec_fn=terminal_session)

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

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
        os.close(self.master)
        os.close(self.slave)
