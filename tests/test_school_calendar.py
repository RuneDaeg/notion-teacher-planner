import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.blocks import recover_block
from teacher_planner.client import NotionError
from teacher_planner.install import Journal, compact
from teacher_planner.model import blueprint, config, rich, schema
from teacher_planner.neis import event_id
from teacher_planner.school_calendar import SOURCE_URL, apply_changes, changes, info_key, page_key
from teacher_planner.timetable import text_property
from test_planner import FakeNotion

ROOT = Path(__file__).resolve().parents[1]


class SchoolNotion(FakeNotion):
    """Notion preserves omitted callout fields on a partial block update."""
    def request(self, method, path, payload=None):
        partial = method == 'PATCH' and path.startswith('/blocks/') and not path.endswith('/children')
        previous = copy.deepcopy(self.objects.get(path, {}).get('callout')) if partial else None
        result = super().request(method, path, payload)
        if previous is not None and 'callout' in (payload or {}):
            previous.update(payload['callout'])
            self.objects[path]['callout'] = previous
            result = copy.deepcopy(self.objects[path])
        return result


class SchoolCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = SchoolNotion()
        definition = next(d for d in blueprint()['databases'] if d['key'] == 'agenda')
        db = self.api.request('POST', '/databases', {
            'parent': {'type': 'page_id', 'page_id': self.api.parent},
            'initial_data_source': {'properties': schema(definition['properties'])}})
        self.ds = db['data_sources'][0]['id']
        journal = Journal(self.path, self.api)
        # Existing notebooks need only their original agenda and state; no layout
        # or schema migration is required for this importer.
        journal.data.update(config=self.c, complete=True, signature='existing-design',
                            databases={'agenda': {'id': db['id'], 'data_source_id': self.ds}})
        journal.save()
        self.snapshot = {'source': 'neis', 'office_code': 'B10', 'school_code': '1234567',
                         'school_name': '가상학교', 'academic_year': 2026,
                         'start': '2026-03-01', 'end': '2027-02-28',
                         'fetched_at': '2026-09-27T00:00:00+00:00', 'rows': []}
        self.add_row()
        self.api.calls.clear()

    def add_row(self, day='2026-09-28', title='가상 학사행사'):
        row = {'date': day, 'title': title, 'description': '학년별 행사 안내',
               'grades': [1, 2], 'school_name': '가상학교', 'course': '중학교',
               'day_night': '주간', 'day_type': '해당없음', 'updated_at': '20260925000000'}
        row['external_id'] = event_id(self.snapshot['office_code'], self.snapshot['school_code'],
                                      day, title, row['day_night'], row['course'])
        self.snapshot['rows'].append(row)
        return row

    def state(self):
        return json.loads(self.path.read_text())

    def sync(self):
        return apply_changes(self.api, self.path, changes(self.api, self.c, self.state(), self.snapshot))

    def writes(self):
        return [call for call in self.api.calls if call[0] in ('POST', 'PATCH')]

    def page(self, row=0):
        key = page_key(self.snapshot['rows'][row]['external_id'])
        return self.api.objects['/pages/' + self.state()['objects'][key]['id']]

    def info(self, row=0):
        key = info_key(self.snapshot['rows'][row]['external_id'])
        return self.api.objects['/blocks/' + self.state()['objects'][key]['id']]

    def test_import_creates_one_agenda_event_and_source_callout_without_new_schema(self):
        self.assertEqual(1, self.sync())
        page, info = self.page(), self.info()
        props = page['properties']
        self.assertEqual(self.ds, page['parent']['data_source_id'])
        self.assertEqual('행사', props['종류']['select']['name'])
        self.assertEqual('예정', props['상태']['select']['name'])
        self.assertEqual('행정', props['업무 분류']['select']['name'])
        self.assertEqual({'start': '2026-09-28'}, props['일정']['date'])
        self.assertNotIn('마감', props)
        self.assertEqual(2026, props['학년도']['number'])
        self.assertFalse(props['보관']['checkbox'])
        text = ''.join(item['text']['content'] for item in info['callout']['rich_text'])
        self.assertIn('1, 2학년', text)
        self.assertIn('학년별 행사 안내', text)
        self.assertIn('20260925000000', text)
        self.assertEqual(SOURCE_URL, info['callout']['rich_text'][-1]['text']['link']['url'])
        self.assertEqual('existing-design', self.state()['signature'])
        self.assertEqual(2, len(self.writes()))
        self.assertTrue(all(path == '/pages' or path.endswith('/children') for _, path, _ in self.writes()))

    def test_unchanged_poll_and_response_metadata_cause_no_remote_writes(self):
        self.sync()
        self.snapshot['fetched_at'] = '2026-10-01T00:00:00+00:00'
        self.page()['properties']['일정']['date'].update(end=None, time_zone=None)
        for item in self.info()['callout']['rich_text']:
            item['plain_text'] = item['text']['content']
            item['annotations'] = {'bold': True, 'color': 'blue'}
            item['text'].setdefault('link', None)
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.writes())

    def test_description_grades_and_source_update_change_only_owned_info_block(self):
        self.sync()
        self.snapshot['rows'][0].update(description='변경 안내', grades=[3], updated_at='20260927000000')
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual([('PATCH', '/blocks/' + self.info()['id'])], [(m, p) for m, p, _ in self.writes()])
        self.assertEqual({'rich_text'}, set(self.writes()[0][2]['callout']))
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.writes())

    def test_manual_status_priority_due_archive_relations_and_body_are_preserved(self):
        self.sync()
        page = self.page()
        manual = {
            '상태': {'type': 'select', 'select': {'name': '취소'}},
            '우선순위': {'type': 'select', 'select': {'name': 'P1 지금'}},
            '마감': {'type': 'date', 'date': {'start': '2026-09-30'}},
            '다음 행동': {'type': 'rich_text', 'rich_text': rich('교사가 남긴 후속 메모')},
            '보관': {'type': 'checkbox', 'checkbox': True},
            '학급': {'type': 'relation', 'relation': [{'id': str(uuid4())}]},
            '종류': {'type': 'select', 'select': {'name': '회의'}},
            '업무 분류': {'type': 'select', 'select': {'name': '평가'}},
        }
        page['properties'].update(copy.deepcopy(manual))
        user_block = self.api.add_blocks(page['id'], [{'object': 'block', 'type': 'paragraph',
                                                     'paragraph': {'rich_text': rich('나의 상세 기록')}}])[0]
        self.info()['callout']['color'] = 'green_background'
        self.snapshot['rows'][0]['description'] = '원본 내용 변경'
        self.sync()
        self.assertEqual(manual, {key: page['properties'][key] for key in manual})
        self.assertEqual(user_block, self.api.objects['/blocks/' + user_block['id']])
        self.assertEqual('green_background', self.info()['callout']['color'])
        page['properties']['상태']['select']['name'] = '완료'
        self.assertEqual(0, self.sync())
        self.assertEqual('완료', page['properties']['상태']['select']['name'])

    def test_source_owned_title_and_date_are_repaired_without_touching_manual_fields(self):
        self.sync()
        page = self.page()
        page['properties']['이름']['title'] = rich('다른 제목')
        page['properties']['일정']['date'] = {'start': '2026-09-28T09:00:00+09:00'}
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual({'이름', '일정'}, set(self.writes()[0][2]['properties']))
        self.assertEqual(1, len(self.writes()))

    def test_missing_or_renamed_events_are_retained_without_heuristic_cancellation(self):
        self.sync()
        old_id = self.page()['id']
        self.snapshot.update(rows=[], school_name='')
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.writes())
        self.snapshot['school_name'] = '가상학교'
        self.add_row(day='2026-10-01', title='변경된 행사 이름')
        self.assertEqual(1, self.sync())
        self.assertEqual(2, len(self.api.pages(self.ds)))
        self.assertEqual('예정', self.api.objects['/pages/' + old_id]['properties']['상태']['select']['name'])

    def test_duplicate_remote_ids_are_detected_before_any_mutation(self):
        self.sync()
        source = self.page()
        self.api.request('POST', '/pages', {'parent': source['parent'],
                                          'properties': {key: {prop['type']: prop[prop['type']]} for key, prop in source['properties'].items()}})
        self.add_row(day='2026-10-05', title='새 행사')
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '중복'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_duplicate_or_forged_snapshot_ids_are_rejected_before_remote_mutation(self):
        self.snapshot['rows'].append(copy.deepcopy(self.snapshot['rows'][0]))
        with self.assertRaisesRegex(ValueError, '중복'):
            self.sync()
        self.snapshot['rows'].pop()
        self.snapshot['rows'][0]['external_id'] = 'neis:forged'
        with self.assertRaisesRegex(ValueError, '외부 ID'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_missing_or_replaced_journal_page_is_not_recreated(self):
        self.sync()
        del self.api.objects['/pages/' + self.page()['id']]
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '사라졌거나'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_info_parent_type_and_trash_are_checked_before_earlier_event_writes(self):
        self.sync()
        info = self.info()
        original = copy.deepcopy(info)
        new_row = self.add_row(day='2026-10-05', title='먼저 처리할 새 행사')
        self.snapshot['rows'].remove(new_row)
        self.snapshot['rows'].insert(0, new_row)
        for change in [{'parent': {'type': 'page_id', 'page_id': str(uuid4())}},
                       {'type': 'paragraph'}, {'in_trash': True}]:
            info.clear(); info.update(copy.deepcopy(original)); info.update(change)
            self.api.calls.clear()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '원본 안내 블록'):
                self.sync()
            self.assertEqual([], self.writes())

    def test_missing_info_block_is_not_replaced_without_inspection(self):
        self.sync()
        path = '/blocks/' + self.info()['id']
        original = self.api.request

        def missing(method, target, payload=None):
            if method == 'GET' and target == path:
                raise NotionError('missing', 404)
            return original(method, target, payload)

        self.api.calls.clear()
        with patch.object(self.api, 'request', side_effect=missing), self.assertRaises(NotionError):
            self.sync()
        self.assertEqual([], self.writes())

    def test_definite_info_append_failure_reuses_the_saved_agenda_page(self):
        original = self.api.request

        def fail_info(method, path, payload=None):
            if method == 'PATCH' and path.endswith('/children'):
                raise NotionError('rejected append', 400)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail_info), self.assertRaises(NotionError):
            self.sync()
        saved_id = self.page()['id']
        self.assertNotIn('pending', self.state())
        self.api.calls.clear()
        self.assertEqual(1, self.sync())
        self.assertEqual(saved_id, self.page()['id'])
        self.assertEqual(1, len(self.writes()))
        self.assertEqual('PATCH', self.writes()[0][0])

    def test_ambiguous_info_append_stays_pending_and_verified_recovery_avoids_duplicates(self):
        original = self.api.request

        def unknown_after_append(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH' and path.endswith('/children'):
                raise NotionError('unknown append', 503)
            return result

        with patch.object(self.api, 'request', side_effect=unknown_after_append), self.assertRaises(NotionError):
            self.sync()
        journal = Journal(self.path, self.api)
        self.assertEqual(info_key(self.snapshot['rows'][0]['external_id']), journal.data['pending']['key'])
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '불확실'):
            self.sync()
        self.assertEqual([], self.writes())
        block_id = self.api.block_children[self.page()['id']][0]
        block = self.api.objects['/blocks/' + block_id]
        recover_block(self.api, journal.data['pending'], block)
        journal.data['objects'][journal.data['pending']['key']] = compact(block)
        journal.data.pop('pending')
        journal.save()
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.writes())
        self.assertEqual([block_id], self.api.block_children[self.page()['id']])

    def test_ambiguous_page_creation_stays_pending_and_is_never_retried(self):
        original = self.api.request

        def unknown_after_create(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'POST' and path == '/pages':
                raise NotionError('unknown page', 503)
            return result

        with patch.object(self.api, 'request', side_effect=unknown_after_create), self.assertRaises(NotionError):
            self.sync()
        self.assertEqual(1, len(self.api.pages(self.ds)))
        self.assertEqual(page_key(self.snapshot['rows'][0]['external_id']), self.state()['pending']['key'])
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '불확실'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_applied_info_update_with_lost_response_is_reconciled_without_rewrite(self):
        self.sync()
        path = '/blocks/' + self.info()['id']
        self.snapshot['rows'][0]['description'] = '이미 반영된 원본 설명'
        original = self.api.request

        def unknown_after_update(method, target, payload=None):
            result = original(method, target, payload)
            if method == 'PATCH' and target == path:
                raise NotionError('unknown patch', 503)
            return result

        with patch.object(self.api, 'request', side_effect=unknown_after_update), self.assertRaises(NotionError):
            self.sync()
        self.assertNotIn('pending', self.state())
        self.api.calls.clear()
        self.assertEqual(0, self.sync())
        self.assertEqual([], self.writes())

    def test_stale_creation_plan_fails_before_recreating_an_event(self):
        ops = changes(self.api, self.c, self.state(), self.snapshot)
        self.assertEqual(1, apply_changes(self.api, self.path, ops))
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '상태가 바뀌었'):
            apply_changes(self.api, self.path, ops)
        self.assertEqual([], self.writes())

    def test_untrusted_source_url_and_extra_fields_are_not_propagated(self):
        self.snapshot['source_url'] = 'https://example.invalid/?KEY=not-a-real-key'
        self.snapshot['rows'][0]['api_url'] = self.snapshot['source_url']
        ops = changes(self.api, self.c, self.state(), self.snapshot)
        self.assertNotIn('not-a-real-key', json.dumps(ops))
        self.sync()
        self.assertNotIn('not-a-real-key', self.path.read_text())
        self.assertEqual(SOURCE_URL, self.info()['callout']['rich_text'][-1]['text']['link']['url'])

    def test_long_source_description_is_split_into_valid_rich_text_items(self):
        self.snapshot['rows'][0]['description'] = '가' * 10000
        self.sync()
        items = self.info()['callout']['rich_text']
        self.assertLess(len(items), 100)
        self.assertTrue(all(len(item['text']['content']) <= 2000 for item in items))
        self.assertEqual(0, self.sync())

    def test_oversized_escaped_source_content_fails_before_any_page_is_created(self):
        self.add_row(day='2026-10-05', title='내용이 긴 행사')['description'] = '가' * 100000
        with self.assertRaisesRegex(ValueError, '요청 크기 제한'):
            self.sync()
        self.assertEqual([], self.writes())
        self.assertEqual([], self.api.pages(self.ds))

    def test_mismatched_row_school_name_fails_before_any_write(self):
        self.snapshot['rows'][0]['school_name'] = '다른 가상학교'
        with self.assertRaisesRegex(ValueError, '학교명'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_non_neis_records_are_not_changed_or_treated_as_import_duplicates(self):
        for _ in range(2):
            self.api.request('POST', '/pages', {'parent': {'type': 'data_source_id', 'data_source_id': self.ds},
                'properties': {'이름': {'title': rich('수동 업무')}, '외부 ID': {'rich_text': rich('tt:unrelated')}}})
        originals = copy.deepcopy(self.api.pages(self.ds))
        self.sync()
        for old in originals:
            self.assertEqual(old, self.api.objects['/pages/' + old['id']])


if __name__ == '__main__':
    unittest.main()
