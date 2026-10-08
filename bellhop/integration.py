"""Configure the optional SSH lobby."""

from pathlib import Path
import hashlib
import os
import shlex
import shutil
import stat
import tempfile
import time

START = '# >>> bellhop SSH lobby >>>'
END = '# <<< bellhop SSH lobby <<<'


class IntegrationError(ValueError):
    """Startup configuration cannot be edited safely."""


def _read_profile(path):
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeError as error:
        raise IntegrationError(str(path) + ': login file is not UTF-8; convert it before changing the SSH hook.') from error


def startup_files(shell, home):
    home = Path(home)
    if shell == 'bash':
        return [home / name for name in ('.bash_profile', '.bash_login', '.profile')]
    if shell == 'zsh':
        directory = Path(os.environ.get('ZDOTDIR') or home).expanduser()
        if not directory.is_absolute():
            directory = home / directory
        return [directory / '.zlogin', directory / '.zprofile']
    raise IntegrationError('Supported login shells: bash and zsh; use --shell to choose.')


def strip_block(text):
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == START]
    ends = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == END]
    if not starts and not ends:
        return text
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise IntegrationError('Malformed bellhop block; fix its markers before changing this file.')
    return ''.join(lines[:starts[0]] + lines[ends[0] + 1:])


def _write(path, text, home):
    # Resolve symlinks so dotfile managers keep their link intact.
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = _read_profile(destination) if destination.exists() else ''
    if original == text:
        return
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o600
    backup_dir = None
    if destination.exists():
        state = Path(os.environ.get('XDG_STATE_HOME') or Path(home) / '.local/state')
        backup_dir = state / 'bellhop/backups'
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = hashlib.sha256(os.fsencode(destination)).hexdigest()[:16]
        backup_prefix = destination.name + '-' + identity + '-'
        backup = backup_dir / (backup_prefix + str(time.time_ns()) + '.bak')
        shutil.copy2(destination, backup)
        backup.chmod(0o600)
    descriptor, temporary = tempfile.mkstemp(prefix='.bellhop-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
        if backup_dir is not None:
            backups = sorted(p for p in backup_dir.iterdir()
                             if p.name.startswith(backup_prefix) and p.name.endswith('.bak'))
            for old in backups[:-5]:
                old.unlink(missing_ok=True)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def enable_ssh(shell, home, executable):
    executable = Path(executable).resolve()
    root = executable.parent.parent
    hook = root / 'share' / 'ssh-hook.sh'
    if not hook.is_file():
        hook = root / 'share' / 'bellhop' / 'ssh-hook.sh'
    if not executable.is_file() or not os.access(executable, os.X_OK) or not hook.is_file():
        raise IntegrationError('Install bellhop before enabling its SSH hook.')
    candidates = startup_files(shell, home)
    path = candidates[0] if shell == 'zsh' else next((p for p in candidates if p.is_file()), candidates[-1])
    original = _read_profile(path) if path.exists() else ''
    body = strip_block(original)
    if body and not body.endswith('\n'):
        body += '\n'
    block = (START + '\n' +
             '_bellhop_executable=' + shlex.quote(str(executable)) + '\n' +
             'if [ -r ' + shlex.quote(str(hook)) + ' ]; then\n' +
             '    . ' + shlex.quote(str(hook)) + '\n' +
             'fi\n' +
             'unset _bellhop_executable\n' + END + '\n')
    legacy = None
    if shell == 'zsh' and candidates[1].is_file():
        previous = _read_profile(candidates[1])
        cleaned = strip_block(previous)
        if cleaned != previous:
            legacy = cleaned
    _write(path, body + block, home)
    if legacy is not None:
        _write(candidates[1], legacy, home)
    return path


def disable_ssh(shell, home):
    for path in startup_files(shell, home):
        if path.is_file():
            original = _read_profile(path)
            cleaned = strip_block(original)
            if cleaned != original:
                _write(path, cleaned, home)
