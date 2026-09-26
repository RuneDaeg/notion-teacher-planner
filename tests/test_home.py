import copy
import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from teacher_planner.blocks import BlockJournal, children
from teacher_planner.cli import main
from teacher_planner.client import NotionError
from teacher_planner.home import refresh_dashboard, verify_dashboard
from teacher_planner.install import Journal, block, install
from teacher_planner.model import config
from teacher_planner.timetable import apply_changes, changes, read_rows, validate_rows
from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


def plain(items):
    return ''.join(item.get('plain_text', item.get('text', {}).get('content', '')) for item in items)


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()
        install(self.api, self.c, self.api.parent, self.path)
        self.rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        self.api.calls.clear()

    def state(self):
        return json.loads(self.path.read_text())

    def obj(self, key):
        return self.state()['objects'][key]

    def synced(self, rows=None):
        rows = self.rows if rows is None else rows
        return apply_changes(self.api, self.path, changes(self.api, self.c, self.state(), rows))

    def matrix(self):
        return children(self.api, self.state()['dashboard']['matrix_id'])

    def matrix_text(self):
        return [[plain(cell) for cell in row['table_row']['cells']] for row in self.matrix()]

    def caption(self):
        row = self.api.objects['/blocks/' + self.state()['dashboard']['caption_id']]
        return plain(row['paragraph']['rich_text'])

    def test_home_section_order_and_linked_views_are_direct_page_children(self):
        keys = ['layout:top', 'layout:things', 'home:inbox', 'home:todo',
                'layout:meetings', 'home:active_meetings', 'layout:middle',
                'layout:students', 'home:active_students', 'layout:schedule',
                'home:weekly', 'home:monthly', 'home:teacher_week',
                'layout:archive', 'home:archive_agenda']
        expected = [self.obj(key)['parent']['database_id'] if key.startswith('home:') else self.obj(key)['id'] for key in keys]
        actual = children(self.api, self.obj('root')['id'])
        self.assertEqual(expected, [row['id'] for row in actual[:len(expected)]])
        for key in (key for key in keys if key.startswith('home:')):
            linked = self.api.objects['/blocks/' + self.obj(key)['parent']['database_id']]
            self.assertEqual(self.obj('root')['id'], linked['parent']['page_id'])
        # Source database pages remain behind the new dashboard, not reordered
        # into it or replaced with imported attachment identifiers.
        self.assertGreater([row['id'] for row in actual].index(self.obj('section:운영 자료')['id']), len(expected))

    def test_top_matrix_and_column_widths_match_notebook_layout(self):
        top = children(self.api, self.obj('layout:top')['id'])
        middle = children(self.api, self.obj('layout:middle')['id'])
        self.assertEqual([.625, .375], [row['column']['width_ratio'] for row in top])
        self.assertEqual([.21, .58, .21], [row['column']['width_ratio'] for row in middle])
        table = self.api.objects['/blocks/' + self.state()['dashboard']['matrix_id']]
        self.assertEqual(top[0]['id'], table['parent']['block_id'])
        self.assertEqual(7, table['table']['table_width'])
        self.assertEqual(['교시', '수업시간', '월', '화', '수', '목', '금'], self.matrix_text()[0])
        self.assertEqual([f'{i}교시' for i in range(1, 8)] + ['공동'], [row[0] for row in self.matrix_text()[1:]])

    def test_verification_detects_moved_home_views_without_rewriting(self):
        state = self.state()
        self.assertEqual([], verify_dashboard(self.api, state))
        root = self.obj('root')['id']
        linked_id = self.obj('home:active_students')['parent']['database_id']
        self.api.block_children[root].remove(linked_id)
        self.api.block_children[root].append(linked_id)
        self.api.calls.clear()
        self.assertTrue(any('순서' in issue for issue in verify_dashboard(self.api, state)))
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_home_links_reference_created_database_and_view_ids(self):
        database_ids = {record['id'].replace('-', '') for record in self.state()['databases'].values()}
        view_ids = {record['id'].replace('-', '') for key, record in self.state()['objects'].items() if key.startswith('view:')}
        seen = []

        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get('link'), dict) and value['link'].get('url'):
                    seen.append(value['link']['url'])
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        for key, value in self.api.objects.items():
            if key.startswith('/blocks/'):
                visit(value)
        self.assertGreater(len(seen), 20)
        for url in seen:
            match = re.fullmatch(r'https://www\.notion\.so/([0-9a-f]{32})(?:\?v=([0-9a-f]{32}))?', url)
            self.assertIsNotNone(match, url)
            self.assertIn(match.group(1), database_ids)
            if match.group(2):
                self.assertIn(match.group(2), view_ids)

    def test_old_four_module_config_omits_unselected_staff_accounts_and_meetings(self):
        raw = copy.deepcopy(self.c)
        raw['modules'] = {key: False for key in ('attendance', 'assessment', 'contact', 'meeting')}
        raw.pop('dashboard', None)
        path = Path(self.temp.name) / 'old-config.json'
        path.write_text(json.dumps(raw), encoding='utf-8')
        old = config(path)
        self.assertEqual(raw, old)
        api, state_path = FakeNotion(), Path(self.temp.name) / 'old-state.json'
        install(api, old, api.parent, state_path)
        state = json.loads(state_path.read_text())
        self.assertNotIn('staff', state['databases'])
        self.assertNotIn('accounts', state['databases'])
        self.assertNotIn('meetings', state['databases'])
        self.assertNotIn('layout:meetings', state['objects'])
        serialized_blocks = json.dumps({key: value for key, value in api.objects.items() if key.startswith('/blocks/')}, ensure_ascii=False)
        self.assertNotIn('교직원 연락처', serialized_blocks)
        self.assertNotIn('학교 업무 계정 안내', serialized_blocks)

    def test_second_install_performs_no_post_or_patch(self):
        before = len(self.api.objects)
        install(self.api, self.c, self.api.parent, self.path)
        self.assertEqual(before, len(self.api.objects))
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_refresh_uses_actual_remote_lessons_and_second_refresh_is_noop(self):
        self.synced()
        page = self.api.pages(self.state()['databases']['timetable']['data_source_id'])[0]
        self.api.request('PATCH', '/pages/' + page['id'], {'properties': {'교실': {'rich_text': [{'type': 'text', 'text': {'content': '현장 교실'}}]}}})
        self.assertGreater(refresh_dashboard(self.api, self.path, self.rows), 0)
        self.assertIn('현장 교실', self.matrix_text()[1][2])
        self.assertIn('2026-09-28 ~ 2026-10-02', self.caption())
        self.api.calls.clear()
        self.assertEqual(0, refresh_dashboard(self.api, self.path, self.rows))
        self.assertEqual([], [call for call in self.api.calls if call[0] == 'PATCH'])

    def test_changed_and_cancelled_lessons_update_the_visible_cells(self):
        self.synced()
        refresh_dashboard(self.api, self.path, self.rows)
        self.rows[0].update(status='변경', room='202')
        self.rows[1]['status'] = '휴강'
        self.synced()
        self.assertGreater(refresh_dashboard(self.api, self.path, self.rows), 0)
        cells = self.matrix_text()
        self.assertIn('변경 · 영어', cells[1][2])
        self.assertIn('202', cells[1][2])
        self.assertIn('취소 · 영어', cells[2][3])
        cancelled = self.matrix()[2]['table_row']['cells'][3]
        self.assertTrue(cancelled[0]['annotations']['strikethrough'])

    def test_omitted_input_slot_keeps_its_existing_remote_lesson_in_matrix(self):
        self.synced()
        refresh_dashboard(self.api, self.path, self.rows)
        self.synced(self.rows[:1])
        self.assertEqual(0, refresh_dashboard(self.api, self.path, self.rows[:1]))
        self.assertIn('영어', self.matrix_text()[2][3])

    def test_eighth_period_extends_journaled_rows_and_empty_next_week_keeps_height(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw[0].update(period=8, start='16:00', end='16:50')
        rows = validate_rows(raw, self.c)
        self.synced(rows)
        self.assertGreater(refresh_dashboard(self.api, self.path, rows), 0)
        self.assertEqual(10, len(self.matrix()))
        self.assertEqual('8교시', self.matrix_text()[8][0])
        self.assertIn('영어', self.matrix_text()[8][2])
        self.assertEqual('공동', self.matrix_text()[9][0])
        extra = {key: value['id'] for key, value in self.state()['objects'].items() if key.startswith('layout:matrix:extra:')}
        self.assertEqual(1, len(extra))
        self.assertEqual(0, refresh_dashboard(self.api, self.path, rows))
        refresh_dashboard(self.api, self.path, [], '2026-10-05')
        self.assertEqual(10, len(self.matrix()))
        self.assertEqual('8교시', self.matrix_text()[8][0])
        self.assertEqual('', self.matrix_text()[8][2])
        self.assertEqual(extra, {key: value['id'] for key, value in self.state()['objects'].items() if key.startswith('layout:matrix:extra:')})

    def test_removed_owned_base_or_extra_row_stops_refresh_before_writes(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw[0].update(period=8, start='16:00', end='16:50')
        rows = validate_rows(raw, self.c)
        self.synced(rows)
        refresh_dashboard(self.api, self.path, rows)
        matrix_id = self.state()['dashboard']['matrix_id']
        original = list(self.api.block_children[matrix_id])
        for index in (1, -1):
            with self.subTest(index=index):
                self.api.block_children[matrix_id] = list(original)
                del self.api.block_children[matrix_id][index]
                self.api.calls.clear()
                with self.assertRaisesRegex(ValueError, '행 순서나 개수'):
                    refresh_dashboard(self.api, self.path, rows)
                self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_manual_common_row_survives_refresh_and_period_expansion(self):
        common = self.matrix()[-1]
        note = [{'type': 'text', 'text': {'content': '공동수업 준비', 'link': {'url': 'https://example.org/class'}},
                 'annotations': {'bold': True, 'color': 'purple'}}]
        self.api.objects['/blocks/' + common['id']]['table_row']['cells'][2] = copy.deepcopy(note)
        self.synced()
        refresh_dashboard(self.api, self.path, self.rows)
        self.assertEqual(note, self.matrix()[-1]['table_row']['cells'][2])
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw[0].update(period=8, start='16:00', end='16:50')
        rows = validate_rows(raw, self.c)
        self.synced(rows)
        refresh_dashboard(self.api, self.path, rows)
        self.assertNotEqual(common['id'], self.matrix()[-1]['id'])
        self.assertEqual(note, self.matrix()[-1]['table_row']['cells'][2])
        self.assertEqual('8교시', self.matrix_text()[8][0])
        self.assertIn('영어', self.matrix_text()[8][2])

    def test_partial_multi_period_extension_resumes_without_losing_common_note(self):
        note = [{'type': 'text', 'text': {'content': '공동수업 메모'}}]
        common_id = self.matrix()[-1]['id']
        self.api.objects['/blocks/' + common_id]['table_row']['cells'][3] = copy.deepcopy(note)
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw[0].update(period=10, start='18:00', end='18:50')
        rows = validate_rows(raw, self.c)
        self.synced(rows)
        endpoint = '/blocks/' + self.state()['dashboard']['matrix_id'] + '/children'
        original = self.api.request
        append_count = 0

        def reject_second_append(method, path, payload=None):
            nonlocal append_count
            if method == 'PATCH' and path == endpoint:
                append_count += 1
                if append_count == 2:
                    raise NotionError('definitive rejection', 400)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=reject_second_append):
            with self.assertRaises(NotionError):
                refresh_dashboard(self.api, self.path, rows)
        self.assertEqual(10, len(self.matrix()))
        self.assertEqual('9교시', self.matrix_text()[-1][0])
        self.assertNotIn('pending', self.state())
        self.assertEqual(note, self.api.objects['/blocks/' + common_id]['table_row']['cells'][3])
        self.assertGreater(refresh_dashboard(self.api, self.path, rows), 0)
        self.assertEqual(12, len(self.matrix()))
        self.assertEqual(['교시'] + [f'{i}교시' for i in range(1, 11)] + ['공동'], [row[0] for row in self.matrix_text()])
        self.assertEqual(note, self.matrix()[-1]['table_row']['cells'][3])
        self.assertEqual(3, sum(key.startswith('layout:matrix:extra:') for key in self.state()['objects']))
        self.assertEqual(0, refresh_dashboard(self.api, self.path, rows))

    def test_multiple_weeks_choose_current_week_then_latest_available_week(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw.append({**raw[0], 'date': '2026-10-05'})
        rows = validate_rows(raw, self.c)
        self.synced(rows)

        class CurrentWeek(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 30, 12, tzinfo=tz)

        class LaterWeek(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 20, 12, tzinfo=tz)

        with patch('teacher_planner.home.datetime', CurrentWeek):
            refresh_dashboard(self.api, self.path, rows)
        self.assertIn('2026-09-28 ~ 2026-10-02', self.caption())
        with patch('teacher_planner.home.datetime', LaterWeek):
            refresh_dashboard(self.api, self.path, rows)
        self.assertIn('2026-10-05 ~ 2026-10-09', self.caption())

    def test_utc_response_and_integral_number_preserve_seoul_morning_slot(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())[:1]
        raw[0].update(start='08:40', end='09:30')
        rows = validate_rows(raw, self.c)
        self.synced(rows)
        page = self.api.pages(self.state()['databases']['timetable']['data_source_id'])[0]
        props = self.api.objects['/pages/' + page['id']]['properties']
        for point in ('start', 'end'):
            props['수업일']['date'][point] = datetime.fromisoformat(props['수업일']['date'][point]).astimezone(timezone.utc).isoformat()
        props['교시']['number'] = 1.0
        refresh_dashboard(self.api, self.path, rows)
        self.assertIn('영어', self.matrix_text()[1][2])
        self.assertEqual('08:40–09:30', self.matrix_text()[1][1])

    def test_legacy_state_without_dashboard_is_untouched(self):
        state = self.state()
        del state['dashboard']
        self.path.write_text(json.dumps(state), encoding='utf-8')
        before = self.path.read_bytes()
        self.assertEqual(0, refresh_dashboard(self.api, self.path, self.rows))
        self.assertEqual([], self.api.calls)
        self.assertEqual(before, self.path.read_bytes())

    def test_recover_cli_reads_block_endpoint_and_does_not_reappend(self):
        journal = Journal(self.path, self.api)
        original = self.api.request

        def lose_response(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH':
                raise NotionError('lost response')
            return result

        with patch.object(self.api, 'request', side_effect=lose_response):
            with self.assertRaises(NotionError):
                BlockJournal(journal).append('layout:recovered', self.obj('root')['id'], block('paragraph', '복구 확인'))
        created = children(self.api, self.obj('root')['id'])[-1]
        self.api.calls.clear()
        with patch('teacher_planner.cli.Client', return_value=self.api), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(0, main(['recover', '--state', str(self.path), '--id', created['id']]))
        self.assertIn(('GET', '/blocks/' + created['id'], None), self.api.calls)
        self.assertNotIn('pending', self.state())
        self.assertEqual(created['id'], self.obj('layout:recovered')['id'])
        BlockJournal(Journal(self.path, self.api)).append('layout:recovered', self.obj('root')['id'], block('paragraph', '복구 확인'))
        self.assertEqual([], [call for call in self.api.calls if call[0] == 'PATCH'])


if __name__ == '__main__':
    unittest.main()
