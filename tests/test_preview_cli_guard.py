"""Reviewed-layout guards must run before a Notion client or install journal."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teacher_planner.cli import main
from teacher_planner.preview_bundle import MODULES, create_bundle, digest
from test_preview_bundle import fragments


class PreviewCliGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config_path = self.root / 'config.json'
        self.bundle_path = self.root / 'reviewed-layout.json'
        self.state_path = self.root / 'state.json'
        self.config = {
            'title': '교무수첩 데스크', 'academic_year': 2026, 'timezone': 'Asia/Seoul',
            'teacher': '교사', 'subjects': ['교과'],
            'classes': [{'name': '예시 학급', 'grade': 1, 'class_name': '1', 'homeroom': False}],
            'modules': {key: False for key in MODULES}, 'forms': [], 'demo': False,
            'dashboard': {'periods': 7},
        }
        self.bundle = create_bundle()
        self.save()

    def save(self):
        self.config_path.write_text(json.dumps(self.config))
        self.bundle_path.write_text(json.dumps(self.bundle))

    def run_offline(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with (patch('teacher_planner.cli.Client', side_effect=AssertionError('unexpected Notion client')) as client,
              patch('teacher_planner.cli.install', side_effect=AssertionError('unexpected installation')) as install,
              redirect_stdout(out), redirect_stderr(err)):
            code = main(list(args))
        client.assert_not_called()
        install.assert_not_called()
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.state_path.with_suffix('.lock').exists())
        return code, out.getvalue(), err.getvalue()

    def install_args(self, apply=False):
        return ['install', '--config', str(self.config_path), '--reviewed-bundle', str(self.bundle_path),
                '--state', str(self.state_path)] + (['--apply'] if apply else [])

    def test_matching_plan_reports_reviewed_digest_and_pending_layout(self):
        code, out, err = self.run_offline('plan', '--config', str(self.config_path),
                                         '--reviewed-bundle', str(self.bundle_path))
        self.assertEqual((0, ''), (code, err))
        result = json.loads(out)
        self.assertEqual(self.bundle['bundle_digest'], result['reviewed_bundle_digest'])
        self.assertFalse(result['applied'])
        self.assertTrue(result['layout_verification_required'])

    def test_matching_install_without_apply_is_a_plan(self):
        code, out, err = self.run_offline(*self.install_args())
        self.assertEqual((0, ''), (code, err))
        self.assertEqual(self.bundle['bundle_digest'], json.loads(out)['reviewed_bundle_digest'])

    def test_changed_selection_stops_apply_before_client_and_journal(self):
        original = copy.deepcopy(self.config)
        for field in ('modules', 'forms', 'periods', 'homeroom', 'school_links'):
            self.config = copy.deepcopy(original)
            if field == 'modules': self.config['modules']['attendance'] = True
            if field == 'forms': self.config['forms'] = ['counseling']
            if field == 'periods': self.config['dashboard']['periods'] = 8
            if field == 'homeroom': self.config['classes'][0]['homeroom'] = True
            if field == 'school_links': self.config['dashboard']['links'] = {'neis': 'https://example.com/'}
            self.save()
            with self.subTest(field=field):
                code, out, err = self.run_offline(*self.install_args(apply=True))
                self.assertEqual(1, code)
                self.assertEqual('', out)
                self.assertIn('미리 본 배치와 설치 설정이 다릅니다', err)

    def test_default_implicit_forms_cannot_add_forms_to_reviewed_empty_choice(self):
        del self.config['forms']
        self.save()
        code, _, err = self.run_offline(*self.install_args(apply=True))
        self.assertEqual(1, code)
        self.assertIn('미리 본 배치와 설치 설정이 다릅니다', err)

    def test_rehashed_tampered_bundle_stops_apply_before_client(self):
        self.bundle['pages']['home']['full_width'] = False
        self.bundle['bundle_digest'] = digest({key: value for key, value in self.bundle.items() if key != 'bundle_digest'})
        self.save()
        code, out, err = self.run_offline(*self.install_args(apply=True))
        self.assertEqual(1, code)
        self.assertEqual('', out)
        self.assertIn('설계 지문이 일치하지 않습니다', err)

    def test_verify_preview_command_is_wired_and_offline(self):
        code, out, err = self.run_offline('verify-preview', '--bundle', str(self.bundle_path))
        self.assertEqual((0, ''), (code, err))
        report = json.loads(out)
        self.assertTrue(report['bundle_valid'])
        self.assertFalse(report['applied'])
        self.assertFalse(report['live_notion_verified'])

    def test_compile_preview_command_is_wired_and_offline(self):
        section_path = self.root / 'sections.json'
        section_path.write_text(json.dumps(fragments(self.bundle)))
        output = self.root / 'output'
        code, out, err = self.run_offline('compile-preview', '--bundle', str(self.bundle_path),
                                         '--sections', str(section_path), '--output-dir', str(output))
        self.assertEqual((0, ''), (code, err))
        report = json.loads(out)
        self.assertEqual(['home', 'classroom', 'teaching', 'planning'], report['compiled_pages'])
        self.assertFalse(report['applied'])
        self.assertFalse(report['layout_complete'])
        self.assertTrue((output / 'home.md').is_file())


if __name__ == '__main__':
    unittest.main()
