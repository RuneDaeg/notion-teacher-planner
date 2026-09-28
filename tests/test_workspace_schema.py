"""Offline schema contracts and boundary examples, not a live Notion formula check."""
import ast
import operator
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from teacher_planner.model import blueprint, config, schema, selected, view_payload

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 27)
NEW_VIEWS = {
    'teacher_today', 'teacher_changes', 'counseling_today', 'attendance_today',
    'attendance_unconfirmed', 'students_gallery', 'students_observation',
    'todo_p1', 'todo_done', 'due_soon', 'progress_attention',
    'assessments_upcoming', 'projects_board',
}
OLD_SPECIAL_VIEWS = {
    'inbox', 'todo', 'weekly', 'monthly', 'deadlines', 'by_work', 'overdue',
    'undated', 'teacher_week', 'progress_class', 'progress_subject',
    'staff_department', 'semester_1', 'semester_2',
}
DB_KEYS = {
    'classes', 'students', 'areas', 'projects', 'resources', 'agenda',
    'timetable', 'lessons', 'counseling', 'attendance', 'submissions',
    'assessments', 'contacts', 'meetings', 'staff', 'accounts',
}


def formula_value(properties, values, name):
    """Evaluate the small documented function subset used in these new formulas.

    Parsing the actual schema expressions catches guard, sign, range, and property
    reference regressions. This intentionally does not emulate the Notion API.
    """
    expression = properties[name]['expression']
    expression = re.sub(r'\b(if|and|or)\(', r'fn_\1(', expression)
    expression = re.sub(r'\btrue\b', 'True', expression)
    expression = re.sub(r'\bfalse\b', 'False', expression)

    def prop(key):
        if properties[key]['type'] == 'formula':
            return formula_value(properties, values, key)
        return values.get(key)

    def start(value):
        if isinstance(value, dict):
            value = value['start']
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value

    def format_date(value, pattern):
        assert pattern == 'YYYY-MM-DD'
        return value.strftime('%Y-%m-%d')

    functions = {
        'prop': prop, 'today': lambda: TODAY, 'dateStart': start,
        'formatDate': format_date, 'parseDate': date.fromisoformat,
        'dateBetween': lambda first, second, unit: (first - second).days if unit == 'days' else None,
        'empty': lambda value: value is None or value == '' or value == [] or value == 0,
        'format': str, 'abs': abs,
    }
    comparisons = {ast.Eq: operator.eq, ast.Gt: operator.gt, ast.Lt: operator.lt,
                   ast.GtE: operator.ge, ast.LtE: operator.le}

    def evaluate(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Call):
            name = node.func.id
            if name == 'fn_if':
                return evaluate(node.args[1] if evaluate(node.args[0]) else node.args[2])
            if name == 'fn_and':
                return all(evaluate(arg) for arg in node.args)
            if name == 'fn_or':
                return any(evaluate(arg) for arg in node.args)
            return functions[name](*(evaluate(arg) for arg in node.args))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            return comparisons[type(node.ops[0])](evaluate(node.left), evaluate(node.comparators[0]))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return evaluate(node.left) + evaluate(node.right)
        raise AssertionError(f'Unsupported formula expression: {ast.dump(node)}')

    return evaluate(ast.parse(expression, mode='eval').body)


def filter_matches(node, values):
    if 'and' in node:
        return all(filter_matches(child, values) for child in node['and'])
    if 'or' in node:
        return any(filter_matches(child, values) for child in node['or'])
    actual = values.get(node['property'])
    condition = next(value for key, value in node.items() if key != 'property')
    if 'checkbox' in condition:  # Formula checkbox filter.
        condition = condition['checkbox']
    if 'equals' in condition:
        return actual == condition['equals']
    if 'does_not_equal' in condition:
        return actual != condition['does_not_equal']
    if 'is_not_empty' in condition:
        return bool(actual)
    raise AssertionError(condition)


def filter_depth(node):
    for compound in ('and', 'or'):
        if compound in node:
            return 1 + max(filter_depth(child) for child in node[compound])
    return 0


class WorkspaceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.b = blueprint()
        self.dbs = {d['key']: d for d in self.b['databases']}
        self.views = {v['key']: v for v in self.b['views']}

    def test_preserves_existing_database_and_view_identities(self):
        self.assertEqual(DB_KEYS, set(self.dbs))
        old = OLD_SPECIAL_VIEWS | {prefix + key for key in DB_KEYS for prefix in ('active_', 'archive_')}
        self.assertEqual(old | NEW_VIEWS, set(self.views))
        self.assertEqual(59, len(self.b['views']))
        self.assertEqual(16, len(self.b['databases']))

    def test_all_view_and_formula_references_exist_in_their_source(self):
        for view in self.b['views']:
            properties = self.dbs[view['source']]['properties']
            names = view.get('show', []) + [s['property'] for s in view.get('sorts', [])]
            names += [view[key] for key in ('date', 'group') if key in view]

            def referenced(node):
                if 'property' in node:
                    return [node['property']]
                return [name for child in node.get('and', node.get('or', [])) for name in referenced(child)]

            names += referenced(view['filter'])
            with self.subTest(view=view['key']):
                self.assertTrue(set(names) <= set(properties))
        for db in self.dbs.values():
            for prop in db['properties'].values():
                if prop['type'] == 'formula':
                    self.assertTrue(set(re.findall(r'prop\("([^\"]+)"\)', prop['expression'])) <= set(db['properties']))

    def test_today_formulas_handle_empty_ranges_and_time_without_fixed_dates(self):
        for db, field, computed in [('timetable', '수업일', '오늘 수업'),
                                    ('counseling', '상담일', '오늘 상담'),
                                    ('attendance', '날짜', '오늘 출결')]:
            properties = self.dbs[db]['properties']
            for raw, expected in [(None, False), ('', False), ('2026-09-27', True),
                                  ('2026-09-27T23:59:00+09:00', True),
                                  ('2026-09-26T23:59:00+09:00', False),
                                  ({'start': '2026-09-27', 'end': '2026-09-28'}, True),
                                  ({'start': '2026-09-26', 'end': '2026-09-27'}, False)]:
                with self.subTest(db=db, raw=raw):
                    result = formula_value(properties, {field: raw}, computed)
                    self.assertIs(expected, result)

    def test_deadline_and_assessment_boundaries_have_correct_days_and_labels(self):
        for db, field, count, label, flag, limit in [
                ('agenda', '마감', '마감 남은 일', 'D-Day', '마감 임박', 7),
                ('assessments', '평가일', '평가 남은 일', '평가 D-Day', '평가 예정', 30)]:
            properties = self.dbs[db]['properties']
            for offset in (-1, 0, 1, limit, limit + 1):
                values = {field: (TODAY + timedelta(days=offset)).isoformat() + 'T23:59:00+09:00', '상태': '준비'}
                with self.subTest(db=db, offset=offset):
                    self.assertEqual(offset, formula_value(properties, values, count))
                    self.assertIs(type(formula_value(properties, values, count)), int)
                    expected = 'D-Day' if offset == 0 else ('D-' + str(offset) if offset > 0 else 'D+' + str(abs(offset)))
                    self.assertEqual(expected, formula_value(properties, values, label))
                    self.assertIs(0 <= offset <= limit, formula_value(properties, values, flag))

    def test_no_false_deadline_for_empty_completed_or_cancelled_records(self):
        for db, field, count, label, flag in [
                ('agenda', '마감', '마감 남은 일', 'D-Day', '마감 임박'),
                ('assessments', '평가일', '평가 남은 일', '평가 D-Day', '평가 예정')]:
            properties = self.dbs[db]['properties']
            self.assertIn('취소', properties['상태']['options'])
            for values in [{field: None, '상태': '준비'}, {field: '', '상태': '준비'},
                           {field: TODAY.isoformat(), '상태': '완료'},
                           {field: TODAY.isoformat(), '상태': '취소'}]:
                with self.subTest(db=db, values=values):
                    self.assertEqual(0, formula_value(properties, values, count))
                    self.assertEqual('', formula_value(properties, values, label))
                    self.assertIs(False, formula_value(properties, values, flag))

    def test_filters_exclude_archived_and_finished_rows_and_keep_attention_cases(self):
        cases = [
            ('teacher_changes', {'상태': '변경'}, True),
            ('teacher_changes', {'상태': '휴강'}, True),
            ('teacher_changes', {'상태': '예정'}, False),
            ('students_observation', {'집중 관찰': True}, True),
            ('students_observation', {'관찰 태그': ['학습']}, True),
            ('students_observation', {'관찰 태그': [], '집중 관찰': False}, False),
            ('attendance_unconfirmed', {'오늘 출결': True, '확인': False}, True),
            ('attendance_unconfirmed', {'오늘 출결': True, '확인': True}, False),
            ('attendance_unconfirmed', {'오늘 출결': False, '확인': False}, False),
            ('todo_p1', {'우선순위': 'P1 지금', '상태': '진행'}, True),
            ('todo_p1', {'우선순위': 'P2 계획', '상태': '진행'}, False),
            ('todo_p1', {'우선순위': 'P1 지금', '상태': '완료'}, False),
            ('todo_p1', {'우선순위': 'P1 지금', '상태': '취소'}, False),
            ('todo_done', {'상태': '완료'}, True),
            ('todo_done', {'상태': '취소'}, False),
            ('progress_attention', {'상태': '보강 필요'}, True),
            ('progress_attention', {'상태': '완료'}, False),
        ]
        for key, values, expected in cases:
            with self.subTest(key=key, values=values):
                self.assertEqual(expected, filter_matches(self.views[key]['filter'], {'보관': False, **values}))
                self.assertFalse(filter_matches(self.views[key]['filter'], {'보관': True, **values}))

    def test_payload_filters_keep_supported_nesting_and_academic_year_scope(self):
        for key in NEW_VIEWS:
            view = self.views[key]
            properties = self.dbs[view['source']]['properties']
            remote = {name: {**prop, 'id': name} for name, prop in properties.items()}
            payload = view_payload(view, {'id': 'db', 'data_source_id': 'ds'}, remote, 2026)
            with self.subTest(key=key):
                self.assertLessEqual(filter_depth(payload['filter']), 2)
                self.assertIn({'property': '학년도', 'number': {'equals': 2026}}, payload['filter']['and'])
                self.assertEqual('ds', payload['data_source_id'])
                self.assertEqual(view['type'], payload['configuration']['type'])
                if 'show' in view:
                    actual = {p['property_id'] for p in payload['configuration']['properties'] if p['visible']}
                    self.assertEqual(set(view['show']), actual)

    def test_native_gallery_board_and_existing_tables_expose_useful_properties(self):
        self.assertEqual('gallery', self.views['students_gallery']['type'])
        self.assertEqual(('board', '상태'), (self.views['projects_board']['type'], self.views['projects_board']['group']))
        self.assertEqual([{'property': '교시', 'direction': 'ascending'}], self.views['teacher_today']['sorts'])
        self.assertTrue({'교실', '상태'} <= set(self.views['teacher_today']['show']))
        for key in ('active_students', 'active_counseling', 'progress_class', 'active_assessments'):
            self.assertIn('이름', self.views[key]['show'])
            self.assertGreater(len(self.views[key]['show']), 3)
        props = schema(self.dbs['students']['properties'])
        self.assertIn('multi_select', props['관찰 태그'])
        self.assertIn('checkbox', props['집중 관찰'])

    def test_optional_views_follow_module_selection(self):
        c = config(ROOT / 'config.example.json')
        c['modules'] = dict.fromkeys(c['modules'], False)
        dbs, views = selected(c)
        selected_keys = {v['key'] for v in views}
        self.assertFalse({'attendance_today', 'attendance_unconfirmed', 'assessments_upcoming'} & selected_keys)
        self.assertTrue((NEW_VIEWS - {'attendance_today', 'attendance_unconfirmed', 'assessments_upcoming'}) <= selected_keys)
        self.assertEqual(9, len(dbs))


if __name__ == '__main__':
    unittest.main()
