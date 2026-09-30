"""Offline cloud worker tests with fictional schools and isolated Notion state."""
import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.client import NotionError
from teacher_planner.cloud_sync import (SOURCE_MARKER, REQUIRED_PROPERTIES,
                                        sync_once, validate_targets)
from teacher_planner.model import rich
from teacher_planner.neis import event_id
from teacher_planner.school_calendar import grouped_schedule, source_info
from teacher_planner.timetable import text_property
from test_planner import FakeNotion


class CloudNotion(FakeNotion):
    def __init__(self):
        super().__init__()
        self.durable = None
        self.enforce_checkpoint = False

    def request(self, method, path, payload=None):
        if self.enforce_checkpoint and (method == 'POST' and path == '/pages'
                                      or method == 'PATCH' and path.endswith('/children')):
            if not self.durable or not self.durable.get('pending'):
                raise AssertionError('Creation was not durably checkpointed')
        if self.enforce_checkpoint and method == 'PATCH' and not path.endswith('/children'):
            if not self.durable or not self.durable.get('mutation'):
                raise AssertionError('Update was not durably checkpointed')
        partial = method == 'PATCH' and path.startswith('/blocks/') and not path.endswith('/children')
        previous = copy.deepcopy(self.objects.get(path, {}).get('callout')) if partial else None
        result = super().request(method, path, payload)
        if previous is not None and 'callout' in (payload or {}):
            previous.update(payload['callout'])
            self.objects[path]['callout'] = previous
            result = copy.deepcopy(self.objects[path])
        return result


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.api = CloudNotion()
        root = self.api.request('POST', '/pages', {'parent': {'type': 'workspace', 'workspace': True},
                    'properties': {'title': {'title': rich('가상 수첩')}}})
        section = self.api.request('POST', '/pages', {'parent': {'type': 'page_id', 'page_id': root['id']},
                       'properties': {'title': {'title': rich('운영 자료')}}})
        db = self.api.request('POST', '/databases', {'parent': {'type': 'page_id', 'page_id': section['id']},
                'initial_data_source': {'properties': {name: {kind: {}} for name, kind in REQUIRED_PROPERTIES.items()}}})
        self.ds = db['data_sources'][0]['id']
        self.api.objects['/data_sources/' + self.ds].update(
            object='data_source', parent={'type': 'database_id', 'database_id': db['id']})
        # Meals nested under a column, while status is a direct root child.
        column = self.api.add_blocks(root['id'], [{'object': 'block', 'type': 'column', 'column': {}}])[0]
        meals = self.api.add_blocks(column['id'], [self.callout('급식 미조회')])[0]
        status = self.api.add_blocks(root['id'], [self.callout('연결 준비')])[0]
        self.manifest = {'version': 1, 'office_code': 'Z99', 'school_code': '0000001',
                         'school_name': '가상학교', 'academic_year': 2026,
                         'root_page_id': root['id'], 'agenda_data_source_id': self.ds,
                         'meals_block_id': meals['id'], 'status_block_id': status['id']}
        self.calendar = {'source': 'neis', 'office_code': 'Z99', 'school_code': '0000001',
                         'school_name': '가상학교', 'academic_year': 2026,
                         'start': '2026-03-01', 'end': '2027-02-28',
                         'fetched_at': '2026-09-30T00:00:00+00:00', 'rows': []}
        self.meals = {'source': 'neis-meals', 'office_code': 'Z99', 'school_code': '0000001',
                      'school_name': '가상학교', 'date': '2026-09-30', 'fetched_at': '2026-09-30T00:00:00+00:00',
                      'rows': [{'meal_code': '2', 'meal_name': '중식', 'menu': '가상밥\n가상국 (1.2.5)',
                                'calories': '700 Kcal', 'origin': '가상 원산지', 'nutrition': '가상 영양정보'}]}
        self.state, self.saved = {}, []
        self.addCleanup(patch.stopall)
        patch('teacher_planner.cloud_sync._today', return_value='2026-09-30').start()
        self.api.calls.clear()

    @staticmethod
    def callout(text):
        return {'object': 'block', 'type': 'callout', 'callout': {'rich_text': rich(text), 'color': 'gray_background'}}

    def add_event(self, day='2026-09-30', title='가상 행사'):
        row = {'date': day, 'title': title, 'description': '가상 설명', 'grades': [1, 2],
               'school_name': '가상학교', 'course': '고등학교', 'day_night': '주간', 'day_type': '해당없음'}
        row['external_id'] = event_id('Z99', '0000001', day, title, '주간', '고등학교')
        self.calendar['rows'].append(row)
        self.calendar['rows'].sort(key=lambda item: (item['date'], item['title']))
        return row

    def save(self, state):
        # JSON round trip verifies state can live in a durable document store.
        self.api.durable = json.loads(json.dumps(state))
        self.saved.append(copy.deepcopy(self.api.durable))

    def sync(self, maximum=8):
        self.api.enforce_checkpoint = True
        return sync_once(self.api, self.manifest, self.calendar, self.meals, self.state, self.save, maximum)

    def writes(self):
        return [call for call in self.api.calls if call[0] in ('POST', 'PATCH', 'DELETE')]

    def record_page(self, key):
        return self.api.objects['/pages/' + self.state['records'][key]['page_id']]

    def create_existing(self, row, *, end=None, archived=False, info=None):
        span = {'start': row['date']}
        if end:
            span['end'] = end
        return self.api.request('POST', '/pages', {
            'parent': {'type': 'data_source_id', 'data_source_id': self.ds},
            'properties': {'이름': {'title': rich(row['title'])}, '외부 ID': {'rich_text': rich(row['external_id'])},
                           '일정': {'date': span}, '학년도': {'number': 2026}, '보관': {'checkbox': archived},
                           '상태': {'select': {'name': '예정'}}},
            'children': [info] if info else []})

    def test_target_validation_handles_nested_meals_and_is_read_only(self):
        targets = validate_targets(self.api, self.manifest)
        self.assertEqual(self.ds, targets['agenda']['id'])
        self.assertEqual('callout', targets['meals']['type'])
        self.assertEqual([], self.writes())

    def test_cross_root_block_and_datasource_are_rejected_before_writes(self):
        for target in ('meals_block_id', 'agenda_data_source_id'):
            path = ('/blocks/' if target.startswith('meals') else '/data_sources/') + self.manifest[target]
            original = copy.deepcopy(self.api.objects[path]['parent'])
            self.api.objects[path]['parent'] = {'type': 'workspace', 'workspace': True}
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, '루트 밖'):
                self.sync()
            self.api.objects[path]['parent'] = original
        self.assertEqual([], self.writes())

    def test_wrong_types_archived_root_and_parent_cycle_fail(self):
        self.api.objects['/data_sources/' + self.ds]['properties']['일정']['type'] = 'rich_text'
        with self.assertRaisesRegex(ValueError, '필수 속성'):
            self.sync()
        self.api.objects['/data_sources/' + self.ds]['properties']['일정']['type'] = 'date'
        self.api.objects['/pages/' + self.manifest['root_page_id']]['in_trash'] = True
        with self.assertRaisesRegex(ValueError, '휴지통'):
            self.sync()
        self.api.objects['/pages/' + self.manifest['root_page_id']]['in_trash'] = False
        block = self.api.objects['/blocks/' + self.manifest['status_block_id']]
        block['parent'] = {'type': 'block_id', 'block_id': block['id']}
        with self.assertRaisesRegex(ValueError, '루트 밖'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_meals_and_status_require_dedicated_flat_callouts(self):
        for target in ('meals_block_id', 'status_block_id'):
            block = self.api.objects['/blocks/' + self.manifest[target]]
            block['has_children'] = True
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, '하위 내용이 없는'):
                self.sync()
            block['has_children'] = False
        self.assertEqual([], self.writes())

    def test_new_period_and_meals_are_checkpointed_then_unchanged_hash_skips_calendar(self):
        row = self.add_event('2026-09-29')
        self.add_event('2026-09-30')
        result = self.sync()
        self.assertTrue(result['complete'])
        self.assertEqual((2, 1, 1), (result['daily_record_count'], result['event_count'], result['changed']))
        self.assertEqual({'start': '2026-09-29', 'end': '2026-09-30'}, self.record_page(row['external_id'])['properties']['일정']['date'])
        self.assertFalse(self.state.get('pending'))
        info_id = self.state['records'][row['external_id']]['info_block_id']
        self.assertIn(SOURCE_MARKER, json.dumps(self.api.objects['/blocks/' + info_id]))
        status_id = self.manifest['status_block_id']
        self.assertFalse(any(path == '/blocks/' + status_id for _, path, _ in self.writes()))
        self.api.calls.clear()
        self.calendar['fetched_at'] = '2026-09-30T03:00:00+00:00'
        again = self.sync()
        self.assertTrue(again['calendar_unchanged'])
        self.assertEqual([], self.writes())
        self.assertEqual(self.state, self.api.durable)

    def test_chunk_checkpoint_continues_without_recreating_completed_events(self):
        for number in range(3):
            self.add_event(title=f'가상 행사 {number}')
        first = self.sync(1)
        self.assertFalse(first['complete'])
        self.assertEqual((1, 2), (first['processed'], first['remaining']))
        self.assertEqual(1, len(self.api.pages(self.ds)))
        self.assertFalse(self.sync(1)['complete'])
        self.assertTrue(self.sync(1)['complete'])
        self.assertEqual(3, len(self.api.pages(self.ds)))
        self.assertEqual(3, sum(method == 'POST' and path == '/pages' for method, path, _ in self.api.calls))

    def test_lost_page_create_response_is_recovered_by_external_id_and_marker(self):
        self.add_event()
        original = self.api.request

        def lose(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'POST' and path == '/pages':
                raise NotionError('unknown create', 503)
            return result

        with patch.object(self.api, 'request', side_effect=lose), self.assertRaises(NotionError):
            self.sync()
        self.assertEqual('page', self.api.durable['pending']['kind'])
        self.state = copy.deepcopy(self.api.durable)
        self.api.calls.clear()
        self.assertTrue(self.sync()['complete'])
        self.assertEqual(1, len(self.api.pages(self.ds)))
        self.assertFalse(any(method == 'POST' and path == '/pages' for method, path, _ in self.writes()))

    def test_no_result_after_ambiguous_create_is_not_blindly_retried(self):
        self.add_event()
        original = self.api.request

        def fail(method, path, payload=None):
            if method == 'POST' and path == '/pages':
                raise NotionError('unknown before create', 503)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail), self.assertRaises(NotionError):
            self.sync()
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '다시 생성하지'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_duplicate_external_ids_are_rejected_before_any_mutation(self):
        row = self.add_event()
        self.create_existing(row)
        self.create_existing(row)
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '중복'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_storage_failure_prevents_first_remote_creation(self):
        self.add_event()

        def fail_pending(state):
            if state.get('pending'):
                raise RuntimeError('durable storage unavailable')
            self.save(state)

        with self.assertRaisesRegex(RuntimeError, 'durable'):
            sync_once(self.api, self.manifest, self.calendar, self.meals, self.state, fail_pending)
        self.assertEqual([], self.writes())

    def test_existing_period_is_adopted_and_archived_original_notes_remain_intact(self):
        first = self.add_event('2026-09-29')
        second = self.add_event('2026-09-30')
        survivor = self.create_existing(first, end='2026-09-30', info=self.callout('교사가 직접 적은 메모'))
        duplicate = self.create_existing(second, archived=True, info=self.callout('통합 전 일자별 메모'))
        original_duplicate = copy.deepcopy(self.api.objects['/pages/' + duplicate['id']])
        original_note = copy.deepcopy(self.api.objects['/blocks/' + self.api.block_children[survivor['id']][0]])
        self.api.calls.clear()
        result = self.sync()
        self.assertTrue(result['complete'])
        self.assertEqual(2, len(self.api.pages(self.ds)))
        self.assertEqual(survivor['id'], self.state['records'][first['external_id']]['page_id'])
        self.assertEqual(original_duplicate, self.api.objects['/pages/' + duplicate['id']])
        self.assertEqual(original_note, self.api.objects['/blocks/' + original_note['id']])
        self.assertFalse(any(method == 'POST' for method, _, _ in self.writes()))
        self.assertEqual(duplicate['id'], self.state['groups'][first['external_id']]['archived_pages'][0]['id'])

    def test_active_legacy_daily_duplicates_stop_without_auto_archive(self):
        first = self.add_event('2026-09-29')
        second = self.add_event('2026-09-30')
        self.create_existing(first)
        self.create_existing(second)
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '여러 개'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_all_archived_existing_period_is_adopted_without_unarchiving_it(self):
        first = self.add_event('2026-09-29')
        second = self.add_event('2026-09-30')
        page = self.create_existing(first, end='2026-09-30', archived=True)
        self.api.objects['/pages/' + page['id']]['properties']['일정']['date']['time_zone'] = None
        self.create_existing(second, archived=True)
        self.api.calls.clear()
        self.sync()
        self.assertTrue(self.record_page(first['external_id'])['properties']['보관']['checkbox'])
        self.assertEqual(2, len(self.api.pages(self.ds)))
        self.assertFalse(any(method == 'POST' for method, _, _ in self.writes()))

    def test_range_extension_preserves_canonical_id_relations_and_teacher_note(self):
        first = self.add_event('2026-09-29')
        self.sync()
        page = self.record_page(first['external_id'])
        manual = {'다음 행동': {'type': 'rich_text', 'rich_text': rich('내 후속 메모')},
                  '학급': {'type': 'relation', 'relation': [{'id': str(uuid4())}]},
                  '상태': {'type': 'select', 'select': {'name': '완료'}}}
        page['properties'].update(copy.deepcopy(manual))
        note = self.api.add_blocks(page['id'], [self.callout('NEIS처럼 보이는 내 메모')])[0]
        self.add_event('2026-09-28')
        self.add_event('2026-09-30')
        self.api.calls.clear()
        self.sync()
        self.assertEqual(1, len(self.api.pages(self.ds)))
        self.assertEqual({'start': '2026-09-28', 'end': '2026-09-30'}, page['properties']['일정']['date'])
        self.assertEqual(manual, {key: page['properties'][key] for key in manual})
        self.assertEqual(note, self.api.objects['/blocks/' + note['id']])
        self.assertEqual(first['external_id'], text_property(page, '외부 ID'))

    def test_split_shrink_and_bootstrap_larger_period_are_rejected(self):
        first = self.add_event('2026-09-28')
        self.add_event('2026-09-29')
        self.add_event('2026-09-30')
        self.sync()
        self.calendar['rows'] = [row for row in self.calendar['rows'] if row['date'] != '2026-09-29']
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '축소·분리'):
            self.sync()
        self.assertEqual([], self.writes())
        self.state = {}  # Fresh OAuth enrollment must detect the live larger period too.
        with self.assertRaisesRegex(ValueError, '축소·분리'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_changed_owned_source_block_updates_and_other_callouts_are_preserved(self):
        row = self.add_event()
        self.sync()
        page = self.record_page(row['external_id'])
        note = self.api.add_blocks(page['id'], [self.callout('교사 기록')])[0]
        row['description'] = '바뀐 가상 설명'
        row['grades'] = [3]
        self.api.calls.clear()
        self.sync()
        self.assertEqual(note, self.api.objects['/blocks/' + note['id']])
        owned_id = self.state['records'][row['external_id']]['info_block_id']
        self.assertIn('바뀐 가상 설명', json.dumps(self.api.objects['/blocks/' + owned_id], ensure_ascii=False))
        self.assertEqual([('PATCH', '/blocks/' + owned_id)], [(m, p) for m, p, _ in self.writes()])

    def test_exact_legacy_source_is_reused_but_similar_user_note_is_never_rewritten(self):
        row = self.add_event()
        group = grouped_schedule(self.calendar, {'academic_year': 2026})[0]
        page = self.create_existing(row, info=source_info(self.calendar, group))
        original_info_id = self.api.block_children[page['id']][0]
        self.api.calls.clear()
        self.sync()
        self.assertEqual(original_info_id, self.state['records'][row['external_id']]['info_block_id'])
        self.assertEqual(1, len(self.api.block_children[page['id']]))

    def test_lost_info_append_response_recovers_without_second_callout(self):
        row = self.add_event()
        page = self.create_existing(row)
        original = self.api.request

        def lose(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH' and path.endswith('/children'):
                raise NotionError('unknown append', 503)
            return result

        with patch.object(self.api, 'request', side_effect=lose), self.assertRaises(NotionError):
            self.sync()
        self.api.calls.clear()
        self.sync()
        self.assertEqual(1, len(self.api.block_children[page['id']]))
        self.assertFalse(any(path.endswith('/children') for _, path, _ in self.writes()))

    def test_lost_owned_patch_response_is_idempotently_reconciled(self):
        row = self.add_event()
        self.sync()
        target = '/blocks/' + self.state['records'][row['external_id']]['info_block_id']
        row['description'] = '새 원본 설명'
        original = self.api.request

        def lose(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH' and path == target:
                raise NotionError('unknown update', 503)
            return result

        with patch.object(self.api, 'request', side_effect=lose), self.assertRaises(NotionError):
            self.sync()
        self.assertIn('mutation', self.api.durable)
        self.state = copy.deepcopy(self.api.durable)
        self.api.calls.clear()
        self.assertTrue(self.sync()['complete'])
        self.assertEqual([], self.writes())
        self.assertNotIn('mutation', self.state)

    def test_empty_meals_are_legitimate_but_malformed_stale_or_other_school_fail_before_writes(self):
        original = copy.deepcopy(self.meals)
        self.meals.update(rows=[], school_name='')
        self.assertTrue(self.sync()['complete'])
        text = json.dumps(self.api.objects['/blocks/' + self.manifest['meals_block_id']], ensure_ascii=False)
        self.assertIn('미실시로 단정하지 않습니다', text)
        for update in ({'rows': None}, {'date': '2026-09-29'}, {'school_code': '0000002'}, {'school_name': '다른 학교'}):
            self.meals = {**copy.deepcopy(original), **update}
            self.api.calls.clear()
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.sync()
            self.assertEqual([], self.writes())

    def test_changed_manifest_binding_and_partial_calendar_stop_before_writes(self):
        self.add_event()
        self.sync()
        self.calendar['start'] = '2026-09-01'
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '전체 학년도'):
            self.sync()
        self.calendar['start'] = '2026-03-01'
        self.state['binding'] = 'other-school-and-workspace'
        with self.assertRaisesRegex(ValueError, '다른 학교'):
            self.sync()
        self.assertEqual([], self.writes())

    def test_midnight_before_meal_write_keeps_previous_meal_and_next_day_can_resume(self):
        original = copy.deepcopy(self.api.objects['/blocks/' + self.manifest['meals_block_id']])
        with patch('teacher_planner.cloud_sync._today', side_effect=['2026-09-30', '2026-10-01']):
            with self.assertRaisesRegex(ValueError, '날짜가 바뀌었'):
                self.sync()
        self.assertEqual(original, self.api.objects['/blocks/' + self.manifest['meals_block_id']])
        self.assertNotIn('meals_date', self.state)
        self.assertEqual([], self.writes())
        self.meals.update(date='2026-10-01', fetched_at='2026-10-01T00:00:00+00:00')
        with patch('teacher_planner.cloud_sync._today', return_value='2026-10-01'):
            self.assertTrue(self.sync()['complete'])

    def test_empty_calendar_retains_old_pages_and_missing_events(self):
        row = self.add_event()
        self.sync()
        original = copy.deepcopy(self.record_page(row['external_id']))
        self.calendar.update(rows=[], school_name='')
        self.api.calls.clear()
        result = self.sync()
        self.assertTrue(result['complete'])
        self.assertEqual(0, result['event_count'])
        self.assertEqual(original, self.record_page(row['external_id']))
        self.assertEqual([], self.writes())


if __name__ == '__main__':
    unittest.main()
