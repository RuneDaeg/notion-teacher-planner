"""Custom columns retain the reviewed layout across native compilation/readback.

All native fragments and screenshots here are synthetic test fixtures.
"""
import copy
from pathlib import Path
import tempfile
import unittest

from teacher_planner.layout_contract import load_contract, render_page, validate_layout_rows
from teacher_planner.layout_verify import required_visual_checks, verify_layout_snapshot
from teacher_planner.preview_bundle import compile_bundle, create_bundle, digest, validate_bundle
from test_layout_verify import NOW, PAGE_IDS, STAMP, fragments, png_fixture


def grouped_rows(bundle, ratios):
    """Move existing ordinary sections to one row without losing any section."""
    selected = ['meals', 'tasks', 'deadlines', 'counseling', 'progress'][:len(ratios)]
    rows = copy.deepcopy(bundle['pages']['home']['rows'])
    for row in rows:
        for column in row['columns']:
            column['sections'] = [key for key in column['sections'] if key not in selected]
        row['columns'] = [column for column in row['columns'] if column['sections']]
        if len(row['columns']) == 1:
            row['columns'][0]['ratio'] = 100
    rows = [row for row in rows if row['columns']]
    # Exercise a custom row immediately following an existing internal card grid.
    index = next(index for index, row in enumerate(rows) if row['id'] == 'quick') + 1
    rows.insert(index, {'id': 'custom_columns', 'columns': [
        {'ratio': ratio, 'sections': [key]} for key, ratio in zip(selected, ratios)]})
    return rows


def resign(bundle):
    bundle['bundle_digest'] = digest({key: value for key, value in bundle.items() if key != 'bundle_digest'})
    return bundle


class CustomColumnBundleTests(unittest.TestCase):
    def test_new_default_and_legacy_bundles_have_stable_independent_hashes(self):
        hashes = {
            1: 'df636473880331d93c25c82d85d61a583a6ceea58638ad182c88a14af7f290ce',
            2: 'd802313b15a775c72181b79c43ca5610d9a6643365ebd4cf09bc1b212a95daa5',
            3: 'c83b626d0e68c54974e168e9490020e7f6bcddc20c605a2f62757997b786a97c',
        }
        sources = set()
        for version, expected in hashes.items():
            with self.subTest(version=version):
                bundle = create_bundle(bundle_version=version)
                self.assertEqual(expected, bundle['bundle_digest'])
                self.assertEqual(bundle, validate_bundle(bundle))
                sources.add(bundle['source_digest'])
        self.assertEqual(1, len(sources))

    def test_arbitrary_widths_and_three_or_four_populated_columns_round_trip(self):
        original = create_bundle()
        for ratios in ([10, 90], [26.125, 73.875], [21.25, 33.75, 45],
                       [33.333333, 33.333333, 33.333334], [10, 20, 30, 40]):
            with self.subTest(ratios=ratios):
                rows = grouped_rows(original, ratios)
                bundle = create_bundle(layout_overrides={'home': rows})
                self.assertEqual(bundle, validate_bundle(bundle))
                self.assertEqual(rows, bundle['pages']['home']['rows'])
                self.assertEqual(original['views'], bundle['views'])
                self.assertEqual(original['pages']['home']['sections'], bundle['pages']['home']['sections'])

    def test_precision_boundaries_and_small_total_rounding_error(self):
        original = create_bundle()
        rows = grouped_rows(original, [33.333333, 33.333333, 33.333333])
        self.assertEqual(rows, create_bundle(layout_overrides={'home': rows})['pages']['home']['rows'])
        for ratios in ([33.333332, 33.333333, 33.333333], [33.3333333, 33.3333333, 33.3333334],
                       [9.999999, 90.000001], [90.000001, 9.999999], [10, 20, 30, 39]):
            with self.subTest(ratios=ratios), self.assertRaises(ValueError):
                create_bundle(layout_overrides={'home': grouped_rows(original, ratios)})

    def test_nonfinite_nonnumeric_and_boolean_widths_are_rejected(self):
        original = create_bundle()
        for value in (True, False, '50', None, float('nan'), float('inf'), -float('inf'), 10 ** 500):
            with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                create_bundle(layout_overrides={'home': grouped_rows(original, [value, 50])})

    def test_fifth_or_empty_column_cannot_be_exported(self):
        original = create_bundle()
        with self.assertRaises(ValueError):
            create_bundle(layout_overrides={'home': grouped_rows(original, [20] * 5)})
        rows = grouped_rows(original, [25] * 4)
        next(row for row in rows if row['id'] == 'custom_columns')['columns'][2]['sections'] = []
        with self.assertRaises(ValueError):
            create_bundle(layout_overrides={'home': rows})

    def test_version_two_keeps_presets_and_rejects_rehashed_expanded_layouts(self):
        original = create_bundle()
        for ratios in ([50, 50], [40, 60], [60, 40], [55, 45], [45, 55]):
            bundle = create_bundle(layout_overrides={'home': grouped_rows(original, ratios)}, bundle_version=2)
            self.assertEqual(bundle, validate_bundle(bundle))
        for ratios in ([30, 70], [20, 30, 50], [25, 25, 25, 25]):
            with self.subTest(ratios=ratios):
                rows = grouped_rows(original, ratios)
                with self.assertRaises(ValueError):
                    create_bundle(layout_overrides={'home': rows}, bundle_version=2)
                bundle = create_bundle(layout_overrides={'home': rows})
                bundle['version'] = 2
                with self.assertRaises(ValueError):
                    validate_bundle(resign(bundle))

    def test_legacy_v1_cannot_acquire_columns_by_version_or_metadata_changes(self):
        original = create_bundle()
        bundle = create_bundle(layout_overrides={'home': grouped_rows(original, [25] * 4)})
        bundle['version'] = 1
        del bundle['layout_overrides']
        with self.assertRaises(ValueError):
            validate_bundle(resign(bundle))
        with self.assertRaises(ValueError):
            create_bundle(layout_overrides={}, bundle_version=1)

    def test_unsupported_versions_fail_before_layout_processing(self):
        for version in (True, 0, 4, '3'):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    create_bundle(bundle_version=version)
                with self.assertRaises(ValueError):
                    validate_layout_rows(create_bundle()['pages']['home'], [], layout_version=version)

    def test_native_compilation_keeps_all_custom_ratios_and_original_content(self):
        original = create_bundle()
        for ratios in ([21.25, 33.75, 45], [33.333333, 33.333333, 33.333334], [10, 20, 30, 40]):
            with self.subTest(ratios=ratios):
                bundle = create_bundle(layout_overrides={'home': grouped_rows(original, ratios)})
                content = {key: fragments(key, page['sections'])[0] for key, page in bundle['pages'].items()}
                rendered = compile_bundle(bundle, {'pages': content})
                for ratio in ratios:
                    self.assertIn(f'<column ratio="{ratio}">', rendered['home'])
                self.assertEqual(render_page('home', content['home'], reviewed_rows=bundle['pages']['home']['rows']),
                                 rendered['home'])
                self.assertIn('data-source-url="collection://', rendered['home'])
                self.assertIn('<synced_block_reference', rendered['home'])
                self.assertIn('<details>', rendered['home'])


class CustomColumnEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        png_fixture(self.base / 'synthetic-test-only.png')

    def snapshot(self, bundle):
        result = {'schema_version': 1, 'contract_version': load_contract()['version'],
                  'reviewed_bundle_digest': bundle['bundle_digest'], 'expected_page_ids': PAGE_IDS.copy(),
                  'active_sections': {}, 'pages': {},
                  'core_verification': {'status': 'pass', 'checked_at': STAMP, 'page_ids': PAGE_IDS.copy()}}
        for key, page in bundle['pages'].items():
            selected = list(page['sections'])
            result['active_sections'][key] = selected
            content, bindings = fragments(key, selected)
            result['pages'][key] = {
                'page_id': PAGE_IDS[key], 'fetched_at': STAMP,
                'properties': {'full_width': True, 'small_text': False},
                'markdown': render_page(key, content, reviewed_rows=page['rows']), 'source_bindings': bindings,
                'screenshots': [{'path': 'synthetic-test-only.png', 'page_id': PAGE_IDS[key],
                                 'captured_at': STAMP, 'viewport': {'width': 1440, 'height': 900},
                                 'coverage': 'full_page', 'observations': [
                                     {'check': check, 'result': 'pass', 'note': 'Synthetic test only'}
                                     for check in required_visual_checks(page)]}],
            }
        return result

    def verify(self, snapshot, bundle):
        return verify_layout_snapshot(snapshot, base_dir=self.base, now=NOW, reviewed_bundle=bundle)

    def test_three_and_four_columns_have_independent_structure_and_visual_checks(self):
        for ratios in ([21.25, 33.75, 45], [33.333333, 33.333333, 33.333334], [10, 20, 30, 40]):
            with self.subTest(ratios=ratios):
                bundle = create_bundle(layout_overrides={'home': grouped_rows(create_bundle(), ratios)})
                snapshot = self.snapshot(bundle)
                result = self.verify(snapshot, bundle)
                self.assertEqual([], result['issues'])
                self.assertEqual([], result['pending'])
                self.assertTrue(result['structural_complete'])
                self.assertTrue(result['visual_complete'])
                self.assertIn('columns', required_visual_checks(bundle['pages']['home']))

    def test_changed_actual_width_or_missing_column_cannot_pass_reviewed_plan(self):
        bundle = create_bundle(layout_overrides={'home': grouped_rows(create_bundle(), [10, 20, 30, 40])})
        for old, new in (('ratio="10"', 'ratio="11"'), ('<column ratio="20">', '<column>')):
            with self.subTest(new=new):
                snapshot = self.snapshot(bundle)
                snapshot['pages']['home']['markdown'] = snapshot['pages']['home']['markdown'].replace(old, new, 1)
                result = self.verify(snapshot, bundle)
                self.assertFalse(result['structural_complete'])
                self.assertIn('home.row.custom_columns', [check['id'] for check in result['issues']])

    def test_custom_columns_still_need_actual_visual_observations(self):
        bundle = create_bundle(layout_overrides={'home': grouped_rows(create_bundle(), [20, 30, 50])})
        snapshot = self.snapshot(bundle)
        observations = snapshot['pages']['home']['screenshots'][0]['observations']
        observations[:] = [item for item in observations if item['check'] != 'columns']
        result = self.verify(snapshot, bundle)
        self.assertTrue(result['structural_complete'])
        self.assertFalse(result['visual_complete'])
        self.assertFalse(result['layout_complete'])


if __name__ == '__main__':
    unittest.main()
