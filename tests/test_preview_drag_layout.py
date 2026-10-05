"""Reviewed drag layouts are bounded plans, never synthetic Notion success."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teacher_planner.cli import main
from teacher_planner.layout_cli import load_evidence
from teacher_planner.layout_contract import load_contract, render_page
from teacher_planner.layout_verify import required_visual_checks, verify_layout_snapshot
from teacher_planner.model import config
from teacher_planner.preview_bundle import (compile_bundle, create_bundle, default_selection,
                                           digest, selection_from_config, validate_bundle)
from test_layout_verify import NOW, PAGE_IDS, STAMP, fragments, png_fixture
from test_planner import FakeNotion


def legacy(bundle):
    result = copy.deepcopy(bundle)
    result['version'] = 1
    del result['layout_overrides']
    del result['bundle_digest']
    result['bundle_digest'] = digest(result)
    return result


def reversed_body_rows(bundle, page='home'):
    rows = copy.deepcopy(bundle['pages'][page]['rows'])
    return rows[:2] + list(reversed(rows[2:]))


class DragLayoutBundleTests(unittest.TestCase):
    def test_version_two_contains_default_empty_overrides(self):
        bundle = create_bundle()
        self.assertEqual(2, bundle['version'])
        self.assertEqual({}, bundle['layout_overrides'])
        self.assertEqual(bundle, validate_bundle(bundle))

    def test_original_v1_bundle_keeps_original_digest_and_validation(self):
        bundle = legacy(create_bundle())
        self.assertEqual('df636473880331d93c25c82d85d61a583a6ceea58638ad182c88a14af7f290ce', bundle['bundle_digest'])
        self.assertEqual(bundle, validate_bundle(bundle))
        bundle['layout_overrides'] = {}
        bundle['bundle_digest'] = digest({key: value for key, value in bundle.items() if key != 'bundle_digest'})
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_reorder_sections_preserves_all_native_section_and_view_metadata(self):
        original = create_bundle()
        rows = reversed_body_rows(original)
        changed = create_bundle(layout_overrides={'home': rows})
        self.assertEqual(rows, changed['pages']['home']['rows'])
        self.assertEqual(original['pages']['home']['sections'], changed['pages']['home']['sections'])
        self.assertEqual(original['views'], changed['views'])
        self.assertEqual(original['source_digest'], changed['source_digest'])
        self.assertNotEqual(original['bundle_digest'], changed['bundle_digest'])
        self.assertEqual(changed, validate_bundle(changed))
        rows[2]['id'] = 'mutated-input'
        self.assertNotEqual(rows, changed['layout_overrides']['home'])

    def test_width_changes_and_column_stacking_are_represented(self):
        original = create_bundle()
        rows = copy.deepcopy(original['pages']['home']['rows'])
        work = next(row for row in rows if row['id'] == 'work')
        for ratios in ((50, 50), (40, 60), (60, 40), (55, 45), (45, 55)):
            for column, ratio in zip(work['columns'], ratios):
                column['ratio'] = ratio
            bundle = create_bundle(layout_overrides={'home': rows})
            self.assertEqual(bundle, validate_bundle(bundle))
        keys = [key for col in work['columns'] for key in col['sections']]
        work['columns'] = [{'ratio': 100, 'sections': keys}]
        self.assertEqual(keys, create_bundle(layout_overrides={'home': rows})['pages']['home']['rows'][rows.index(work)]['columns'][0]['sections'])

    def test_rehashed_page_metadata_cannot_override_reviewed_rows(self):
        for mutate in (
                lambda b: b['pages']['home']['rows'].reverse(),
                lambda b: b['pages']['home'].update(full_width=False),
                lambda b: b['pages']['home']['sections']['tasks'].update(database_blocks=2),
                lambda b: b['views']['weekly'].update(source='timetable')):
            bundle = create_bundle(layout_overrides={'home': reversed_body_rows(create_bundle())})
            mutate(bundle)
            bundle['bundle_digest'] = digest({key: value for key, value in bundle.items() if key != 'bundle_digest'})
            with self.assertRaises(ValueError):
                validate_bundle(bundle)

    def test_invalid_rows_cannot_hide_move_or_duplicate_selected_sections(self):
        original = create_bundle()
        base = original['pages']['home']['rows']
        mutations = {
            'empty': lambda rows: rows.clear(),
            'duplicate': lambda rows: rows.append(copy.deepcopy(rows[-1])),
            'missing': lambda rows: rows.pop(),
            'unknown': lambda rows: rows[-1]['columns'][0]['sections'].append('contacts'),
            'missing-column': lambda rows: rows[-1].update(columns=[]),
            'missing-sections': lambda rows: rows[-1]['columns'][0].update(sections=[]),
            'extra-field': lambda rows: rows[-1].update(secret='unused'),
            'extra-column-field': lambda rows: rows[-1]['columns'][0].update(title='unused'),
            'duplicate-id': lambda rows: rows[-1].update(id=rows[0]['id']),
            'invalid-id': lambda rows: rows[-1].update(id='<script>'),
            'long-id': lambda rows: rows[-1].update(id='a' * 65),
            'numeric-id': lambda rows: rows[-1].update(id='1-row'),
            'wrong-width': lambda rows: rows[-1]['columns'][0].update(ratio=90),
            'boolean-width': lambda rows: rows[-1]['columns'][0].update(ratio=True),
            'nan-width': lambda rows: rows[-1]['columns'][0].update(ratio=float('nan')),
            'anchor-moved': lambda rows: rows.reverse(),
            'anchor-renamed': lambda rows: rows[0].update(id='different-nav'),
            'anchor-extra-content': lambda rows: rows[0]['columns'][0]['sections'].append(rows.pop()['columns'][0]['sections'][0]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                rows = copy.deepcopy(base)
                mutate(rows)
                with self.assertRaises(ValueError):
                    create_bundle(layout_overrides={'home': rows})
        for overrides in ([], {'elsewhere': base}, {'home': {}}, {'home': None}):
            with self.subTest(overrides=type(overrides).__name__), self.assertRaises(ValueError):
                create_bundle(layout_overrides=overrides)

    def test_card_rows_cannot_nest_or_stack_in_ordinary_columns(self):
        base = create_bundle()
        for nested in (True, False):
            rows = copy.deepcopy(base['pages']['home']['rows'])
            quick = next(row for row in rows if row['columns'][0]['sections'] == ['quick'])
            briefing = next(row for row in rows if row['columns'][0]['sections'] == ['briefing'])
            rows.remove(briefing)
            if nested:
                quick['columns'] = [{'ratio': 50, 'sections': ['quick']}, {'ratio': 50, 'sections': ['briefing']}]
            else:
                quick['columns'][0]['sections'].append('briefing')
            with self.assertRaisesRegex(ValueError, '카드'):
                create_bundle(layout_overrides={'home': rows})

    def test_compile_uses_reviewed_rows_and_preserves_original_database_ids(self):
        original = create_bundle()
        bundle = create_bundle(layout_overrides={'home': reversed_body_rows(original)})
        pages = {key: fragments(key, page['sections'])[0] for key, page in bundle['pages'].items()}
        result = compile_bundle(bundle, {'pages': pages})
        expected = render_page('home', pages['home'], reviewed_rows=bundle['pages']['home']['rows'])
        self.assertEqual(expected, result['home'])
        self.assertNotEqual(render_page('home', pages['home']), result['home'])
        self.assertIn('data-source-url="collection://', result['home'])
        self.assertIn('<details>', result['home'])
        self.assertEqual(compile_bundle(legacy(original), {'pages': pages})['teaching'], result['teaching'])


class DragLayoutEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        png_fixture(self.base / 'synthetic-test-only.png')
        self.bundle = create_bundle(layout_overrides={'home': reversed_body_rows(create_bundle())})
        self.snapshot = {
            'schema_version': 1, 'contract_version': load_contract()['version'],
            'reviewed_bundle_digest': self.bundle['bundle_digest'], 'expected_page_ids': PAGE_IDS.copy(),
            'active_sections': {}, 'pages': {},
            'core_verification': {'status': 'pass', 'checked_at': STAMP, 'page_ids': PAGE_IDS.copy()},
        }
        for key, page in self.bundle['pages'].items():
            selected = list(page['sections'])
            self.snapshot['active_sections'][key] = selected
            content, bindings = fragments(key, selected)
            self.snapshot['pages'][key] = {
                'page_id': PAGE_IDS[key], 'fetched_at': STAMP,
                'properties': {'full_width': True, 'small_text': False},
                'markdown': render_page(key, content, reviewed_rows=page['rows']), 'source_bindings': bindings,
                'screenshots': [{'path': 'synthetic-test-only.png', 'page_id': PAGE_IDS[key], 'captured_at': STAMP,
                                 'viewport': {'width': 1440, 'height': 900}, 'coverage': 'full_page',
                                 'observations': [{'check': check, 'result': 'pass', 'note': 'Synthetic test only'}
                                                  for check in required_visual_checks(page)]}],
            }

    def verify(self, bundle=True):
        return verify_layout_snapshot(self.snapshot, base_dir=self.base, now=NOW,
                                      reviewed_bundle=self.bundle if bundle else None)

    def test_explicit_reviewed_layout_is_checked_instead_of_canonical_order(self):
        result = self.verify()
        self.assertEqual([], result['issues'])
        self.assertEqual([], result['pending'])
        self.assertTrue(result['complete'])
        without = self.verify(bundle=False)
        self.assertFalse(without['layout_complete'])
        self.assertIn('snapshot.reviewed_bundle', [check['id'] for check in without['issues']])
        self.assertIn('home.section_order', [check['id'] for check in without['issues']])

    def test_reviewed_plan_does_not_bypass_actual_order_or_width(self):
        self.snapshot['pages']['home']['markdown'] = self.snapshot['pages']['home']['markdown'].replace('ratio="40"', 'ratio="50"')
        self.assertFalse(self.verify()['structural_complete'])

    def test_card_section_can_be_followed_immediately_by_a_two_column_row(self):
        rows = copy.deepcopy(create_bundle()['pages']['home']['rows'])
        work = next(row for row in rows if row['id'] == 'work')
        rows.remove(work)
        quick_index = next(index for index, row in enumerate(rows) if row['id'] == 'quick')
        rows.insert(quick_index + 1, work)
        self.bundle = create_bundle(layout_overrides={'home': rows})
        self.snapshot['reviewed_bundle_digest'] = self.bundle['bundle_digest']
        content, _ = fragments('home', self.bundle['pages']['home']['sections'])
        self.snapshot['pages']['home']['markdown'] = render_page('home', content, reviewed_rows=rows)
        result = self.verify()
        self.assertEqual([], result['issues'])
        self.assertTrue(result['layout_complete'])

    def test_missing_or_different_reviewed_digest_fails(self):
        for digest_value in (None, '0' * 64):
            self.snapshot['reviewed_bundle_digest'] = digest_value
            self.assertFalse(self.verify()['structural_complete'])

    def test_missing_or_extra_active_sections_do_not_pass_reviewed_plan(self):
        for keys in (None, ['nav'], list(self.bundle['pages']['home']['sections']) + ['unselected']):
            self.snapshot['active_sections']['home'] = keys
            self.assertFalse(self.verify()['layout_complete'])

    def test_reviewed_plan_does_not_bypass_screenshots_settings_or_sources(self):
        original = copy.deepcopy(self.snapshot)
        for mutate in (
                lambda s: s['pages']['home'].update(screenshots=[]),
                lambda s: s['pages']['home']['properties'].update(full_width=False),
                lambda s: s['pages']['home']['source_bindings']['tasks'].update(data_source_id='f' * 32),
                lambda s: s['pages']['teaching']['screenshots'][0].update(captured_at='2000-01-01T00:00:00Z')):
            self.snapshot = copy.deepcopy(original)
            mutate(self.snapshot)
            self.assertFalse(self.verify()['layout_complete'])

    def test_cli_loads_reviewed_bundle_and_remains_offline(self):
        path, bundle_path = self.base / 'snapshot.json', self.base / 'bundle.json'
        path.write_text(json.dumps(self.snapshot))
        bundle_path.write_text(json.dumps(self.bundle))
        out = io.StringIO()
        with (patch('teacher_planner.layout_verify.datetime') as clock,
              patch('teacher_planner.cli.Client', side_effect=AssertionError('No API calls')),
              redirect_stdout(out)):
            clock.now.return_value = NOW
            clock.fromisoformat.side_effect = __import__('datetime').datetime.fromisoformat
            result = main(['verify-layout', '--snapshot', str(path), '--reviewed-bundle', str(bundle_path)])
        self.assertEqual(0, result)
        self.assertTrue(json.loads(out.getvalue())['layout_complete'])


class ReviewedInstallationStateTests(unittest.TestCase):
    def test_successful_install_retains_reviewed_plan_without_claiming_layout_applied(self):
        root = Path(__file__).resolve().parents[1]
        c = config(root / 'config.example.json')
        base = create_bundle(selection_from_config(c))
        bundle = create_bundle(base['selection'], {'home': reversed_body_rows(base)})
        api = FakeNotion()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            bundle_path, state_path = folder / 'bundle.json', folder / 'state.json'
            bundle_path.write_text(json.dumps(bundle))
            with patch('teacher_planner.cli.Client', return_value=api), redirect_stdout(io.StringIO()):
                result = main(['install', '--config', str(root / 'config.example.json'), '--parent', api.parent,
                               '--reviewed-bundle', str(bundle_path), '--state', str(state_path), '--apply'])
            self.assertEqual(0, result)
            state = json.loads(state_path.read_text())
            self.assertEqual(bundle, state['reviewed_layout'])
            self.assertFalse(state['layout_acceptance']['layout_complete'])

    def test_state_reviewed_plan_is_default_target_and_explicit_target_can_supersede(self):
        state = {'config': {'modules': {}, 'forms': [], 'classes': [], 'dashboard': {}},
                 'reviewed_layout': create_bundle()}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.json'
            path.write_text('{}')
            # Stop after selection binding; neither branch can mutate the state.
            before = copy.deepcopy(state)
            with patch('teacher_planner.preview_bundle.validate_bundle', wraps=validate_bundle) as validate:
                with self.assertRaises(ValueError):
                    load_evidence(path, state=state)
                validate.assert_called_once_with(state['reviewed_layout'])
            explicit = legacy(create_bundle())
            with patch('teacher_planner.preview_bundle.validate_bundle', wraps=validate_bundle) as validate:
                with self.assertRaises(ValueError):
                    load_evidence(path, state=state, reviewed_bundle=explicit)
                validate.assert_called_once_with(explicit)
            self.assertEqual(before, state)

    def test_reviewed_final_verification_requires_snapshot_before_client(self):
        for flags in ([], ['--core-only']):
            with (patch('teacher_planner.cli.Client', side_effect=AssertionError('No API calls')),
                  redirect_stderr(io.StringIO()) as err):
                result = main(['verify', '--reviewed-bundle', 'not-opened.json'] + flags)
            self.assertEqual(1, result)
            self.assertIn('--layout-snapshot', err.getvalue())


if __name__ == '__main__':
    unittest.main()
