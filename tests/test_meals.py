import copy
import io
import json
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from teacher_planner.blocks import BlockJournal
from teacher_planner.cli import main, verify_remote
from teacher_planner.client import NotionError
from teacher_planner.extras_cli import watch_delay
from teacher_planner.extras import verify_extras
from teacher_planner.install import Journal, compact, fingerprint, install
from teacher_planner.meals import MEAL_KEY, SOURCE_URL, meal_block, update_meals
from teacher_planner.model import config, rich
from teacher_planner.workspace import callout
from test_planner import FakeNotion

ROOT = Path(__file__).resolve().parents[1]


def column_reflow(api, state):
    """Represent an external Notion UI move while preserving the owned meal ID."""
    root = state['objects']['root']['id']
    row = api.add_blocks(root, [{'object': 'block', 'type': 'column_list', 'column_list': {}}])[0]
    column = api.add_blocks(row['id'], [{'object': 'block', 'type': 'column', 'column': {'width_ratio': .4}}])[0]
    meal = state['objects'][MEAL_KEY]['id']
    api.block_children[root].remove(meal)
    api.block_children[column['id']] = [meal]
    api.objects['/blocks/' + meal]['parent'] = {'type': 'block_id', 'block_id': column['id']}
    api.objects['/blocks/' + column['id']]['has_children'] = True
    return row, column


class MealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / '.local'
        self.path = self.private / 'state.json'
        self.cfg = self.private / 'config.json'
        self.c = config(ROOT / 'config.example.json')
        self.api = FakeNotion()
        root = self.api.request('POST', '/pages', {'parent': {'type': 'page_id', 'page_id': self.api.parent},
                    'properties': {'title': {'title': rich('가상 수첩')}}})
        j = Journal(self.path, self.api)
        j.data.update(config=self.c, identity=self.api.identity, complete=True,
                      objects={'root': compact(root)}, databases={})
        j.save()
        BlockJournal(j).append(MEAL_KEY, root['id'], callout('급식 미조회'))
        self.cfg.write_text(json.dumps(self.c))
        self.snapshot = {'source': 'neis-meals', 'office_code': 'Z99', 'school_code': '0000001',
                         'school_name': '가상학교', 'date': '2026-09-28', 'fetched_at': '2026-09-28T00:00:00+00:00',
                         'rows': [{'meal_code': '2', 'meal_name': '중식', 'menu': '가상밥\n가상국 (1.2.5)',
                                   'calories': '700 Kcal', 'origin': '가상 원산지', 'nutrition': '가상 영양정보', 'updated_at': ''}]}
        self.api.calls.clear()

    def state(self):
        return json.loads(self.path.read_text())

    def writes(self):
        return [c for c in self.api.calls if c[0] in ('POST', 'PATCH', 'DELETE')]

    def args(self, command='meals-sync', extra=()):
        args = [command, '--office-code', 'Z99', '--school-code', '0000001']
        if command == 'meals-sync':
            args += ['--config', str(self.cfg), '--state', str(self.path)]
        return args + list(extra)

    def invoke(self, args, **provider):
        with patch.dict('os.environ', {'NEIS_API_KEY': 'TEST_ONLY_KEY'}), \
                patch('teacher_planner.neis_meals.fetch_meals', return_value=self.snapshot, **provider), \
                patch('teacher_planner.extras_cli.Client', return_value=self.api), \
                patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            return main(args)

    def test_only_owned_home_block_changes_and_same_snapshot_is_noop(self):
        self.assertEqual(1, update_meals(self.api, self.path, self.snapshot))
        block = self.state()['objects'][MEAL_KEY]['id']
        method, target, body = self.writes()[0]
        self.assertEqual(('PATCH', '/blocks/' + block), (method, target))
        self.assertEqual({'rich_text'}, set(body['callout']))
        content = json.dumps(body, ensure_ascii=False)
        for value in ('오늘의 중식', '2026-09-28', '가상학교', '(1.2.5)', '700 Kcal', SOURCE_URL):
            self.assertIn(value, content)
        for value in ('가상 원산지', '가상 영양정보'):
            self.assertNotIn(value, content)
        self.api.calls.clear()
        self.assertEqual(0, update_meals(self.api, self.path, self.snapshot))
        self.assertEqual([], self.writes())

    def test_all_meals_snapshot_displays_only_lunch_without_changing_source(self):
        breakfast = {**self.snapshot['rows'][0], 'meal_code': '1', 'meal_name': '조식', 'menu': '가상 아침밥', 'calories': '500 Kcal'}
        dinner = {**self.snapshot['rows'][0], 'meal_code': '3', 'meal_name': '석식', 'menu': '가상 저녁밥', 'calories': '800 Kcal'}
        self.snapshot['rows'] = [breakfast, self.snapshot['rows'][0], dinner]
        before = copy.deepcopy(self.snapshot)
        content = json.dumps(meal_block(self.snapshot, 2026), ensure_ascii=False)
        for value in ('오늘의 중식', '가상밥', '가상국 (1.2.5)', '700 Kcal', SOURCE_URL):
            self.assertIn(value, content)
        for value in ('가상 아침밥', '가상 저녁밥', '500 Kcal', '800 Kcal', '원산지', '영양정보'):
            self.assertNotIn(value, content)
        self.assertEqual(before, self.snapshot)

    def test_missing_lunch_never_falls_back_to_breakfast_or_dinner(self):
        for code, name in (('1', '조식'), ('3', '석식')):
            with self.subTest(code=code):
                snapshot = {**self.snapshot, 'rows': [{**self.snapshot['rows'][0], 'meal_code': code, 'meal_name': name}]}
                content = json.dumps(meal_block(snapshot, 2026), ensure_ascii=False)
                self.assertIn('해당 날짜에 공개된 중식 정보가 없습니다.', content)
                self.assertNotIn('가상밥', content)
                self.assertNotIn('700 Kcal', content)

    def test_hidden_meal_and_fields_remain_validated_before_writes(self):
        for field in ('menu', 'calories', 'origin', 'nutrition'):
            for code, name in (('1', '조식'), ('2', '중식'), ('3', '석식')):
                with self.subTest(field=field, code=code):
                    invalid = {**self.snapshot['rows'][0], 'meal_code': code, 'meal_name': name, field: ['invalid']}
                    rows = [invalid] if code == '2' else [self.snapshot['rows'][0], invalid]
                    with self.assertRaises(ValueError):
                        update_meals(self.api, self.path, {**self.snapshot, 'rows': rows})
                    self.assertEqual([], self.api.calls)

    def test_empty_initial_result_does_not_bind_school_and_does_not_claim_no_meal(self):
        empty = {**self.snapshot, 'rows': [], 'school_name': ''}
        self.assertEqual(1, update_meals(self.api, self.path, empty))
        self.assertNotIn('neis_meals_source', self.state())
        self.assertIn('해당 날짜에 공개된 중식 정보가 없습니다.', json.dumps(self.writes()[0][2], ensure_ascii=False))
        self.assertIn('미실시로 단정하지 않습니다', json.dumps(self.writes()[0][2], ensure_ascii=False))
        update_meals(self.api, self.path, self.snapshot)
        binding = self.state()['neis_meals_source']
        update_meals(self.api, self.path, empty)
        self.assertEqual(binding, self.state()['neis_meals_source'])

    def test_school_binding_checks_both_meal_and_calendar_sources_before_writes(self):
        for field in ('neis_source', 'neis_meals_source'):
            j = Journal(self.path, self.api)
            j.data[field] = fingerprint({'office_code': 'Z99', 'school_code': '0000002', 'academic_year': 2026})
            j.save()
            before = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, '기존 NEIS'):
                update_meals(self.api, self.path, self.snapshot)
            self.assertEqual(before, self.path.read_bytes())
            self.assertEqual([], self.writes())
            j.data.pop(field)
            j.save()

    def test_invalid_snapshot_fails_before_notion_writes(self):
        cases = [{**self.snapshot, 'date': '2027-03-01'}, {**self.snapshot, 'fetched_at': '2026-09-28T09:00:00'},
                 {**self.snapshot, 'rows': self.snapshot['rows'] * 2},
                 {**self.snapshot, 'rows': [{**self.snapshot['rows'][0], 'menu': '가' * 20001}]},
                 {**self.snapshot, 'source': 'other'}]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot['date']), self.assertRaises(ValueError):
                update_meals(self.api, self.path, snapshot)
            self.assertEqual([], self.writes())

    def test_moved_changed_or_trashed_block_is_not_replaced(self):
        path = '/blocks/' + self.state()['objects'][MEAL_KEY]['id']
        original = copy.deepcopy(self.api.objects[path])
        for delta in ({'type': 'paragraph'}, {'in_trash': True}, {'parent': {'type': 'page_id', 'page_id': self.api.parent}}):
            self.api.objects[path] = {**original, **delta}
            with self.assertRaisesRegex(ValueError, '급식 블록'):
                update_meals(self.api, self.path, self.snapshot)
            self.assertEqual([], self.writes())

    def test_same_owned_callout_still_updates_inside_live_home_columns(self):
        state = self.state()
        _, column = column_reflow(self.api, state)
        meal = state['objects'][MEAL_KEY]['id']
        self.api.calls.clear()
        self.assertEqual(1, update_meals(self.api, self.path, self.snapshot))
        self.assertEqual([('PATCH', '/blocks/' + meal)], [(m, p) for m, p, _ in self.writes()])
        self.assertEqual(column['id'], self.api.objects['/blocks/' + meal]['parent']['block_id'])
        self.api.calls.clear()
        self.assertEqual(0, update_meals(self.api, self.path, self.snapshot))
        self.assertEqual([], self.writes())

    def test_column_ancestry_rejects_foreign_root_cycles_and_trashed_ancestors(self):
        state = self.state()
        row, column = column_reflow(self.api, state)
        cases = [('/blocks/' + row['id'], {'parent': {'type': 'page_id', 'page_id': self.api.parent}}),
                 ('/blocks/' + row['id'], {'parent': {'type': 'block_id', 'block_id': column['id']}}),
                 ('/blocks/' + row['id'], {'archived': True}),
                 ('/blocks/' + column['id'], {'in_trash': True}),
                 ('/blocks/' + column['id'], {'type': 'toggle'}),
                 ('/pages/' + state['objects']['root']['id'], {'in_trash': True})]
        for path, delta in cases:
            with self.subTest(delta=delta):
                original = copy.deepcopy(self.api.objects[path])
                self.api.objects[path].update(delta)
                self.api.calls.clear()
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ValueError, '급식 블록'):
                    update_meals(self.api, self.path, self.snapshot)
                self.assertEqual([], self.writes())
                self.assertEqual(before, self.path.read_bytes())
                self.assertLessEqual(len(self.api.calls), 6)
                self.api.objects[path] = original

    def test_column_ancestry_is_bounded(self):
        state = self.state()
        root = state['objects']['root']['id']
        parent = root
        for _ in range(35):
            parent = self.api.add_blocks(parent, [{'object': 'block', 'type': 'column', 'column': {}}])[0]['id']
        meal = state['objects'][MEAL_KEY]['id']
        self.api.objects['/blocks/' + meal]['parent'] = {'type': 'block_id', 'block_id': parent}
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '급식 블록'):
            update_meals(self.api, self.path, self.snapshot)
        self.assertEqual([], self.writes())
        self.assertLessEqual(len(self.api.calls), 34)

    def test_failed_or_lost_response_keeps_recoverable_block_without_duplicate(self):
        original = self.api.request
        def lost(method, path, body=None):
            result = original(method, path, body)
            if method == 'PATCH':
                raise NotionError('lost response')
            return result
        with patch.object(self.api, 'request', side_effect=lost), self.assertRaises(NotionError):
            update_meals(self.api, self.path, self.snapshot)
        self.assertNotIn('neis_meals_last_checked_at', self.state())
        self.api.calls.clear()
        self.assertEqual(0, update_meals(self.api, self.path, self.snapshot))
        self.assertEqual([], self.writes())

    def test_fetch_and_preview_need_no_notion_and_file_is_private(self):
        target = self.private / 'meals.json'
        self.assertEqual(0, self.invoke(self.args('neis-meals', ['--output', str(target)])))
        self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
        self.assertNotIn('TEST_ONLY_KEY', target.read_text())
        self.assertEqual(self.snapshot, json.loads(target.read_text()))
        before = self.path.read_bytes()
        self.assertEqual(0, self.invoke(self.args()))
        self.assertEqual([], self.api.calls)
        self.assertEqual(before, self.path.read_bytes())

    def test_cli_apply_and_identity_check(self):
        self.assertEqual(0, self.invoke(self.args(extra=['--apply'])))
        self.api.calls.clear()
        j = Journal(self.path, self.api)
        j.data['identity'] = 'different'
        j.save()
        self.assertEqual(1, self.invoke(self.args(extra=['--apply'])))
        self.assertEqual([], self.writes())

    def test_cli_error_never_replaces_previous_meal(self):
        update_meals(self.api, self.path, self.snapshot)
        before = self.path.read_bytes()
        self.api.calls.clear()
        self.assertEqual(1, self.invoke(self.args(extra=['--apply']), side_effect=ValueError('조회 오류')))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], self.api.calls)

    def test_watch_requires_apply_rejects_fixed_day_and_requeries_today(self):
        for flags in (['--watch'], ['--apply', '--watch', '--date', '2026-09-28'], ['--interval', '3599']):
            self.assertEqual(1, self.invoke(self.args(extra=flags)))
        tomorrow = {**self.snapshot, 'date': '2026-09-29', 'fetched_at': '2026-09-29T00:00:01+09:00'}
        with patch('teacher_planner.extras_cli.sleep', side_effect=[None, KeyboardInterrupt]):
            self.assertEqual(0, self.invoke(self.args(extra=['--apply', '--watch']), side_effect=[self.snapshot, tomorrow]))
        self.assertEqual('2026-09-29', self.state()['neis_meals_date'])
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_watch_wakes_at_seoul_midnight_even_for_six_hour_interval(self):
        class NearMidnight(datetime):
            @classmethod
            def now(cls, tz):
                return cls(2026, 9, 28, 23, 59, 59, tzinfo=tz)
        with patch('teacher_planner.extras_cli.datetime', NearMidnight):
            self.assertEqual(2, watch_delay(21600))
            self.assertEqual(1, watch_delay(21600, '2026-09-27'))

    def test_request_crossing_midnight_rechecks_new_day_promptly(self):
        class AfterMidnight(datetime):
            @classmethod
            def now(cls, tz):
                return cls(2026, 9, 29, 0, 0, 5, tzinfo=tz)
        with patch('teacher_planner.extras_cli.datetime', AfterMidnight):
            self.assertEqual(1, watch_delay(21600, '2026-09-28'))
            self.assertEqual(21600, watch_delay(21600, '2026-09-29'))


class ExtrasSetupTests(unittest.TestCase):
    def test_setup_after_column_reflow_preserves_ids_records_order_and_forms_link(self):
        with tempfile.TemporaryDirectory() as folder:
            path, cfg = Path(folder) / 'state.json', Path(folder) / 'config.json'
            c = config(ROOT / 'config.example.json')
            cfg.write_text(json.dumps(c))
            api = FakeNotion()
            install(api, c, api.parent, path)
            j = Journal(path, api)
            row, column = column_reflow(api, j.data)
            meal = j.data['extras']['meal_block_id']
            forms_link = j.data['extras']['forms_link_id']
            # A completed UI reflow records the column row, not its nested meal,
            # in the page's top-level order. Setup must not reverse that record.
            order = j.data['dashboard']['page_order']['home']
            order.remove(meal)
            order.append(row['id'])
            order.remove(forms_link)
            order.insert(2, forms_link)
            api.objects['/blocks/' + meal]['callout']['rich_text'] = rich('이미 갱신된 오늘의 중식')
            api.objects['/blocks/' + forms_link]['paragraph']['rich_text'][0]['text']['content'] = '나의 양식 모음'
            j.save()
            before_objects = copy.deepcopy(api.objects)
            before_state = copy.deepcopy(j.data)
            api.calls.clear()
            args = ['setup-extras', '--config', str(cfg), '--state', str(path), '--apply']
            with patch('teacher_planner.extras_cli.Client', return_value=api), patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(0, main(args))
            after = Journal(path, api).data
            self.assertEqual(before_state, after)
            self.assertEqual(before_objects, api.objects)
            self.assertEqual(column['id'], api.objects['/blocks/' + meal]['parent']['block_id'])
            self.assertEqual([], [call for call in api.calls if call[0] in ('POST', 'PATCH', 'DELETE')])
            self.assertEqual([], verify_extras(api, after))

    def test_setup_rejects_moved_or_retargeted_existing_forms_link_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path, cfg = Path(folder) / 'state.json', Path(folder) / 'config.json'
            c = config(ROOT / 'config.example.json')
            cfg.write_text(json.dumps(c))
            api = FakeNotion()
            install(api, c, api.parent, path)
            state = Journal(path, api).data
            link_path = '/blocks/' + state['extras']['forms_link_id']
            original = copy.deepcopy(api.objects[link_path])
            bad_target = copy.deepcopy(original['paragraph'])
            bad_target['rich_text'][0]['text']['link']['url'] = 'https://example.org/other'
            for delta in ({'parent': {'type': 'page_id', 'page_id': api.parent}},
                          {'paragraph': bad_target}, {'in_trash': True}):
                with self.subTest(delta=delta):
                    api.objects[link_path] = {**copy.deepcopy(original), **delta}
                    api.calls.clear()
                    before = path.read_bytes()
                    args = ['setup-extras', '--config', str(cfg), '--state', str(path), '--apply']
                    with patch('teacher_planner.extras_cli.Client', return_value=api), \
                            patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                        self.assertEqual(1, main(args))
                    self.assertEqual(before, path.read_bytes())
                    self.assertEqual([], [call for call in api.calls if call[0] in ('POST', 'PATCH', 'DELETE')])

    def test_existing_install_gets_only_additions_and_second_setup_preserves_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            cfg = Path(folder) / 'config.json'
            c = config(ROOT / 'config.example.json')
            cfg.write_text(json.dumps(c))
            api = FakeNotion()
            with patch('teacher_planner.extras.install_extras'):
                install(api, c, api.parent, path)
            before = Journal(path, api).data['databases']
            args = ['setup-extras', '--config', str(cfg), '--state', str(path), '--apply']
            api.calls.clear()
            with patch('teacher_planner.extras_cli.Client', return_value=api), patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(0, main(args))
                state = Journal(path, api).data
                self.assertEqual(before, state['databases'])
                self.assertEqual(6, len(state['forms']['entries']))
                meal_content = json.dumps(api.objects['/blocks/' + state['extras']['meal_block_id']], ensure_ascii=False)
                self.assertIn('오늘의 중식', meal_content)
                self.assertNotIn('조식·중식·석식', meal_content)
                self.assertEqual([], verify_remote(api, state))
                self.assertFalse(any(path.startswith(('/data_sources/', '/views')) or path == '/databases'
                                     for method, path, body in api.calls if method in ('POST', 'PATCH')))
                api.calls.clear()
                self.assertEqual(0, main(args))
                self.assertEqual([], [call for call in api.calls if call[0] in ('POST', 'PATCH')])
