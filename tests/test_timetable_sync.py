import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.client import NotionError
from teacher_planner.install import Journal, compact, install
from teacher_planner.model import config, rich
from teacher_planner.timetable import apply_changes, changes, read_rows, validate_rows
from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


class TimetableSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()
        install(self.api, self.c, self.api.parent, self.path)
        self.state = json.loads(self.path.read_text())
        self.rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        self.api.calls.clear()

    def sync(self, source='파일 가져오기'):
        return apply_changes(self.api, self.path,
                             changes(self.api, self.c, self.state, self.rows, source=source))

    def page(self, source, index=0):
        page = self.api.pages(self.state['databases'][source]['data_source_id'])[index]
        return self.api.objects['/pages/' + page['id']]

    def test_repeated_sync_writes_nothing_and_preserves_timestamp(self):
        self.assertEqual(2, self.sync())
        stamp = copy.deepcopy(self.page('timetable')['properties']['동기화 시각'])
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.api.calls)
        self.assertEqual(stamp, self.page('timetable')['properties']['동기화 시각'])

    def test_response_metadata_and_equivalent_dates_do_not_create_changes(self):
        self.sync()
        for source in ('timetable',):
            for page in self.api.pages(self.state['databases'][source]['data_source_id']):
                props = self.api.objects['/pages/' + page['id']]['properties']
                for prop in props.values():
                    prop['id'] = 'response-only-id'
                    kind = prop['type']
                    if kind in ('rich_text', 'title'):
                        for text in prop[kind]:
                            text['plain_text'] = text['text']['content']
                            text['annotations'] = {'bold': False, 'color': 'default'}
                    elif kind == 'select':
                        prop[kind].update(id='select-id', color='default')
                    elif kind == 'relation':
                        for item in prop[kind]:
                            item['id'] = item['id'].replace('-', '')
                    elif kind == 'date':
                        value = prop[kind]
                        for point in ('start', 'end'):
                            if point in value:
                                value[point] = datetime.fromisoformat(value[point]).astimezone(timezone.utc).isoformat(timespec='milliseconds')
                        value['time_zone'] = None
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.api.calls)

    def test_all_day_provider_rows_are_idempotent_and_keep_source(self):
        for row in self.rows:
            row['date_range'] = {'start': row['date']}
        self.assertEqual(2, self.sync(source='컴시간 어댑터'))
        for source, name in (('timetable', '수업일'),):
            date_value = self.page(source)['properties'][name]['date']
            self.assertEqual({'start': self.rows[0]['date']}, date_value)
            date_value.update(end=None, time_zone=None)
        self.assertEqual('컴시간 어댑터', self.page('timetable')['properties']['출처']['select']['name'])
        self.api.calls.clear()
        self.assertEqual(0, self.sync(source='컴시간 어댑터'))
        self.assertEqual([], self.api.calls)

    def test_room_change_writes_only_timetable_changed_fields(self):
        self.sync()
        self.rows[0]['room'] = '변경 교실'
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual(1, len(self.api.calls))
        method, path, body = self.api.calls[0]
        self.assertEqual(('PATCH', '/pages/' + self.page('timetable')['id']), (method, path))
        self.assertEqual({'교실', '동기화 시각'}, set(body['properties']))

    def test_cancellation_updates_only_timetable_and_repeated_poll_is_noop(self):
        self.sync()
        self.rows[0]['status'] = '휴강'
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual('휴강', self.page('timetable')['properties']['상태']['select']['name'])
        self.assertEqual([], self.api.pages(self.state['databases']['agenda']['data_source_id']))
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.api.calls)

    def test_source_change_is_persisted_without_changing_agenda(self):
        self.sync()
        self.api.calls.clear()
        self.assertEqual(2, self.sync(source='컴시간 어댑터'))
        for method, _, payload in self.api.calls:
            self.assertEqual('PATCH', method)
            self.assertEqual({'출처', '동기화 시각'}, set(payload['properties']))

    def test_absent_and_empty_agenda_relations_are_never_created_or_repaired(self):
        self.sync()
        self.assertNotIn('업무·일정', self.page('timetable')['properties'])
        self.page('timetable')['properties']['업무·일정'] = {'type': 'relation', 'relation': []}
        stamp = copy.deepcopy(self.page('timetable')['properties']['동기화 시각'])
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.api.calls)
        self.assertEqual([], self.page('timetable')['properties']['업무·일정']['relation'])
        self.assertEqual(stamp, self.page('timetable')['properties']['동기화 시각'])

    def test_manual_timetable_relation_notes_and_archive_survive_changes(self):
        self.sync()
        props = self.page('timetable')['properties']
        manual = {
            '업무·일정': {'type': 'relation', 'relation': [{'id': str(uuid4())}]},
            '보관': {'type': 'checkbox', 'checkbox': True},
            '내 메모': {'type': 'rich_text', 'rich_text': rich('내가 기록한 내용')},
        }
        props.update(copy.deepcopy(manual))
        self.rows[0]['status'] = '변경'
        self.rows[0]['room'] = '다른 교실'
        self.assertEqual(1, self.sync())
        self.assertEqual(manual, {key: props[key] for key in manual})
        self.assertEqual(0, self.sync())

    def test_existing_manual_neis_and_legacy_agenda_rows_are_never_read_or_written(self):
        agenda_ds = self.state['databases']['agenda']['data_source_id']
        legacy = None
        for external, title, status in [('', '수동 업무', '진행'),
                                        ('neis:fixture', 'NEIS 학사일정', '예정'),
                                        (self.rows[0]['external_id'], '이전 버전의 수업 일정', '완료'),
                                        (self.rows[0]['external_id'], '이전 버전의 중복 일정', '취소')]:
            legacy = self.api.request('POST', '/pages', {
                'parent': {'type': 'data_source_id', 'data_source_id': agenda_ds},
                'properties': {'이름': {'title': rich(title)}, '외부 ID': {'rich_text': rich(external)},
                               '상태': {'select': {'name': status}}, '보관': {'checkbox': True},
                               '우선순위': {'select': {'name': 'P1 지금'}},
                               '일정': {'date': {'start': self.rows[0]['date']}}}})
        before = copy.deepcopy(self.api.pages(agenda_ds))
        journal = Journal(self.path, self.api)
        legacy_key = 'import:agenda:' + self.rows[0]['external_id']
        journal.data['objects'][legacy_key] = compact(legacy)
        journal.save()
        original_pages = self.api.pages

        def forbid_agenda_reads(ds, filter_=None):
            self.assertNotEqual(agenda_ds, ds, 'Time sync queried the agenda database')
            return original_pages(ds, filter_)

        self.api.calls.clear()
        with patch.object(self.api, 'pages', side_effect=forbid_agenda_reads):
            self.assertEqual(2, self.sync())
            self.assertEqual(2, self.sync(source='컴시간 어댑터'))
            self.rows[0]['status'] = '휴강'
            self.rows[1]['room'] = '바뀐 교실'
            self.assertEqual(2, self.sync(source='컴시간 어댑터'))
            self.assertEqual(0, self.sync(source='컴시간 어댑터'))
        self.assertEqual(before, self.api.pages(agenda_ds))
        self.assertEqual(compact(legacy), Journal(self.path, self.api).data['objects'][legacy_key])
        for method, path, payload in self.api.calls:
            if method == 'POST' and path == '/pages':
                self.assertEqual(self.state['databases']['timetable']['data_source_id'], payload['parent']['data_source_id'])
            if method == 'PATCH':
                self.assertNotIn(path, ['/pages/' + page['id'] for page in before])
                self.assertNotIn('업무·일정', payload.get('properties', {}))

    def test_old_mixed_source_plans_are_rejected_before_the_first_write(self):
        ops = changes(self.api, self.c, self.state, self.rows)
        self.assertEqual(2, len(ops))
        self.assertEqual({'timetable'}, {op['source'] for op in ops})
        for source in ('agenda', 'other'):
            stale = dict(ops[-1], source=source)
            self.api.calls.clear()
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, '교사 시간표만'):
                apply_changes(self.api, self.path, [ops[0], stale])
            self.assertEqual([], self.api.calls)
            self.assertEqual([], self.api.pages(self.state['databases']['timetable']['data_source_id']))

    def test_explicit_relation_writes_in_old_ops_are_rejected_before_mutations(self):
        ops = changes(self.api, self.c, self.state, self.rows)
        ops[-1]['changed_properties']['업무·일정'] = {'relation': [{'id': str(uuid4())}]}
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '수동 업무·일정 관계'):
            apply_changes(self.api, self.path, ops)
        self.assertEqual([], self.api.calls)

    def test_partial_update_resumes_without_rewriting_successful_timetable_row(self):
        self.sync()
        self.rows[0]['status'] = '휴강'
        self.rows[1]['status'] = '변경'
        original = self.api.request
        failed_path = '/pages/' + self.page('timetable', 1)['id']

        def fail_second(method, path, payload=None):
            if method == 'PATCH' and path == failed_path:
                raise NotionError('temporary failure', 503)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail_second):
            with self.assertRaises(NotionError):
                self.sync()
        self.assertEqual('휴강', self.page('timetable')['properties']['상태']['select']['name'])
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual(failed_path, self.api.calls[0][1])
        self.assertEqual(0, self.sync())

    def test_partial_creation_resumes_remaining_timetable_without_agenda(self):
        ops = changes(self.api, self.c, self.state, self.rows)
        self.assertEqual(1, apply_changes(self.api, self.path, ops[:1]))
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual(1, len(self.api.calls))
        self.assertTrue(all(method == 'POST' for method, _, _ in self.api.calls))
        self.assertEqual([], self.api.pages(self.state['databases']['agenda']['data_source_id']))
        self.assertTrue(all('업무·일정' not in page['properties'] for page in
                            self.api.pages(self.state['databases']['timetable']['data_source_id'])))

    def test_validate_rows_preserves_strict_file_contract(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())
        self.assertEqual(self.rows, validate_rows(raw, self.c))
        raw[0]['date_range'] = {'start': raw[0]['date']}
        with self.assertRaisesRegex(ValueError, '필수 열'):
            validate_rows(raw, self.c)


if __name__ == '__main__':
    unittest.main()
