#!/usr/bin/env python3
"""Install or remove Bellhop files."""

import argparse
import ast
import json
from pathlib import Path
import os
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path('share/bellhop/installed-files.json')


def source_files():
    files = [(ROOT / 'bin/bellhop', Path('bin/bellhop'), 0o755)]
    for module in sorted((ROOT / 'bellhop').rglob('*.py')):
        files.append((module, Path('lib/bellhop') / module.relative_to(ROOT), 0o644))
    files += [(ROOT / 'share/ssh-hook.sh', Path('share/bellhop/ssh-hook.sh'), 0o644),
              (ROOT / 'bellhop.tmux', Path('share/bellhop/bellhop.tmux'), 0o755)]
    files += [(ROOT / name, Path('share/doc/bellhop') / name, 0o644)
              for name in ('README.md', 'LICENSE', 'CONTRIBUTING.md')]
    return files


def installed_paths(location, files):
    manifest = location / MANIFEST
    names = json.loads(manifest.read_text()) if manifest.is_file() else [str(path) for _, path, _ in files]
    if not isinstance(names, list):
        raise ValueError('Invalid installed-file manifest')
    paths = []
    assets = {'bin/bellhop', 'share/bellhop/ssh-hook.sh', 'share/bellhop/bellhop.tmux',
              'share/doc/bellhop/README.md', 'share/doc/bellhop/LICENSE', 'share/doc/bellhop/CONTRIBUTING.md'}
    for name in names:
        if not isinstance(name, str):
            raise ValueError('Invalid installed-file manifest')
        relative = Path(name)
        module = relative.parts[:3] == ('lib', 'bellhop', 'bellhop') and relative.suffix == '.py'
        if relative.is_absolute() or '..' in relative.parts or not (name in assets or module):
            raise ValueError('Invalid path in installed-file manifest')
        paths.append(location / relative)
    return paths


def copy_file(source, destination, mode):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.bellhop-', dir=destination.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('--prefix', default=str(Path.home() / '.local'))
    parser.add_argument('--destdir', default='')
    args = parser.parse_args()
    prefix = Path(args.prefix).expanduser().absolute()
    location = Path(args.destdir).absolute() / prefix.relative_to('/') if args.destdir else prefix
    files = source_files()
    if args.action == 'install':
        required = [source for source, _, _ in files] + [ROOT / 'bellhop/__init__.py', ROOT / 'bellhop/__main__.py']
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        for source, destination, _ in files:
            if not source.is_file() or (source.suffix != '.py' and destination != Path('bin/bellhop')):
                continue
            try:
                tree = ast.parse(source.read_bytes(), filename=str(source))
            except SyntaxError as error:
                raise ValueError('Invalid Python source ' + str(source.relative_to(ROOT)) + ': ' + error.msg) from error
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [node.module or '']
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                for module in modules:
                    if module.startswith('bellhop.'):
                        path = ROOT.joinpath(*module.split('.'))
                        if not path.with_suffix('.py').is_file() and not (path / '__init__.py').is_file():
                            missing.append(str(path.with_suffix('.py').relative_to(ROOT)))
        if missing:
            raise ValueError('Missing required source files: ' + ', '.join(sorted(set(missing))))
        previous = installed_paths(location, files)
        for source, destination, mode in files:
            copy_file(source, location / destination, mode)
        current = {location / destination for _, destination, _ in files}
        for obsolete in set(previous) - current:
            obsolete.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            manifest_source = Path(tmp) / 'manifest.json'
            manifest_source.write_text(json.dumps([str(path) for _, path, _ in files], indent=2) + '\n')
            copy_file(manifest_source, location / MANIFEST, 0o644)
        print('Installed bellhop to ' + str(location / 'bin/bellhop'))
        print('SSH integration is opt-in: bellhop ssh enable')
    else:
        paths = installed_paths(location, files)
        if not args.destdir:
            sys.path.insert(0, str(ROOT))
            from bellhop.integration import disable_ssh
            for shell in ('bash', 'zsh'):
                disable_ssh(shell, Path.home())
        for destination in paths:
            destination.unlink(missing_ok=True)
        (location / MANIFEST).unlink(missing_ok=True)
        directories = {destination.parent for destination in paths}
        for destination in paths:
            if destination.suffix != '.py':
                continue
            cache = destination.parent / '__pycache__'
            if cache.is_dir():
                for path in cache.glob(destination.stem + '.*.pyc'):
                    path.unlink()
            directories.add(cache)
        directories.update([location / MANIFEST.parent, location / 'lib/bellhop'])
        for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        print('Uninstalled bellhop; unrelated files were kept.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print('bellhop installer: ' + str(error), file=sys.stderr)
        sys.exit(1)
