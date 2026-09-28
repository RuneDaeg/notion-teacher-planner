import copy
import io
import json
import re
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from teacher_planner.blocks import BlockJournal, children
from teacher_planner.cli import main, verify_remote
from teacher_planner.client import NotionError
from teacher_planner.dashboard import top_columns
from teacher_planner.home import refresh_dashboard, verify_dashboard
from teacher_planner.install import Journal, block, install
from teacher_planner.model import blueprint, config
from teacher_planner.timetable import apply_changes, changes, read_rows, validate_rows
from test_planner import FakeNotion
from test_workspace_schema import filter_matches, formula_value


ROOT = Path(__file__).resolve().parents[1]


def plain(items):
    return ''.join(item.get('plain_text', item.get('text', {}).get('content', '')) for item in items)


class NotebookFixture:
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


class HomeTests(NotebookFixture, unittest.TestCase):
    def test_teaching_matrix_precedes_compact_management_tables(self):
        state = self.state()
        layout = state['dashboard']
        self.assertIn('matrix_id', layout)
        self.assertIn('caption_id', layout)
        self.assertNotIn('layout:top', state['objects'])
        self.assertEqual(self.obj('layout:matrix')['id'], layout['matrix_id'])
        order = layout['page_order']['teaching']
        self.assertLess(order.index(layout['caption_id']), order.index(layout['matrix_id']))
        self.assertLess(order.index(layout['matrix_id']), order.index(layout['anchors']['teaching:timetable']))
        self.assertLess(order.index(layout['anchors']['teaching:timetable']),
                        order.index(self.obj('teaching:teacher_week')['parent']['database_id']))
        self.assertNotIn(layout['matrix_id'], layout['page_order']['home'])
        props = self.api.request('GET', '/data_sources/' + state['databases']['timetable']['data_source_id'])['properties']
        names = ['이름', '수업일', '교시', '학급', '교과', '교실', '상태']
        expected = [props[name]['id'] for name in names]
        for key in ('view:teacher_week', 'home:teacher_today', 'home:teacher_changes',
                    'teaching:teacher_week', 'teaching:teacher_changes'):
            view = self.api.objects['/views/' + self.obj(key)['id']]
            with self.subTest(key=key):
                self.assertEqual('table', view['type'])
                self.assertEqual(expected, [p['property_id'] for p in view['configuration']['properties'] if p['visible']])
                self.assertNotIn('view_range', view['configuration'])
        teaching = self.api.objects['/views/' + self.obj('teaching:teacher_week')['id']]
        self.assertEqual([{'property': '수업일', 'direction': 'ascending'},
                          {'property': '교시', 'direction': 'ascending'}], teaching['sorts'])

    def test_earlier_notebook_without_grid_needs_no_block_refresh_or_state_change(self):
        state = self.state()
        for name in ('matrix_id', 'caption_id', 'matrix_row_ids'):
            state['dashboard'].pop(name)
        self.path.write_text(json.dumps(state), encoding='utf-8')
        self.synced()
        self.api.calls.clear()
        before = self.path.read_bytes()
        self.assertEqual(0, refresh_dashboard(self.api, self.path, self.rows))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.api.calls)

    def test_four_pages_keep_views_as_direct_page_children(self):
        state = self.state()
        self.assertEqual({'home', 'classroom', 'teaching', 'planning'}, set(state['dashboard']['pages']))
        for key, parent in state['dashboard']['pages'].items():
            actual = children(self.api, parent)
            expected = state['dashboard']['page_order'][key]
            self.assertEqual(expected, [row['id'] for row in actual[:len(expected)]])
            for group in state['dashboard']['view_groups']:
                if group['page'] == key:
                    linked = self.api.objects['/blocks/' + group['container_id']]
                    self.assertEqual(parent, linked['parent']['page_id'])
        self.assertEqual([], verify_dashboard(self.api, state))

    def test_week_and_month_are_tabs_in_one_home_calendar(self):
        weekly = self.api.objects['/views/' + self.obj('home:weekly')['id']]
        monthly = self.api.objects['/views/' + self.obj('home:monthly')['id']]
        self.assertNotEqual(weekly['id'], monthly['id'])
        self.assertEqual(weekly['parent'], monthly['parent'])
        self.assertEqual(self.state()['databases']['agenda']['data_source_id'], weekly['data_source_id'])
        self.assertEqual(weekly['data_source_id'], monthly['data_source_id'])
        self.assertEqual(weekly['configuration']['date_property_id'], monthly['configuration']['date_property_id'])
        self.assertEqual(('week', 'month'), (weekly['configuration']['view_range'], monthly['configuration']['view_range']))
        self.assertEqual(weekly['filter'], monthly['filter'])
        self.assertIn('create_database', weekly)
        self.assertNotIn('create_database', monthly)
        self.assertEqual(weekly['parent']['database_id'], monthly['database_id'])
        # The saved source database and separate teacher timetable keep their identities.
        self.assertNotEqual(self.state()['databases']['agenda']['id'], monthly['database_id'])
        self.assertNotEqual(self.obj('teaching:teacher_week')['parent'], monthly['parent'])
        self.assertEqual([], verify_remote(self.api, self.state()))

    def test_weekly_lessons_feed_today_at_home_without_agenda_copies(self):
        self.synced()
        refresh_dashboard(self.api, self.path, self.rows)
        state = self.state()
        today = self.api.objects['/views/' + self.obj('home:teacher_today')['id']]
        week = self.api.objects['/views/' + self.obj('teaching:teacher_week')['id']]
        self.assertEqual(week['data_source_id'], today['data_source_id'])
        self.assertEqual(state['databases']['timetable']['data_source_id'], today['data_source_id'])
        container = self.api.objects['/blocks/' + today['parent']['database_id']]
        self.assertEqual(state['dashboard']['pages']['home'], container['parent']['page_id'])
        properties = next(db['properties'] for db in blueprint()['databases'] if db['key'] == 'timetable')
        lessons = self.api.pages(today['data_source_id'])
        visible = []
        with patch('test_workspace_schema.TODAY', date(2026, 9, 28)):
            for lesson in lessons:
                props = lesson['properties']
                values = {'학년도': props['학년도']['number'], '보관': props['보관']['checkbox'],
                          '수업일': props['수업일']['date']}
                values['오늘 수업'] = formula_value(properties, values, '오늘 수업')
                if filter_matches(today['filter'], values):
                    visible.append(lesson)
        self.assertEqual(1, len(visible))
        self.assertEqual(1, visible[0]['properties']['교시']['number'])
        self.assertEqual('table', week['type'])
        self.assertIn('영어', self.matrix_text()[1][2])
        self.assertIn('2026-09-28 ~ 2026-10-02', self.caption())
        for key in ('home:todo', 'home:weekly', 'home:monthly', 'planning:weekly', 'planning:monthly'):
            view = self.api.objects['/views/' + self.obj(key)['id']]
            self.assertEqual(state['databases']['agenda']['data_source_id'], view['data_source_id'])
            self.assertEqual([], self.api.pages(view['data_source_id']))

    def test_verification_detects_monthly_tab_moved_to_another_database(self):
        monthly = self.api.objects['/views/' + self.obj('home:monthly')['id']]
        monthly['parent'] = self.obj('teaching:teacher_week')['parent']
        self.api.calls.clear()
        self.assertIn('home:monthly: 뷰가 속한 데이터베이스 불일치', verify_remote(self.api, self.state()))
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_both_calendar_pages_share_source_and_each_has_one_tab_container(self):
        state = self.state()
        containers = []
        for page in ('home', 'planning'):
            keys = [page + ':weekly', page + ':monthly']
            if page == 'planning':
                keys.append('planning:deadlines')
            views = [self.api.objects['/views/' + self.obj(key)['id']] for key in keys]
            self.assertEqual(1, len({view['parent']['database_id'] for view in views}))
            self.assertEqual({state['databases']['agenda']['data_source_id']}, {view['data_source_id'] for view in views})
            containers.append(views[0]['parent']['database_id'])
        self.assertNotEqual(*containers)

    def test_navigation_accepts_notion_null_links_and_detects_missing_page_link(self):
        for key in self.state()['dashboard']['pages']:
            nav = self.api.objects['/blocks/' + self.obj(f'layout:{key}:nav')['id']]
            for item in nav['paragraph']['rich_text']:
                item['text'].setdefault('link', None)
        self.assertEqual([], verify_remote(self.api, self.state()))
        nav['paragraph']['rich_text'][0]['text']['link'] = None
        self.assertTrue(any('탐색 링크' in issue for issue in verify_dashboard(self.api, self.state())))

    def test_shared_native_tabs_preserve_student_and_progress_sources(self):
        for keys, source in ((['classroom:active_students', 'classroom:students_gallery', 'classroom:students_observation'], 'students'),
                             (['teaching:progress_class', 'teaching:progress_subject', 'teaching:semester_1', 'teaching:semester_2'], 'lessons')):
            views = [self.api.objects['/views/' + self.obj(key)['id']] for key in keys]
            self.assertEqual(1, len({view['parent']['database_id'] for view in views}))
            self.assertEqual({self.state()['databases'][source]['data_source_id']}, {view['data_source_id'] for view in views})

    def test_verification_detects_subpage_moved_outside_the_notebook(self):
        page = self.api.objects['/pages/' + self.state()['dashboard']['pages']['classroom']]
        page['parent']['page_id'] = self.api.parent
        self.assertTrue(any('classroom: 하위 페이지' in issue for issue in verify_dashboard(self.api, self.state())))

    def test_verification_detects_moved_home_views_without_rewriting(self):
        state = self.state()
        self.assertEqual([], verify_dashboard(self.api, state))
        root = self.obj('root')['id']
        linked_id = self.obj('home:weekly')['parent']['database_id']
        self.api.block_children[root].remove(linked_id)
        self.api.block_children[root].append(linked_id)
        self.api.calls.clear()
        self.assertTrue(any('순서' in issue for issue in verify_dashboard(self.api, state)))
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

    def test_home_links_reference_created_database_and_view_ids(self):
        database_ids = {record['id'].replace('-', '') for record in self.state()['databases'].values()}
        database_ids.update(identifier.replace('-', '') for identifier in self.state()['dashboard']['pages'].values())
        forms = self.state()['forms']
        database_ids.add(forms['library_id'].replace('-', ''))
        database_ids.update(entry['page_id'].replace('-', '') for entry in forms['entries'].values())
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
        self.assertNotIn('teaching:active_meetings', state['objects'])
        self.assertNotIn('home:attendance_today', state['objects'])
        self.assertNotIn('classroom:active_contacts', state['objects'])
        self.assertNotIn('home:assessments_upcoming', state['objects'])
        serialized_blocks = json.dumps({key: value for key, value in api.objects.items() if key.startswith('/blocks/')}, ensure_ascii=False)
        self.assertNotIn('교직원 연락처', serialized_blocks)
        self.assertNotIn('학교 업무 계정 안내', serialized_blocks)

    def test_second_install_performs_no_post_or_patch(self):
        before = len(self.api.objects)
        install(self.api, self.c, self.api.parent, self.path)
        self.assertEqual(before, len(self.api.objects))
        self.assertEqual([], [call for call in self.api.calls if call[0] in ('POST', 'PATCH')])

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


class WeeklyMatrixTests(NotebookFixture, unittest.TestCase):
    def test_teaching_matrix_has_full_width_and_weekday_period_axes(self):
        table = self.api.objects['/blocks/' + self.state()['dashboard']['matrix_id']]
        self.assertEqual(self.state()['dashboard']['pages']['teaching'], table['parent']['page_id'])
        self.assertEqual(7, table['table']['table_width'])
        self.assertEqual(['교시', '수업시간', '월', '화', '수', '목', '금'], self.matrix_text()[0])
        self.assertEqual([f'{i}교시' for i in range(1, 8)] + ['공동'], [row[0] for row in self.matrix_text()[1:]])

    def test_legacy_column_grid_keeps_refresh_and_width_verification(self):
        journal = Journal(self.path, self.api)
        layout = journal.data['dashboard']
        parent = layout['pages']['teaching']
        top = BlockJournal(journal).append('layout:top', parent, top_columns(self.c, {}))
        left = children(self.api, top['id'])[0]['id']
        old_ids = [layout['caption_id'], layout['matrix_id']]
        for identifier in old_ids:
            self.api.block_children[parent].remove(identifier)
            self.api.block_children[left].append(identifier)
            self.api.objects['/blocks/' + identifier]['parent'] = {'block_id': left}
        self.api.block_children[parent].remove(top['id'])
        expected = layout['page_order']['teaching']
        index = expected.index(layout['caption_id'])
        expected[index:index + 2] = [top['id']]
        self.api.block_children[parent].insert(index, top['id'])
        layout['version'] = 3
        journal.save()
        self.assertEqual([], verify_dashboard(self.api, self.state()))
        self.synced()
        self.assertGreater(refresh_dashboard(self.api, self.path, self.rows), 0)
        self.assertIn('영어', self.matrix_text()[1][2])
        self.api.objects['/blocks/' + left]['column']['width_ratio'] = .5
        self.api.calls.clear()
        self.assertIn('수업 시간표: 열 개수·비율을 확인하세요.', verify_dashboard(self.api, self.state()))
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



if __name__ == '__main__':
    unittest.main()
