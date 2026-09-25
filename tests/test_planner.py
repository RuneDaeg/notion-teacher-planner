import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

from teacher_planner.cli import main, validate_recovery, verify_remote
from teacher_planner.client import Client, NotionError
from teacher_planner.install import Journal, install, locked, page_id
from teacher_planner.model import blueprint, config
from teacher_planner.timetable import apply_changes, changes, read_rows, text_property

ROOT = Path(__file__).resolve().parents[1]


class FakeNotion:
    """Stateful remote boundary: databases and sources deliberately have different IDs."""
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.identity = str(uuid4())
        self.parent = str(uuid4())
        self.objects['/pages/' + self.parent] = {'id': self.parent}

    def request(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == '/users/me':
            return {'id': self.identity}
        if method == 'GET':
            return copy.deepcopy(self.objects[path])
        if method == 'POST':
            obj_id = str(uuid4())
            obj = {'id': obj_id, 'object': path.strip('/').rstrip('s'), 'url': 'https://www.notion.so/' + obj_id, **copy.deepcopy(payload)}
            if path == '/databases':
                ds = str(uuid4())
                obj['data_sources'] = [{'id': ds}]
                props = self.prop_schema(payload['initial_data_source']['properties'])
                self.objects['/data_sources/' + ds] = {'id': ds, 'properties': props}
            if path == '/pages':
                for prop in obj['properties'].values():
                    prop['type'] = next(iter(prop))
            if path == '/views':
                if 'database_id' in payload:
                    obj['parent'] = {'database_id': payload['database_id']}
                else:
                    obj['parent'] = {'database_id': str(uuid4())}
            self.objects[path + '/' + obj_id] = obj
            return copy.deepcopy(obj)
        if method == 'PATCH':
            obj = self.objects[path]
            props = copy.deepcopy(payload['properties'])
            if path.startswith('/data_sources/'):
                props = self.prop_schema(props)
            else:
                for prop in props.values():
                    prop['type'] = next(iter(prop))
            obj['properties'].update(props)
            return copy.deepcopy(obj)
        raise AssertionError((method, path))

    def prop_schema(self, props):
        return {n: {'id': str(uuid4())[:8], 'type': next(iter(v)), **copy.deepcopy(v)} for n, v in props.items()}

    def pages(self, ds, filter_=None):
        # Fixtures contain one academic year. Returning all records is conservative
        # for duplicate detection and does not simulate Notion filter semantics.
        return [copy.deepcopy(v) for k, v in self.objects.items() if k.startswith('/pages/') and v.get('parent', {}).get('data_source_id') == ds]


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()

    def install(self):
        return install(self.api, self.c, self.api.parent, self.path)

    def test_complete_install_has_correct_relations_and_week_month(self):
        self.install()
        state = json.loads(self.path.read_text())
        self.assertEqual(14, len(state['databases']))
        self.assertEqual(45, sum(1 for k in state['objects'] if k.startswith(('view:', 'home:'))))
        student_ds = state['databases']['students']['data_source_id']
        props = self.api.objects['/data_sources/' + student_ds]['properties']
        self.assertEqual(state['databases']['classes']['data_source_id'], props['학급']['relation']['data_source_id'])
        self.assertEqual([], verify_remote(self.api, state))
        for key, span in [('view:weekly', 'week'), ('view:monthly', 'month')]:
            view = self.api.objects['/views/' + state['objects'][key]['id']]
            self.assertEqual(span, view['configuration']['view_range'])
            self.assertEqual(state['databases']['agenda']['data_source_id'], view['data_source_id'])

    def test_second_install_creates_no_duplicates(self):
        self.install()
        count = len(self.api.objects)
        first_posts = sum(c[0] == 'POST' for c in self.api.calls)
        self.install()
        self.assertEqual(count, len(self.api.objects))
        self.assertEqual(first_posts, sum(c[0] == 'POST' for c in self.api.calls))

    def test_optional_modules_are_not_installed_when_disabled(self):
        self.c['modules'] = dict.fromkeys(self.c['modules'], False)
        self.install()
        state = json.loads(self.path.read_text())
        self.assertEqual(9, len(state['databases']))
        self.assertNotIn('attendance', state['databases'])

    def test_demo_is_opt_in(self):
        self.install()
        state = json.loads(self.path.read_text())
        self.assertFalse(any(k.startswith('seed:demo:') for k in state['objects']))

    def test_demo_rows_have_valid_student_relationships(self):
        self.c['demo'] = True
        self.install()
        state = json.loads(self.path.read_text())
        counseling = self.api.objects['/pages/' + state['objects']['seed:demo:counseling']['id']]
        self.assertEqual(state['objects']['seed:demo:student']['id'], counseling['properties']['학생']['relation'][0]['id'])

    def test_config_or_parent_change_refused(self):
        self.install()
        self.c['academic_year'] += 1
        with self.assertRaisesRegex(ValueError, '다릅니다'):
            self.install()

    def test_ambiguous_write_is_journaled_and_blocks_retry(self):
        j = Journal(self.path, self.api)
        with patch.object(self.api, 'request', side_effect=NotionError('timeout')):
            with self.assertRaises(NotionError):
                j.create('root', '/pages', {'parent': {'page_id': self.api.parent}})
        reloaded = Journal(self.path, self.api)
        self.assertIn('pending', reloaded.data)
        with self.assertRaisesRegex(ValueError, '불확실'):
            reloaded.create('root', '/pages', {})

    def test_definitive_rejection_allows_retry(self):
        j = Journal(self.path, self.api)
        with patch.object(self.api, 'request', side_effect=NotionError('bad', 403)):
            with self.assertRaises(NotionError):
                j.create('root', '/pages', {})
        self.assertNotIn('pending', Journal(self.path, self.api).data)

    def test_process_lock_prevents_second_writer(self):
        with locked(self.path):
            with self.assertRaises(ValueError):
                with locked(self.path):
                    pass
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_invalid_recovery_does_not_accept_unrelated_page(self):
        pending = {'endpoint': '/pages', 'payload': {'parent': {'page_id': 'A'}, 'properties': {'title': {'title': []}}}}
        with self.assertRaises(ValueError):
            validate_recovery(pending, {'parent': {'page_id': 'B'}})

    def test_import_updates_and_cancels_without_duplicate_rows(self):
        self.install()
        rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        state = json.loads(self.path.read_text())
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        first_count = len(self.api.objects)
        rows[0]['room'] = '202'
        rows[1]['status'] = '휴강'
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        self.assertEqual(first_count, len(self.api.objects))
        tt = self.api.pages(state['databases']['timetable']['data_source_id'])
        self.assertEqual(2, len(tt))
        self.assertEqual('202', text_property(tt[0], '교실'))
        agenda = self.api.pages(state['databases']['agenda']['data_source_id'])
        self.assertEqual('취소', agenda[1]['properties']['상태']['select']['name'])
        self.assertEqual(agenda[0]['id'], tt[0]['properties']['업무·일정']['relation'][0]['id'])
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows[:1]))
        self.assertEqual(2, len(self.api.pages(state['databases']['timetable']['data_source_id'])))

    def test_duplicate_remote_external_ids_fail_before_writes(self):
        self.install()
        rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        state = json.loads(self.path.read_text())
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        pages = self.api.pages(state['databases']['timetable']['data_source_id'])
        self.api.objects['/pages/' + str(uuid4())] = pages[0]
        calls_before = len(self.api.calls)
        with self.assertRaisesRegex(ValueError, '중복'):
            changes(self.api, self.c, state, rows)
        self.assertEqual(calls_before, len(self.api.calls))

    def test_partial_import_can_resume_existing_agenda(self):
        self.install()
        rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        state = json.loads(self.path.read_text())
        ops = changes(self.api, self.c, state, rows)
        apply_changes(self.api, self.path, ops[:1])
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        self.assertEqual(2, len(self.api.pages(state['databases']['agenda']['data_source_id'])))
        self.assertEqual(2, len(self.api.pages(state['databases']['timetable']['data_source_id'])))

    def test_import_preserves_manual_priority_and_archive_flag(self):
        self.install()
        rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        state = json.loads(self.path.read_text())
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        obj = self.api.pages(state['databases']['agenda']['data_source_id'])[0]
        props = self.api.objects['/pages/' + obj['id']]['properties']
        props['우선순위'] = {'type': 'select', 'select': {'name': 'P1 지금'}}
        props['보관'] = {'type': 'checkbox', 'checkbox': True}
        apply_changes(self.api, self.path, changes(self.api, self.c, state, rows))
        self.assertEqual('P1 지금', props['우선순위']['select']['name'])
        self.assertTrue(props['보관']['checkbox'])

    def test_remote_verification_detects_wrong_calendar_range(self):
        self.install()
        state = json.loads(self.path.read_text())
        view = self.api.objects['/views/' + state['objects']['view:weekly']['id']]
        view['configuration']['view_range'] = 'month'
        self.assertTrue(any('view:weekly' in issue for issue in verify_remote(self.api, state)))

    def test_csv_and_json_are_equivalent_and_use_seoul_offset(self):
        rows = read_rows(ROOT / 'examples/timetable.csv', self.c)
        self.assertEqual(rows, read_rows(ROOT / 'examples/timetable.json', self.c))
        self.assertTrue(rows[0]['date_range']['start'].endswith('+09:00'))

    def invalid_rows(self, mutate, pattern):
        data = json.loads((ROOT / 'examples/timetable.json').read_text())
        mutate(data)
        path = Path(self.temp.name) / 'rows.json'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, pattern):
            read_rows(path, self.c)

    def test_duplicate_dated_slot_rejected(self):
        self.invalid_rows(lambda rows: rows.append(rows[0]), '중복')

    def test_overlapping_lessons_rejected(self):
        self.invalid_rows(lambda rows: rows[1].update(date=rows[0]['date'], start='09:30'), '겹칩니다')

    def test_unknown_class_rejected(self):
        self.invalid_rows(lambda rows: rows[0].update(class_name='없는 반'), '설정에 없는')

    def test_wrong_academic_year_rejected(self):
        self.invalid_rows(lambda rows: rows[0].update(date='2026-02-01'), '학년도')

    def test_end_before_start_rejected(self):
        self.invalid_rows(lambda rows: rows[0].update(end='08:00'), '종료')

    def test_plan_and_import_validation_need_no_token_or_network(self):
        with patch.dict('os.environ', {}, clear=True), patch('teacher_planner.cli.Client', side_effect=AssertionError('network')), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(0, main(['plan', '--config', str(ROOT / 'config.example.json')]))
            self.assertEqual(0, main(['import-timetable', str(ROOT / 'examples/timetable.csv'), '--config', str(ROOT / 'config.example.json')]))

    def test_notion_url_ignores_view_query(self):
        id_ = str(uuid4())
        self.assertEqual(id_, page_id('https://notion.so/Name-' + id_.replace('-', '') + '?v=' + str(uuid4())))

    def test_all_relations_and_view_properties_resolve(self):
        b = blueprint()
        dbs = {d['key']: d for d in b['databases']}
        for d in dbs.values():
            self.assertEqual(1, sum(v['type'] == 'title' for v in d['properties'].values()))
            for prop in d['properties'].values():
                if prop['type'] == 'relation':
                    self.assertIn(prop['target'], dbs)
        for v in b['views']:
            for name in v.get('show', []) + [v[k] for k in ('date', 'group') if k in v]:
                self.assertIn(name, dbs[v['source']]['properties'])


class ClientTests(unittest.TestCase):
    def test_request_uses_pinned_version_and_bearer(self):
        class Opener:
            def open(self, req, timeout):
                self.req = req
                return io.BytesIO(b'{"id":"ok"}')
        opener = Opener()
        client = Client('fake-test-token', opener=opener, sleep=lambda _: None)
        self.assertEqual('ok', client.request('GET', '/users/me')['id'])
        self.assertEqual('2026-03-11', opener.req.get_header('Notion-version'))
        self.assertEqual('Bearer fake-test-token', opener.req.get_header('Authorization'))

    def test_post_500_is_not_retried_and_does_not_leak_body(self):
        class Opener:
            count = 0
            def open(self, req, timeout):
                self.count += 1
                raise HTTPError(req.full_url, 500, 'error', {}, io.BytesIO(b'secret-student-text'))
        opener = Opener()
        with self.assertRaises(NotionError) as result:
            Client('fake', opener=opener, sleep=lambda _: None).request('POST', '/pages', {})
        self.assertEqual(1, opener.count)
        self.assertNotIn('secret-student-text', str(result.exception))

    def test_429_honors_retry_after(self):
        class Opener:
            count = 0
            def open(self, req, timeout):
                self.count += 1
                if self.count == 1:
                    raise HTTPError(req.full_url, 429, 'rate', {'Retry-After': '2'}, None)
                return io.BytesIO(b'{"id":"ok"}')
        delays = []
        opener = Opener()
        Client('fake', opener=opener, sleep=delays.append).request('POST', '/pages', {})
        self.assertEqual(2, opener.count)
        self.assertIn(2, delays)

    def test_query_paginates_all_results(self):
        client = Client('fake', sleep=lambda _: None)
        results = [{'results': [{'id': 'a'}], 'has_more': True, 'next_cursor': 'cursor'}, {'results': [{'id': 'b'}], 'has_more': False}]
        with patch.object(client, 'request', side_effect=results) as req:
            self.assertEqual(['a', 'b'], [r['id'] for r in client.pages('ds')])
        self.assertEqual('cursor', req.call_args_list[1].args[2]['start_cursor'])


if __name__ == '__main__':
    unittest.main()
