import compileall
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_cloud import BundleError, build, main


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'repo'
        self.root.mkdir()
        self.source = {
            'main.py': 'VALUE = 1\n', 'requirements.txt': 'Flask>=3.1,<4\n',
            'cloud/__init__.py': '', 'cloud/app.py': 'VALUE = 2\n',
            'teacher_planner/__init__.py': '', 'teacher_planner/model.py': 'VALUE = 3\n',
            'teacher_planner/blueprint.json': '{}\n', 'teacher_planner/dashboard.json': '{}\n',
        }
        for name, value in self.source.items():
            self.write(name, value)
        self.target = self.root / '.local/firebase/functions'

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_exact_allowlist_ignores_root_secrets_private_state_and_other_files(self):
        forbidden = ['.env', '.env.school-project', '.secret.local', 'env/settings.json',
                     '.local/state.json', 'private/student.json', 'config.example.json',
                     'cloud/web/app.js', 'cloud/tests/test_private.py',
                     'teacher_planner/private/config.json', 'teacher_planner/note.txt']
        original = {self.write(name, 'PRIVATE_SENTINEL'): b'PRIVATE_SENTINEL' for name in forbidden}
        from scripts import build_cloud
        real_read = build_cloud._bytes
        reads = []

        def source_only(path):
            self.assertNotIn(path, original)
            reads.append(path.relative_to(self.root).as_posix())
            return real_read(path)

        with patch.object(build_cloud, '_bytes', side_effect=source_only):
            self.assertEqual(len(self.source), build(self.root))
        self.assertEqual(set(self.source), set(reads))
        files = {path.relative_to(self.target).as_posix() for path in self.target.rglob('*') if path.is_file()}
        self.assertEqual(set(self.source), files)
        for path, value in original.items():
            self.assertEqual(value, path.read_bytes())

    def test_second_build_refreshes_sources_preserves_venv_and_public_parameters(self):
        build(self.root)
        environment = self.target / '.env.school-project'
        environment.write_text('NOTION_CLIENT_ID="12345678-1234-4123-8123-123456789abc"\n'
                               'PUBLIC_BASE_URL=https://school-project.web.app\n')
        binary = self.target / 'venv/bin/python'
        binary.parent.mkdir(parents=True)
        binary.symlink_to('/usr/bin/python3')
        saved = self.target / 'venv/pyvenv.cfg'
        saved.write_text('preserved venv')
        before = environment.read_bytes()
        self.assertTrue(compileall.compile_dir(self.target / 'cloud', quiet=1))
        self.assertTrue(compileall.compile_dir(self.target / 'teacher_planner', quiet=1))
        self.assertTrue(compileall.compile_file(self.target / 'main.py', quiet=1))
        self.write('cloud/app.py', 'VALUE = 4\n')
        build(self.root)
        self.assertEqual('VALUE = 4\n', (self.target / 'cloud/app.py').read_text())
        self.assertEqual(before, environment.read_bytes())
        self.assertTrue(binary.is_symlink())
        self.assertEqual('preserved venv', saved.read_text())
        self.assertEqual([], list(self.target.rglob('*.pyc')))

    def test_forbidden_target_files_stop_before_any_staged_file_is_changed(self):
        build(self.root)
        self.write('main.py', 'VALUE = 9\n')
        cases = ['.env', '.env.local', '.secret.local', '.env.example', 'unknown.json',
                 'cloud/private.py', 'cloud/data.json', 'teacher_planner/.env',
                 'cloud/__pycache__/private.cpython-312.pyc']
        for name in cases:
            with self.subTest(name=name):
                path = self.target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('PRIVATE_SENTINEL')
                with self.assertRaises(BundleError) as raised:
                    build(self.root)
                self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))
                self.assertEqual('VALUE = 1\n', (self.target / 'main.py').read_text())
                self.assertEqual('PRIVATE_SENTINEL', path.read_text())
                path.unlink()
        cache = self.target / 'cloud/__pycache__'
        if cache.exists():
            cache.rmdir()

    def test_target_parameter_file_rejects_secret_keys_and_invalid_syntax(self):
        build(self.root)
        path = self.target / '.env.school-project'
        invalid = ['NEIS_API_KEY=PRIVATE_SENTINEL\n', 'NOTION_CLIENT_SECRET=PRIVATE_SENTINEL\n',
                   'PUBLIC_BASE_URL=https://valid.web.app\nPUBLIC_BASE_URL=https://other.web.app',
                   'export PUBLIC_BASE_URL=https://valid.web.app',
                   'PUBLIC_BASE_URL="https://valid.web.app\nPRIVATE_SENTINEL"',
                   'PUBLIC_BASE_URL=https://user:PRIVATE_SENTINEL@valid.web.app',
                   'NOTION_CLIENT_ID=PRIVATE_SENTINEL', 'PUBLIC_BASE_URL=$(PRIVATE_SENTINEL)']
        for value in invalid:
            with self.subTest(index=invalid.index(value)):
                path.write_text(value)
                with self.assertRaises(BundleError) as raised:
                    build(self.root)
                self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))
                self.assertEqual(value, path.read_text())

    def test_source_file_or_package_symlinks_are_not_followed(self):
        outside = Path(self.temp.name) / 'outside'
        outside.write_text('PRIVATE_SENTINEL')
        source = self.root / 'main.py'
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaises(BundleError):
            build(self.root)
        self.assertFalse(self.target.exists())
        source.unlink()
        self.write('main.py', 'VALUE = 1\n')
        package = self.root / 'cloud'
        moved = self.root / 'cloud-original'
        package.rename(moved)
        package.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(BundleError):
            build(self.root)
        self.assertFalse(self.target.exists())

    def test_target_ancestor_file_venv_and_package_symlinks_fail_without_outside_writes(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        for relative in ('.local', '.local/firebase', '.local/firebase/functions'):
            with self.subTest(relative=relative):
                link = self.root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(BundleError):
                    build(self.root)
                self.assertEqual([], list(outside.iterdir()))
                link.unlink()
        build(self.root)
        for relative in ('main.py', 'cloud', 'venv'):
            with self.subTest(relative=relative):
                path = self.target / relative
                saved = None
                if path.exists():
                    saved = self.root / ('saved-' + relative)
                    path.rename(saved)
                path.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(BundleError):
                    build(self.root)
                self.assertEqual([], list(outside.iterdir()))
                path.unlink()
                if saved:
                    saved.rename(path)

    def test_symlink_repo_root_and_cache_entries_are_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(BundleError):
            build(alias)
        build(self.root)
        cache = Path(importlib.util.cache_from_source(str(self.target / 'main.py')))
        cache.parent.mkdir()
        cache.symlink_to(self.root / 'main.py')
        with self.assertRaises(BundleError):
            build(self.root)
        self.assertTrue(cache.is_symlink())

    def test_cli_reports_only_count_and_redacts_failure_details(self):
        output = io.StringIO()
        with patch('scripts.build_cloud.build', return_value=8), patch('sys.stdout', output):
            self.assertEqual(0, main([]))
        self.assertIn('8', output.getvalue())
        output = io.StringIO()
        with patch('scripts.build_cloud.build', side_effect=OSError('PRIVATE_SENTINEL')), patch('sys.stdout', output):
            self.assertEqual(1, main([]))
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())


if __name__ == '__main__':
    unittest.main()
