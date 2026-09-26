import copy
import io
import json
import os
import stat
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from teacher_planner.cli import main
from teacher_planner.client import NotionError
from teacher_planner.install import install
from teacher_planner.model import config
from teacher_planner.timetable import read_rows
from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


class ComciganCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / '.local'
        self.state_path = self.private / 'state.json'
        self.config_path = self.private / 'config.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()
        install(self.api, self.c, self.api.parent, self.state_path)
        self.config_path.write_text(json.dumps(self.c), encoding='utf-8')
        self.rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        self.snapshot = {'provider': 'comcigan', 'school_code': '12345', 'teacher_id': 7,
                         'teacher_name': '가상교사', 'week_start': '2026-09-28',
                         'source_updated': '2026-09-28T08:00:00+09:00', 'rows': copy.deepcopy(self.rows)}
        # The provider is a separate network boundary. These tests exercise the
        # real CLI, local journal and Notion sync without contacting either site.
        self.provider = types.ModuleType('teacher_planner.comcigan')
        self.provider.fetch_week = Mock(return_value=self.snapshot)
        self.provider.normalize = Mock(return_value=self.rows)
        self.addCleanup(patch.stopall)
        patch.dict('sys.modules', {'teacher_planner.comcigan': self.provider}).start()
        patch.dict('os.environ', {'COMCIGAN_SCHOOL_CODE': '', 'COMCIGAN_TEACHER_ID': ''}).start()
        # An empty integer environment default is not valid argparse input; unset
        # it unless an individual test explicitly verifies environment defaults.
        os.environ.pop('COMCIGAN_TEACHER_ID', None)
        self.out, self.err = io.StringIO(), io.StringIO()
        patch('sys.stdout', self.out).start()
        patch('sys.stderr', self.err).start()
        self.api.calls.clear()

    def args(self, command='comcigan-sync', extra=()):
        args = [command, '--school-code', '12345', '--teacher-id', '7']
        if command == 'comcigan-sync':
            args += ['--config', str(self.config_path), '--state', str(self.state_path)]
        return args + list(extra)

    def run_apply(self, extra=()):
        with patch('teacher_planner.cli.Client', return_value=self.api):
            return main(self.args(extra=['--apply', *extra]))

    def state(self):
        return json.loads(self.state_path.read_text())

    def test_week_writes_only_private_snapshot_atomically_with_mode_0600(self):
        output = self.private / 'week.json'
        output.write_text('previous', encoding='utf-8')
        output.chmod(0o644)
        with patch('teacher_planner.cli.Client', side_effect=AssertionError('Notion not allowed')):
            code = main(self.args('comcigan-week', ['--output', str(output), '--date', '2026-09-30']))
        self.assertEqual(0, code)
        self.assertEqual(self.snapshot, json.loads(output.read_text()))
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        self.assertEqual([], list(self.private.glob('.week.json.*.tmp')))
        self.provider.fetch_week.assert_called_once_with('12345', 7, requested=date(2026, 9, 30))
        self.provider.normalize.assert_not_called()
        self.assertNotIn(self.snapshot['teacher_name'], self.out.getvalue())

    def test_week_defaults_and_environment_ids(self):
        with patch.dict('os.environ', {'COMCIGAN_SCHOOL_CODE': '12345', 'COMCIGAN_TEACHER_ID': '7'}), patch('teacher_planner.cli.private_json') as save:
            self.assertEqual(0, main(['comcigan-week']))
        save.assert_called_once_with('.local/comcigan-week.json', self.snapshot)
        self.provider.fetch_week.assert_called_once_with('12345', 7, requested=None)

    def test_public_output_path_is_rejected(self):
        output = Path(self.temp.name) / 'public.json'
        self.assertEqual(1, main(self.args('comcigan-week', ['--output', str(output)])))
        self.assertFalse(output.exists())
        self.assertIn('.local/', self.err.getvalue())

    def test_preview_passes_mapping_and_times_without_notion_or_journal_changes(self):
        mapping = {'classes': {'1-1': '1학년 1반'}, 'subjects': {'영어A': '영어'}}
        periods = {'1': {'start': '09:00', 'end': '09:50'}}
        mapping_path, periods_path = self.private / 'mapping.json', self.private / 'times.json'
        mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
        periods_path.write_text(json.dumps(periods), encoding='utf-8')
        before = self.state_path.read_bytes()
        with patch('teacher_planner.cli.Client', side_effect=AssertionError('Notion not allowed')):
            self.assertEqual(0, main(self.args(extra=['--mapping', str(mapping_path), '--period-times', str(periods_path)])))
        self.provider.normalize.assert_called_once_with(self.snapshot, self.c, mapping=mapping, period_times=periods)
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertFalse(json.loads(self.out.getvalue())['applied'])

    def test_preview_reports_omitted_slots_without_cancelling(self):
        self.snapshot['omitted_slots'] = 3
        with patch('teacher_planner.cli.Client', side_effect=AssertionError('Notion not allowed')):
            self.assertEqual(0, main(self.args()))
        report = json.loads(self.out.getvalue())
        self.assertEqual(3, report['omitted_slots'])
        self.assertIn('누락 교시는 삭제하거나 휴강 처리하지 않습니다', report['behavior'])

    def test_apply_binds_source_records_check_and_preserves_created_journal_ids(self):
        self.assertEqual(0, self.run_apply())
        state = self.state()
        self.assertEqual(64, len(state['comcigan_source']))
        self.assertTrue(state['comcigan_last_checked_at'].endswith('+09:00'))
        self.assertEqual(2, sum(key.startswith('import:timetable:') for key in state['objects']))
        self.assertFalse(any(key.startswith('import:agenda:') for key in state['objects']))
        self.assertEqual([], self.api.pages(state['databases']['agenda']['data_source_id']))
        ds = state['databases']['timetable']['data_source_id']
        self.assertTrue(all(row['properties']['출처']['select']['name'] == '컴시간 어댑터' for row in self.api.pages(ds)))
        self.api.calls.clear()
        self.assertEqual(0, self.run_apply())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_file_import_also_keeps_lessons_out_of_agenda(self):
        args = ['import-timetable', str(ROOT / 'examples/timetable.csv'),
                '--config', str(self.config_path), '--state', str(self.state_path), '--apply']
        with patch('teacher_planner.cli.Client', return_value=self.api):
            self.assertEqual(0, main(args))
        state = self.state()
        timetable = state['databases']['timetable']['data_source_id']
        self.assertEqual(2, len(self.api.pages(timetable)))
        self.assertEqual([], self.api.pages(state['databases']['agenda']['data_source_id']))
        pages = [payload for method, path, payload in self.api.calls if method == 'POST' and path == '/pages']
        self.assertEqual(2, len(pages))
        self.assertTrue(all(page['parent']['data_source_id'] == timetable for page in pages))
        self.assertIn('교사 시간표 2개 행', self.out.getvalue())
        self.provider.fetch_week.assert_not_called()

    def test_teacher_number_reassignment_fails_before_writes(self):
        self.assertEqual(0, self.run_apply())
        before = self.state_path.read_bytes()
        self.snapshot['teacher_name'] = '다른가상교사'
        self.api.calls.clear()
        self.assertEqual(1, self.run_apply())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])
        self.assertEqual(before, self.state_path.read_bytes())
        self.assertIn('기존 컴시간 연결과 다릅니다', self.err.getvalue())

    def test_failed_apply_does_not_claim_successful_check(self):
        with patch('teacher_planner.cli.apply_changes', side_effect=NotionError('temporary failure', 503)):
            self.assertEqual(1, self.run_apply())
        self.assertIn('comcigan_source', self.state())
        self.assertNotIn('comcigan_last_checked_at', self.state())

    def test_invalid_identity_or_config_fails_before_sync_writes(self):
        for field, value in (('identity', 'another-connection'), ('config', {}), ('complete', False)):
            with self.subTest(field=field):
                state = self.state()
                old = state[field]
                state[field] = value
                self.state_path.write_text(json.dumps(state), encoding='utf-8')
                self.api.calls.clear()
                self.assertEqual(1, self.run_apply())
                self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])
                state[field] = old
                self.state_path.write_text(json.dumps(state), encoding='utf-8')

    def test_watch_validation_happens_before_fetch(self):
        for extra in (['--watch'], ['--apply', '--watch', '--interval', '299'],
                      ['--apply', '--watch', '--date', '2026-09-28']):
            with self.subTest(extra=extra):
                self.assertEqual(1, main(self.args(extra=extra)))
        self.provider.fetch_week.assert_not_called()

    def test_foreground_watch_sleeps_and_keyboard_interrupt_stops_cleanly(self):
        with patch('teacher_planner.cli.sleep', side_effect=KeyboardInterrupt) as wait:
            self.assertEqual(0, self.run_apply(['--watch', '--interval', '600']))
        wait.assert_called_once_with(600)
        self.assertEqual(1, self.provider.fetch_week.call_count)
        self.assertFalse(self.state_path.with_suffix('.lock').exists())

    def test_foreground_watch_refetches_current_week_and_stops_on_provider_error(self):
        self.provider.fetch_week.side_effect = [self.snapshot, ValueError('조회 실패')]
        with patch('teacher_planner.cli.sleep') as wait:
            self.assertEqual(1, self.run_apply(['--watch', '--interval', '300']))
        self.assertEqual(2, self.provider.fetch_week.call_count)
        self.assertTrue(all(call.kwargs['requested'] is None for call in self.provider.fetch_week.call_args_list))
        wait.assert_called_once_with(300)
        self.assertIn('조회 실패', self.err.getvalue())

    def test_provider_and_normalization_errors_never_write_notion(self):
        for function in (self.provider.fetch_week, self.provider.normalize):
            with self.subTest(function=function):
                function.side_effect = ValueError('검증 실패')
                self.api.calls.clear()
                self.assertEqual(1, self.run_apply())
                self.assertEqual([], self.api.calls)
                function.side_effect = None

    def test_missing_identifiers_are_rejected_before_fetch(self):
        self.assertEqual(1, main(['comcigan-week']))
        self.provider.fetch_week.assert_not_called()


if __name__ == '__main__':
    unittest.main()
