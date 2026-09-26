import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from teacher_planner.client import NotionError
from teacher_planner.install import install
from teacher_planner.model import config
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
        self.assertEqual(4, self.sync())
        stamp = copy.deepcopy(self.page('timetable')['properties']['동기화 시각'])
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.api.calls)
        self.assertEqual(stamp, self.page('timetable')['properties']['동기화 시각'])

    def test_response_metadata_and_equivalent_dates_do_not_create_changes(self):
        self.sync()
        for source in ('timetable', 'agenda'):
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
        self.assertEqual(4, self.sync(source='컴시간 어댑터'))
        for source, name in (('timetable', '수업일'), ('agenda', '일정')):
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

    def test_cancellation_updates_both_records_and_repeated_poll_is_noop(self):
        self.sync()
        self.rows[0]['status'] = '휴강'
        self.api.calls.clear()
        self.assertEqual(2, self.sync())
        self.assertEqual('휴강', self.page('timetable')['properties']['상태']['select']['name'])
        self.assertEqual('취소', self.page('agenda')['properties']['상태']['select']['name'])
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

    def test_missing_agenda_relation_is_repaired_on_unchanged_rows(self):
        self.sync()
        self.page('timetable')['properties']['업무·일정'] = {'type': 'relation', 'relation': []}
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual({'업무·일정', '동기화 시각'}, set(self.api.calls[0][2]['properties']))
        self.assertEqual([{'id': self.page('agenda')['id']}], self.page('timetable')['properties']['업무·일정']['relation'])
        self.assertEqual(0, self.sync())

    def test_manually_authored_fields_survive_changed_and_unchanged_sync(self):
        self.sync()
        props = self.page('agenda')['properties']
        manual = {
            '우선순위': {'type': 'select', 'select': {'name': 'P1 지금'}},
            '보관': {'type': 'checkbox', 'checkbox': True},
            '다음 행동': {'type': 'rich_text', 'rich_text': [{'text': {'content': '내가 기록한 내용'}}]},
        }
        props.update(copy.deepcopy(manual))
        self.page('timetable')['properties']['보관']['checkbox'] = True
        self.rows[0]['status'] = '변경'
        self.rows[0]['room'] = '다른 교실'
        self.sync()
        self.assertEqual(manual, {key: props[key] for key in manual})
        self.assertTrue(self.page('timetable')['properties']['보관']['checkbox'])
        self.assertEqual(0, self.sync())

    def test_partial_update_resumes_without_rewriting_successful_agenda(self):
        self.sync()
        self.rows[0]['status'] = '휴강'
        original = self.api.request
        timetable_path = '/pages/' + self.page('timetable')['id']

        def fail_timetable(method, path, payload=None):
            if method == 'PATCH' and path == timetable_path:
                raise NotionError('temporary failure', 503)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail_timetable):
            with self.assertRaises(NotionError):
                self.sync()
        self.assertEqual('취소', self.page('agenda')['properties']['상태']['select']['name'])
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual(timetable_path, self.api.calls[0][1])
        self.assertEqual(0, self.sync())

    def test_partial_creation_uses_existing_agenda_for_link(self):
        ops = changes(self.api, self.c, self.state, self.rows)
        self.assertEqual(1, apply_changes(self.api, self.path, ops[:1]))
        self.api.calls.clear()
        self.assertEqual(3, self.sync())
        self.assertTrue(all(method == 'POST' for method, _, _ in self.api.calls))
        self.assertEqual([{'id': self.page('agenda')['id']}], self.page('timetable')['properties']['업무·일정']['relation'])

    def test_validate_rows_preserves_strict_file_contract(self):
        raw = json.loads((ROOT / 'examples/timetable.json').read_text())
        self.assertEqual(self.rows, validate_rows(raw, self.c))
        raw[0]['date_range'] = {'start': raw[0]['date']}
        with self.assertRaisesRegex(ValueError, '필수 열'):
            validate_rows(raw, self.c)


if __name__ == '__main__':
    unittest.main()
