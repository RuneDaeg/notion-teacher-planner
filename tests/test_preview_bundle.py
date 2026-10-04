import argparse
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from teacher_planner.preview_bundle import (
    FORMS, MODULES, MAX_INPUT_BYTES, build_catalog, canonical_json, compile_bundle,
    create_bundle, default_selection, digest, read_json, selection_from_config,
    validate_bundle, validate_selection,
)
from teacher_planner.preview_cli import add_commands, run


def fragments(bundle):
    pages = {}
    for page_key, page in bundle['pages'].items():
        sections = {}
        for key, spec in page['sections'].items():
            if 'cards' in spec:
                sections[key] = ['<callout>연결 유지 ' + str(i) + '</callout>'
                                 for i in range(spec['cards']['min'])]
            else:
                text = '## ' + spec['title'] + '\n'
                if spec.get('database_blocks'):
                    text += '<database url="https://www.notion.so/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" inline="true">원본 연결 보기</database>'
                else:
                    text += '기존 본문과 원본 링크를 유지합니다.'
                sections[key] = text
        pages[page_key] = sections
    return {'pages': pages}


class PreviewBundleTests(unittest.TestCase):
    def test_default_only_includes_required_and_core_sections(self):
        bundle = create_bundle()
        self.assertEqual(default_selection(), bundle['selection'])
        self.assertEqual(4, len(bundle['pages']))
        self.assertNotIn('attendance', bundle['pages']['home']['sections'])
        self.assertNotIn('forms', bundle['pages']['home']['sections'])
        self.assertNotIn('school', bundle['pages']['classroom']['sections'])
        self.assertFalse(bundle['applied'])
        self.assertTrue(bundle['page_settings']['full_width'])

    def test_default_hash_matches_browser_canonical_fixture(self):
        bundle = create_bundle()
        expected = digest({k: v for k, v in bundle.items() if k != 'bundle_digest'})
        self.assertEqual(expected, bundle['bundle_digest'])
        self.assertEqual('{"a":100,"b":0.5,"제목":"학급"}', canonical_json({'제목': '학급', 'b': .5, 'a': 100.0}))

    def test_all_choices_resolve_all_selected_sections_and_views(self):
        selection = {**default_selection(), 'modules': list(MODULES), 'forms': list(FORMS), 'homeroom': True, 'school_links': True, 'periods': 9}
        bundle = create_bundle(selection)
        for section in ('attendance', 'assessment', 'forms'):
            self.assertIn(section, bundle['pages']['home']['sections'])
        self.assertIn('contacts', bundle['pages']['classroom']['sections'])
        self.assertIn('school', bundle['pages']['classroom']['sections'])
        self.assertIn('meetings', bundle['pages']['teaching']['sections'])
        self.assertIn('assessments_upcoming', bundle['views'])
        self.assertEqual(bundle, validate_bundle(bundle))

    def test_choice_removal_normalizes_remaining_column(self):
        bundle = create_bundle()
        row = next(row for row in bundle['pages']['home']['rows'] if any('progress' in col['sections'] for col in row['columns']))
        self.assertEqual(1, len(row['columns']))
        self.assertEqual(100, row['columns'][0]['ratio'])

    def test_form_dependency_rejected_without_enabling_extra_modules(self):
        for form in ('guardian', 'meeting', 'assessment', 'homeroom'):
            with self.subTest(form=form), self.assertRaises(ValueError):
                create_bundle({**default_selection(), 'forms': [form]})

    def test_selection_validation_and_normalization(self):
        valid = {**default_selection(), 'modules': ['staff', 'attendance']}
        self.assertEqual(['attendance', 'staff'], validate_selection(valid)['modules'])
        for patch in ({'periods': True}, {'periods': 0}, {'periods': 21}, {'homeroom': 1},
                      {'school_links': 'yes'}, {'modules': ['staff', 'staff']},
                      {'forms': ['unknown']}, {'modules': ['core']}, {'extra': True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_selection({**default_selection(), **patch})

    def test_missing_selection_field_rejected(self):
        selection = default_selection()
        del selection['forms']
        with self.assertRaises(ValueError):
            validate_selection(selection)

    def test_source_digest_binds_all_public_source_inputs(self):
        catalog = build_catalog()
        self.assertEqual(64, len(catalog['source_digest']))
        self.assertEqual(catalog['source_digest'], create_bundle()['source_digest'])
        self.assertEqual({'format', 'version', 'source_digest', 'contract', 'databases', 'views', 'workspace'}, set(catalog))

    def test_reference_views_are_exact_not_every_original_database_view(self):
        bundle = create_bundle()
        referenced = {view for page in bundle['pages'].values() for section in page['sections'].values()
                      for view in section.get('view_keys', [])}
        self.assertEqual(referenced, set(bundle['views']))
        self.assertNotIn('active_assessments', bundle['views'])
        self.assertEqual(bundle['views']['weekly']['source'], bundle['views']['monthly']['source'])
        self.assertEqual('agenda', bundle['views']['weekly']['source'])

    def test_tampered_width_rejected_even_with_recomputed_hash(self):
        bundle = create_bundle()
        bundle['pages']['home']['rows'][0]['columns'][0]['ratio'] = 90
        bundle['bundle_digest'] = digest({key: value for key, value in bundle.items() if key != 'bundle_digest'})
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_tampered_filter_or_extra_fields_rejected(self):
        for mutate in (
                lambda b: b['views']['todo'].update(filter={}),
                lambda b: b.update(applied=True),
                lambda b: b.update(secret='never print this'),
                lambda b: b.update(version=True),
                lambda b: b.update(bundle_digest='0' * 64)):
            bundle = create_bundle()
            mutate(bundle)
            with self.assertRaises(ValueError):
                validate_bundle(bundle)

    def test_stale_source_rejected(self):
        bundle = create_bundle()
        bundle['source_digest'] = '0' * 64
        with self.assertRaisesRegex(ValueError, '버전'):
            validate_bundle(bundle)

    def test_actual_config_selector_does_not_export_private_settings(self):
        config = {'modules': {'attendance': True}, 'forms': ['counseling'],
                  'classes': [{'homeroom': True, 'name': 'private-class'}],
                  'teacher': 'private-teacher', 'dashboard': {'periods': 8, 'links': {'neis': 'https://example.com/private'}}}
        selection = selection_from_config(config)
        self.assertEqual({**default_selection(), 'modules': ['attendance'], 'forms': ['counseling'],
                          'homeroom': True, 'school_links': True, 'periods': 8}, selection)
        self.assertNotIn('private', canonical_json(create_bundle(selection)))

    def test_compile_preserves_native_urls_and_uses_contract_columns_toggles(self):
        bundle = create_bundle()
        sections = fragments(bundle)
        sections['pages']['home']['matrix'] = '<synced_block synced_from="https://www.notion.so/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"/>'
        rendered = compile_bundle(bundle, sections)
        self.assertEqual(set(bundle['pages']), set(rendered))
        self.assertIn('<column ratio="40">', rendered['home'])
        self.assertIn('<details>', rendered['home'])
        self.assertIn('https://www.notion.so/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', rendered['home'])
        self.assertIn('synced_from="https://www.notion.so/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"', rendered['home'])

    def test_compile_rejects_missing_extra_and_empty_active_sections(self):
        bundle = create_bundle()
        for mode in ('missing-page', 'extra-page', 'missing-section', 'extra-section', 'empty-section'):
            sections = fragments(bundle)
            if mode == 'missing-page': del sections['pages']['home']
            if mode == 'extra-page': sections['pages']['surprise'] = {}
            if mode == 'missing-section': del sections['pages']['home']['meals']
            if mode == 'extra-section': sections['pages']['home']['custom-data'] = '기존 사용자 구역'
            if mode == 'empty-section': sections['pages']['home']['meals'] = ' '
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                compile_bundle(bundle, sections)

    def test_compile_rejects_two_databases_for_one_tab_group(self):
        bundle = create_bundle()
        sections = fragments(bundle)
        sections['pages']['home']['tasks'] *= 2
        with self.assertRaisesRegex(ValueError, 'database_block_count'):
            compile_bundle(bundle, sections)

    def test_json_input_rejects_duplicate_and_oversized_data(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'input.json'
            for value in ('{"pages":{},"pages":{}}', '{"value":NaN}', ' ' * (MAX_INPUT_BYTES + 1)):
                target.write_text(value)
                with self.assertRaises(ValueError):
                    read_json(target)

    def test_compile_cli_writes_private_files_without_exposing_fragments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = create_bundle()
            sections = fragments(bundle)
            sections['pages']['home']['briefing'] = 'private-example-content'
            (root / 'bundle.json').write_text(json.dumps(bundle))
            (root / 'sections.json').write_text(json.dumps(sections))
            args = argparse.Namespace(command='compile-preview', bundle=root / 'bundle.json',
                                      sections=root / 'sections.json', output_dir=root / 'output')
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(0, run(args))
            self.assertNotIn('private-example-content', stream.getvalue())
            report = json.loads(stream.getvalue())
            self.assertFalse(report['applied'])
            self.assertFalse(report['live_notion_verified'])
            self.assertFalse(report['layout_complete'])
            files = list((root / 'output').iterdir())
            self.assertEqual(6, len(files))
            for path in files:
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertIn('private-example-content', (root / 'output/home.md').read_text())
            self.assertIn('전체 페이지 덮어쓰기', (root / 'output/apply-instructions.txt').read_text())

    def test_verify_cli_reports_offline_success_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bundle.json'
            path.write_text(json.dumps(create_bundle()))
            parser = argparse.ArgumentParser()
            add_commands(parser.add_subparsers(dest='command'))
            args = parser.parse_args(['verify-preview', '--bundle', str(path)])
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(0, run(args))
            report = json.loads(stream.getvalue())
            self.assertTrue(report['bundle_valid'])
            self.assertFalse(report['applied'])

    def test_invalid_compile_creates_no_partial_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = create_bundle()
            sections = fragments(bundle)
            sections['pages']['planning']['archive'] = 'missing database'
            (root / 'bundle.json').write_text(json.dumps(bundle))
            (root / 'sections.json').write_text(json.dumps(sections))
            args = argparse.Namespace(command='compile-preview', bundle=root / 'bundle.json',
                                      sections=root / 'sections.json', output_dir=root / 'output')
            with self.assertRaises(ValueError):
                run(args)
            self.assertFalse((root / 'output').exists())

    def test_generated_catalog_is_current(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'scripts/build_preview_catalog.py'), '--check'],
                                cwd=root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == '__main__':
    unittest.main()
