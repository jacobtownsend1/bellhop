from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from bellhop import __version__

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_staged_install_and_repeated_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'stage'
            prefix = Path('/opt/bellhop space')
            env = dict(os.environ, HOME=tmp)
            (Path(tmp) / '.profile').write_text('# keep me\n')
            def run(action):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), action,
                                       '--prefix', str(prefix), '--destdir', str(stage)],
                                      env=env, capture_output=True, text=True)
            self.assertEqual(run('install').returncode, 0)
            executable = stage / prefix.relative_to('/') / 'bin/bellhop'
            result = subprocess.run([sys.executable, str(executable), '--version'], capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), 'bellhop ' + __version__)
            self.assertEqual((Path(tmp) / '.profile').read_text(), '# keep me\n')
            unrelated = executable.parent / 'unrelated'
            unrelated.write_text('keep')
            self.assertEqual(run('uninstall').returncode, 0)
            self.assertEqual(run('uninstall').returncode, 0)
            self.assertFalse(executable.exists())
            self.assertEqual(unrelated.read_text(), 'keep')

    def test_make_install_custom_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / 'custom prefix')
            result = subprocess.run(['make', 'install', 'PREFIX=' + prefix, 'PYTHON=' + sys.executable], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(prefix) / 'bin/bellhop').is_file())

    def test_malformed_hook_uninstall_reports_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            home.mkdir()
            profile = home / '.profile'
            text = '# >>> bellhop SSH lobby >>>\nkeep this\n'
            profile.write_text(text)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), 'uninstall',
                                     '--prefix', str(Path(tmp) / 'prefix')],
                                    env=dict(os.environ, HOME=str(home)), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('Malformed bellhop block', result.stderr)
            self.assertEqual(profile.read_text(), text)

    def checkout(self, directory):
        checkout = Path(directory) / 'checkout'
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        return checkout

    def test_install_missing_required_file_aborts_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = self.checkout(tmp)
            (checkout / 'share/ssh-hook.sh').unlink()
            prefix = Path(tmp) / 'prefix'
            result = subprocess.run([sys.executable, str(checkout / 'scripts/install.py'), 'install',
                                     '--prefix', str(prefix)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ssh-hook.sh', result.stderr)
            self.assertFalse(prefix.exists())

    def test_missing_referenced_module_aborts_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = self.checkout(tmp)
            (checkout / 'bellhop/tmux.py').unlink()
            prefix = Path(tmp) / 'prefix'
            result = subprocess.run([sys.executable, str(checkout / 'scripts/install.py'), 'install',
                                     '--prefix', str(prefix)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('bellhop/tmux.py', result.stderr)
            self.assertFalse(prefix.exists())

    def test_new_modules_install_and_uninstall_without_a_manual_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = self.checkout(tmp)
            module = checkout / 'bellhop/additional.py'
            module.write_text('VALUE = 42\n')
            stage = Path(tmp) / 'stage'
            command = [sys.executable, str(checkout / 'scripts/install.py'), 'install',
                       '--prefix', '/app', '--destdir', str(stage)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = stage / 'app/lib/bellhop/bellhop/additional.py'
            self.assertEqual(installed.read_text(), 'VALUE = 42\n')
            module.unlink()
            command[2] = 'uninstall'
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(installed.exists())

    def test_make_check_rejects_a_broken_extensionless_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = self.checkout(tmp)
            (checkout / 'bin/bellhop').write_text('def broken(\n')
            python = Path(tmp) / 'syntax-python'
            python.write_text('#!' + sys.executable + '\nimport os, sys\n'
                              'if sys.argv[1:3] == ["-m", "unittest"]: sys.exit(0)\n'
                              'os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n')
            python.chmod(0o755)
            result = subprocess.run(['make', 'check', 'PYTHON=' + str(python)], cwd=checkout,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SyntaxError', result.stderr)

    def test_uninstall_rejects_manifest_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'stage'
            manifest = stage / 'app/share/bellhop/installed-files.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('["../../keep-me"]')
            protected = Path(tmp) / 'keep-me'
            protected.write_text('keep')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/install.py'), 'uninstall',
                                     '--prefix', '/app', '--destdir', str(stage)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Invalid path', result.stderr)
            self.assertEqual(protected.read_text(), 'keep')
