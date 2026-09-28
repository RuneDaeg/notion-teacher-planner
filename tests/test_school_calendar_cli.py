import copy
import io
import json
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from teacher_planner.cli import main
from teacher_planner.client import NotionError
from teacher_planner.install import fingerprint, install
from teacher_planner.model import config
from teacher_planner.neis import event_id

from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


class SchoolCalendarCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / '.local'
        self.path = self.private / 'state.json'
        self.config_path = self.private / 'config.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()
        install(self.api, self.c, self.api.parent, self.path)
        self.config_path.write_text(json.dumps(self.c))
        self.row = {'date': '2026-09-28', 'title': '[예시] 학교 행사', 'description': '가상 설명',
                    'grades': [1, 2], 'school_name': '가상학교', 'course': '중학교', 'day_night': '주간'}
        self.row['external_id'] = event_id('B10', '1234567', self.row['date'], self.row['title'], '주간', '중학교')
        self.snapshot = {'source': 'neis', 'office_code': 'B10', 'school_code': '1234567', 'school_name': '가상학교',
                         'academic_year': 2026, 'start': '2026-03-01', 'end': '2027-02-28',
                         'fetched_at': '2026-09-27T00:00:00+00:00', 'rows': [self.row]}
        self.addCleanup(patch.stopall)
        self.fetch = patch('teacher_planner.neis.fetch_schedule', return_value=self.snapshot).start()
        patch.dict('os.environ', {'NEIS_API_KEY': 'TEST_ONLY_NEIS_KEY', 'NEIS_OFFICE_CODE': '', 'NEIS_SCHOOL_CODE': ''}).start()
        self.out, self.err = io.StringIO(), io.StringIO()
        patch('sys.stdout', self.out).start()
        patch('sys.stderr', self.err).start()
        self.api.calls.clear()

    def args(self, command='neis-sync', extra=()):
        args = [command, '--office-code', 'B10', '--school-code', '1234567']
        if command == 'neis-sync':
            args += ['--config', str(self.config_path), '--state', str(self.path)]
        return args + list(extra)

    def state(self):
        return json.loads(self.path.read_text())

    def apply(self, extra=()):
        with patch('teacher_planner.school_calendar_cli.Client', return_value=self.api):
            return main(self.args(extra=['--apply', *extra]))

    def test_fetch_private_file_permissions_and_no_token_in_snapshot(self):
        output = self.private / 'calendar.json'
        with patch('teacher_planner.school_calendar_cli.Client', side_effect=AssertionError('Notion not allowed')):
            self.assertEqual(0, main(self.args('neis-calendar', ['--year', '2026', '--output', str(output)])))
        self.assertEqual(self.snapshot, json.loads(output.read_text()))
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        self.assertNotIn('TEST_ONLY_NEIS_KEY', output.read_text() + self.out.getvalue() + self.err.getvalue())
        self.fetch.assert_called_once_with('B10', '1234567', 2026, api_key='TEST_ONLY_NEIS_KEY', start=None, end=None)

    def test_preview_uses_config_year_and_does_not_touch_notion_or_state(self):
        before = self.path.read_bytes()
        with patch('teacher_planner.school_calendar_cli.Client', side_effect=AssertionError('Notion not allowed')):
            self.assertEqual(0, main(self.args(extra=['--from', '2026-09-01', '--to', '2026-09-30'])))
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(json.loads(self.out.getvalue())['applied'])
        self.fetch.assert_called_once_with('B10', '1234567', 2026, api_key='TEST_ONLY_NEIS_KEY',
                                          start='2026-09-01', end='2026-09-30')

    def test_env_codes_and_january_academic_year_default(self):
        class January(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2027, 1, 15, tzinfo=tz)
        with patch.dict('os.environ', {'NEIS_OFFICE_CODE': 'B10', 'NEIS_SCHOOL_CODE': '1234567'}), \
                patch('teacher_planner.school_calendar_cli.datetime', January), patch('teacher_planner.cli.private_json'):
            self.assertEqual(0, main(['neis-calendar']))
        self.assertEqual(2026, self.fetch.call_args.args[2])

    def test_invalid_watch_missing_codes_key_and_public_path_do_not_fetch(self):
        for extra in (['--watch'], ['--apply', '--watch', '--interval', '3599']):
            self.assertEqual(1, main(self.args(extra=extra)))
        self.assertEqual(1, main(['neis-calendar']))
        for key in ('', 'sample', 'sample key', 'sample_key'):
            with patch.dict('os.environ', {'NEIS_API_KEY': key}):
                self.assertEqual(1, main(self.args('neis-calendar')))
        self.assertEqual(1, main(self.args('neis-calendar', ['--output', str(Path(self.temp.name) / 'public.json')])))
        self.fetch.assert_not_called()

    def test_apply_and_unchanged_repeat_share_agenda_and_preserve_journal(self):
        self.assertEqual(0, self.apply())
        state = self.state()
        self.assertEqual(64, len(state['neis_source']))
        self.assertTrue(state['neis_last_checked_at'].endswith('+09:00'))
        self.assertEqual(1, sum(key.startswith('import:agenda:neis:') for key in state['objects']))
        self.assertEqual(1, sum(key.startswith('school-calendar:info:neis:') for key in state['objects']))
        agenda = self.api.pages(state['databases']['agenda']['data_source_id'])
        self.assertEqual({'start': '2026-09-28'}, agenda[0]['properties']['일정']['date'])
        self.api.calls.clear()
        self.assertEqual(0, self.apply())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_preview_counts_periods_separately_from_preserved_daily_rows(self):
        second = copy.deepcopy(self.row)
        second['date'] = '2026-09-29'
        second['external_id'] = event_id('B10', '1234567', second['date'], second['title'], '주간', '중학교')
        self.snapshot['rows'].append(second)
        before = self.path.read_bytes()
        self.assertEqual(0, main(self.args(extra=['--merge-existing'])))
        summary = json.loads(self.out.getvalue())
        self.assertEqual((1, 2), (summary['event_count'], summary['daily_record_count']))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.api.calls)

    def test_merge_flag_reaches_preflight_and_saved_summary_uses_group_count(self):
        second = copy.deepcopy(self.row)
        second['date'] = '2026-09-29'
        second['external_id'] = event_id('B10', '1234567', second['date'], second['title'], '주간', '중학교')
        self.snapshot['rows'].append(second)
        from teacher_planner.school_calendar import changes
        with patch('teacher_planner.school_calendar.changes', wraps=changes) as preflight:
            self.assertEqual(0, self.apply(['--merge-existing']))
        self.assertTrue(preflight.call_args.kwargs['merge_existing'])
        summary = self.state()['neis_last_range']
        self.assertEqual((1, 2), (summary['event_count'], summary['daily_record_count']))
        agenda = self.api.pages(self.state()['databases']['agenda']['data_source_id'])
        self.assertEqual(1, len(agenda))
        self.assertEqual({'start': '2026-09-28', 'end': '2026-09-29'}, agenda[0]['properties']['일정']['date'])

    def test_other_school_binding_is_rejected_before_writes(self):
        self.assertEqual(0, self.apply())
        before = self.path.read_bytes()
        self.snapshot['office_code'] = 'C10'
        self.api.calls.clear()
        self.assertEqual(1, self.apply())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_other_school_meal_binding_is_rejected_before_calendar_writes(self):
        state = self.state()
        state['neis_meals_source'] = fingerprint({'office_code': 'B10', 'school_code': '7654321', 'academic_year': 2026})
        self.path.write_text(json.dumps(state))
        before = self.path.read_bytes()
        self.assertEqual(1, self.apply())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_initial_empty_result_does_not_bind_school_but_later_empty_keeps_binding(self):
        empty = {**self.snapshot, 'office_code': 'C10', 'school_name': '', 'rows': []}
        self.fetch.return_value = empty
        self.assertEqual(0, self.apply(['--office-code', 'C10']))
        self.assertNotIn('neis_source', self.state())
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])
        self.fetch.return_value = self.snapshot
        self.assertEqual(0, self.apply())
        binding = self.state()['neis_source']
        self.fetch.return_value = {**self.snapshot, 'school_name': '', 'rows': []}
        self.api.calls.clear()
        self.assertEqual(0, self.apply())
        self.assertEqual(binding, self.state()['neis_source'])
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_wrong_config_identity_or_incomplete_install_is_rejected(self):
        original = self.state()
        for key, value in (('config', {}), ('identity', 'another-connection'), ('complete', False)):
            altered = copy.deepcopy(original)
            altered[key] = value
            self.path.write_text(json.dumps(altered))
            self.assertEqual(1, self.apply())
            self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_provider_failure_and_failed_apply_do_not_claim_successful_check(self):
        before = self.path.read_bytes()
        self.fetch.side_effect = ValueError('NEIS 조회 실패')
        self.assertEqual(1, self.apply())
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.api.calls)
        self.fetch.side_effect = None
        with patch('teacher_planner.school_calendar.apply_changes', side_effect=NotionError('저장 실패', 503)):
            self.assertEqual(1, self.apply())
        self.assertNotIn('neis_last_checked_at', self.state())
        self.assertIn('neis_source', self.state())

    def test_watch_rechecks_and_stops_cleanly_without_leaving_lock(self):
        with patch('teacher_planner.school_calendar_cli.sleep', side_effect=KeyboardInterrupt) as wait:
            self.assertEqual(0, self.apply(['--watch']))
        wait.assert_called_once_with(21600)
        self.assertEqual(1, self.fetch.call_count)
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_watch_stops_on_later_provider_failure(self):
        self.fetch.side_effect = [self.snapshot, ValueError('NEIS 조회 실패')]
        with patch('teacher_planner.school_calendar_cli.sleep') as wait:
            self.assertEqual(1, self.apply(['--watch', '--interval', '3600']))
        self.assertEqual(2, self.fetch.call_count)
        wait.assert_called_once_with(3600)
        self.assertTrue(self.state()['neis_last_checked_at'])


if __name__ == '__main__':
    unittest.main()
