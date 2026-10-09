"""Own a test terminal session until its command's modes are recorded."""

import os
import pickle
import signal
import subprocess
import sys
import termios


def main():
    # Caught handlers reset to defaults when the command execs. The supervisor
    # survives terminal signals so it can record the command's restored modes.
    for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM):
        signal.signal(sig, lambda *_: None)
    child = subprocess.Popen(sys.argv[2:])

    def resize(*_):
        try:
            os.kill(child.pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGWINCH, resize)
    result = child.wait()
    with os.fdopen(int(sys.argv[1]), 'wb') as snapshot:
        pickle.dump(termios.tcgetattr(0), snapshot)
    if result < 0:
        sig = -result
        if sig != signal.SIGKILL:
            signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)
    return result


if __name__ == '__main__':
    sys.exit(main())
