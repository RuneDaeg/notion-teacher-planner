import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teacher_planner.cli import main, verify_remote
from teacher_planner.client import NotionError
from teacher_planner.dashboard import layout_spec
from teacher_planner.install import Journal, block, fingerprint, install
from teacher_planner.model import academic_label, blueprint, config, rich
from test_planner import FakeNotion
from test_workspace_schema import filter_matches


ROOT = Path(__file__).resolve().parents[1]


def plain(items):
    return ''.join(item.get('text', {}).get('content', '') for item in items)


class AcademicYearTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.config_path = Path(self.temp.name) / 'config.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()

    def state(self):
        return json.loads(self.path.read_text())

    def install(self):
        return install(self.api, self.c, self.api.parent, self.path)

    def assert_labels(self, label):
        state = self.state()
        root = self.api.objects['/pages/' + state['objects']['root']['id']]
        self.assertEqual(label + ' · 교무수첩 데스크', plain(root['properties']['title']['title']))
        for page in ('home', 'classroom', 'teaching', 'planning'):
            intro = self.api.objects['/blocks/' + state['objects'][f'layout:{page}:intro']['id']]
            self.assertTrue(plain(intro['paragraph']['rich_text']).startswith(label + ' · 교사 · 영어\n'))

    def legacy_journal(self):
        """A pre-annual state with the exact old config/manifest fingerprint."""
        j = Journal(self.path, self.api)
        j.data.update(identity=self.api.identity, parent=self.api.parent, config=copy.deepcopy(self.c),
                      signature=fingerprint({'config': self.c, 'blueprint': blueprint(), 'dashboard': layout_spec()}))
        j.save()
        return j

    def legacy_root_payload(self):
        return {
            'parent': {'type': 'page_id', 'page_id': self.api.parent},
            'icon': {'type': 'emoji', 'emoji': '📒'},
            'properties': {'title': {'title': rich(f"{self.c['academic_year']}학년도 {self.c.get('semester', 1)}학기 · {self.c['title']}")}},
            'children': [block('paragraph', '오늘의 수업과 꼭 해야 할 일을 한곳에. 작은 기록으로 가볍게 시작하세요.')]}

    def test_new_notebook_is_annual_and_keeps_both_semester_progress_views(self):
        self.assertNotIn('semester', self.c)
        self.install()
        self.assert_labels('2026학년도')
        state = self.state()
        self.assertEqual(self.c, state['config'])
        lessons = state['databases']['lessons']['data_source_id']
        props = self.api.objects['/data_sources/' + lessons]['properties']
        self.assertEqual(['1학기', '2학기'], [o['name'] for o in props['학기']['select']['options']])
        for semester in (1, 2):
            for key in (f'view:semester_{semester}', f'teaching:semester_{semester}'):
                view = self.api.objects['/views/' + state['objects'][key]['id']]
                self.assertEqual(lessons, view['data_source_id'])
                for year, term, archived, expected in ((2026, semester, False, True),
                                                       (2026, 3 - semester, False, False),
                                                       (2025, semester, False, False),
                                                       (2026, semester, True, False)):
                    self.assertEqual(expected, filter_matches(view['filter'], {
                        '학년도': year, '학기': f'{term}학기', '보관': archived}))
        self.assertEqual([], verify_remote(self.api, state))

    def test_new_annual_install_retains_annual_label_after_root_rejection(self):
        request = self.api.request

        def reject_root(method, endpoint, payload=None):
            if method == 'POST' and endpoint == '/pages':
                raise NotionError('root rejected', 400)
            return request(method, endpoint, payload)

        with patch.object(self.api, 'request', side_effect=reject_root):
            with self.assertRaises(NotionError):
                self.install()
        state = self.state()
        self.assertEqual('2026학년도', state['academic_label'])
        self.assertNotIn('pending', state)
        self.assertNotIn('root', state['objects'])
        self.install()
        self.assert_labels('2026학년도')

    def test_explicit_legacy_semester_config_and_existing_root_are_reused(self):
        for semester in (1, 2):
            with self.subTest(semester=semester):
                self.path = Path(self.temp.name) / f'legacy-{semester}.json'
                self.c['semester'] = semester
                self.config_path.write_text(json.dumps(self.c))
                self.assertEqual(self.c, config(self.config_path))
                j = self.legacy_journal()
                root = j.create('root', '/pages', self.legacy_root_payload())
                signature = j.data['signature']
                self.install()
                self.assertEqual(root, self.state()['objects']['root'])
                self.assertEqual(signature, self.state()['signature'])
                self.assertEqual(self.c, self.state()['config'])
                self.assert_labels(f'2026학년도 {semester}학기')
                before = copy.deepcopy(self.api.objects)
                self.api.calls.clear()
                self.install()
                self.assertEqual(before, self.api.objects)
                self.assertTrue(all(method == 'GET' for method, _, _ in self.api.calls))

    def test_legacy_implicit_semester_retry_preserves_old_label_without_config_change(self):
        j = self.legacy_journal()
        root = j.create('root', '/pages', self.legacy_root_payload())
        signature = j.data['signature']
        self.install()
        state = self.state()
        self.assert_labels('2026학년도 1학기')
        self.assertEqual(root, state['objects']['root'])
        self.assertEqual(signature, state['signature'])
        self.assertEqual(self.c, state['config'])
        self.assertNotIn('semester', state['config'])
        self.assertEqual([], verify_remote(self.api, state))

    def test_legacy_pending_root_keeps_recovery_payload_and_reuses_recovered_root(self):
        j = self.legacy_journal()
        payload = self.legacy_root_payload()
        request = self.api.request
        created = []

        def create_then_timeout(method, endpoint, payload=None):
            result = request(method, endpoint, payload)
            created.append(result)
            raise NotionError('response lost')

        with patch.object(self.api, 'request', side_effect=create_then_timeout):
            with self.assertRaises(NotionError):
                j.create('root', '/pages', payload)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '불확실'):
            self.install()
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(payload, self.state()['pending']['payload'])
        with patch('teacher_planner.cli.Client', return_value=self.api), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(0, main(['recover', '--state', str(self.path), '--id', created[0]['id']]))
        self.install()
        self.assertEqual(created[0]['id'], self.state()['objects']['root']['id'])
        self.assert_labels('2026학년도 1학기')
        self.assertNotIn('semester', self.state()['config'])

    def test_removing_legacy_semester_still_fails_signature_gate_without_state_writes(self):
        self.c['semester'] = 2
        self.legacy_journal()
        before = self.path.read_bytes()
        del self.c['semester']
        with self.assertRaisesRegex(ValueError, '다릅니다'):
            self.install()
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(all(method == 'GET' for method, _, _ in self.api.calls))

    def test_plan_reports_annual_title_without_implicit_semester_and_preserves_explicit_value(self):
        for semester in (None, 1, 2):
            with self.subTest(semester=semester):
                c = dict(self.c)
                if semester is not None:
                    c['semester'] = semester
                self.config_path.write_text(json.dumps(c))
                output = io.StringIO()
                with patch('teacher_planner.cli.Client', side_effect=AssertionError('network')), patch('sys.stdout', new=output):
                    self.assertEqual(0, main(['plan', '--config', str(self.config_path)]))
                report = json.loads(output.getvalue())
                self.assertEqual(academic_label(c) + ' · 교무수첩 데스크', report['notebook_title'])
                if semester is None:
                    self.assertNotIn('semester', report)
                else:
                    self.assertEqual(semester, report['semester'])

    def test_invalid_explicit_legacy_semester_is_not_treated_as_annual(self):
        for value in (None, True, 0, 3, '1'):
            with self.subTest(value=value):
                self.config_path.write_text(json.dumps({**self.c, 'semester': value}))
                with self.assertRaisesRegex(ValueError, 'semester'):
                    config(self.config_path)


if __name__ == '__main__':
    unittest.main()
