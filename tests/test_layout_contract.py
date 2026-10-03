import copy
import unittest

from teacher_planner.layout_contract import (
    LayoutContractError, active_sections_for_config, load_contract,
    render_page, resolve_page_layout,
)
from teacher_planner.model import blueprint


def fragments(page_key, optional=True):
    result = {}
    for key, spec in load_contract()['pages'][page_key]['sections'].items():
        if not optional and not spec['required']:
            continue
        if 'cards' in spec:
            result[key] = ['<callout>\n\tCard ' + str(i) + '\n</callout>'
                           for i in range(spec['cards']['max'])]
        elif spec.get('database_blocks'):
            result[key] = ('## ' + spec['title'] + '\n'
                           '<database url="https://example.org/' + page_key + '/' + key
                           + '" inline="true">Existing DB</database>')
        else:
            result[key] = '## ' + spec['title'] + '\nExisting content ' + key
    return result


class LayoutContractTests(unittest.TestCase):
    def test_contract_covers_every_section_once_in_all_four_pages(self):
        contract = load_contract()
        self.assertEqual('teacher-desk-layout-v1', contract['id'])
        self.assertEqual(1, contract['version'])
        self.assertEqual({'home', 'classroom', 'teaching', 'planning'}, set(contract['pages']))
        for page in contract['pages'].values():
            self.assertIs(page['full_width'], True)
            self.assertIs(page['small_text'], False)
            sections = [key for row in page['rows'] for col in row['columns'] for key in col['sections']]
            self.assertCountEqual(page['sections'], sections)
            for row in page['rows']:
                self.assertAlmostEqual(100, sum(col['ratio'] for col in row['columns']))
        contract['pages']['home']['full_width'] = False
        self.assertTrue(load_contract()['pages']['home']['full_width'])

    def test_database_sections_refer_to_real_blueprint_views(self):
        view_keys = {view['key'] for view in blueprint()['views']}
        for page in load_contract()['pages'].values():
            for section in page['sections'].values():
                if 'view_keys' in section:
                    self.assertFalse(set(section['view_keys']) - view_keys)
                    self.assertIn(section['default_view'], section['view_keys'])
                    self.assertEqual(1, section['database_blocks'])
        self.assertEqual('monthly', load_contract()['pages']['planning']['sections']['schedule']['default_view'])

    def test_required_missing_and_unknown_fail_before_discarding_content(self):
        supplied = fragments('home')
        supplied['unrecognized_notes'] = 'Keep this user note'
        with self.assertRaisesRegex(LayoutContractError, 'unknown_sections: unrecognized_notes'):
            render_page('home', supplied)
        del supplied['unrecognized_notes']
        supplied['matrix'] = '  '
        with self.assertRaisesRegex(LayoutContractError, 'required_missing: matrix'):
            render_page('home', supplied)
        with self.assertRaisesRegex(LayoutContractError, 'unknown_page'):
            resolve_page_layout('unknown')

    def test_optional_modules_collapse_rows_without_enabling_them(self):
        supplied = fragments('home', optional=False)
        page = resolve_page_layout('home', supplied)
        rows = {row['id']: row for row in page['rows']}
        self.assertNotIn('sync_status', rows)
        self.assertEqual([{'ratio': 100, 'sections': ['counseling']}], rows['students']['columns'])
        self.assertEqual([{'ratio': 100, 'sections': ['progress']}], rows['teaching']['columns'])
        self.assertEqual([40, 60], [col['ratio'] for col in rows['work']['columns']])
        self.assertEqual(['tasks', 'deadlines'], rows['work']['columns'][1]['sections'])
        self.assertNotIn('attendance', page['sections'])
        classroom = resolve_page_layout('classroom', fragments('classroom', optional=False))
        self.assertNotIn('attendance_submissions', [row['id'] for row in classroom['rows']])

    def test_config_selection_uses_modules_forms_and_supplied_school_links(self):
        config = {'modules': {'attendance': False, 'assessment': False, 'contact': False,
                              'meeting': False}, 'classes': [], 'forms': []}
        before = copy.deepcopy(config)
        home = active_sections_for_config(config, 'home')
        self.assertNotIn('attendance', home)
        self.assertNotIn('assessment', home)
        self.assertNotIn('forms', home)
        self.assertNotIn('sync_status', home)
        self.assertIn('meals', home)
        self.assertNotIn('school', active_sections_for_config(config, 'classroom'))
        self.assertEqual(before, config)
        config['dashboard'] = {'links': {'neis': 'https://example.org/portal'}}
        self.assertIn('school', active_sections_for_config(config, 'classroom'))
        config['forms'] = ['counseling']
        self.assertIn('forms', active_sections_for_config(config, 'home'))
        config['modules']['attendance'] = True
        self.assertIn('attendance', active_sections_for_config(config, 'home'))

    def test_native_ids_and_synced_reference_survive_without_database_duplication(self):
        supplied = fragments('home')
        supplied['matrix'] = ('## 오늘의 수업 · 주간 시간표\n'
                              '<synced_block_reference url="https://example.org/synced-source">\n'
                              '\t<table fit-page-width="true"><tr><td>1</td></tr></table>\n'
                              '</synced_block_reference>')
        supplied['document_storage'] = ('## 문서 보관실\n'
                                         '<page url="https://example.org/existing-child">Existing child</page>')
        before = copy.deepcopy(supplied)
        rendered = render_page('home', supplied)
        self.assertEqual(before, supplied)
        self.assertEqual(rendered, render_page('home', dict(reversed(list(supplied.items())))))
        self.assertIn(supplied['matrix'], rendered)
        self.assertIn(supplied['document_storage'], rendered)
        self.assertEqual(1, rendered.count('url="https://example.org/home/tasks"'))
        self.assertIn('<column ratio="40">', rendered)
        self.assertIn('<column ratio="60">', rendered)
        self.assertLess(rendered.index('home/tasks'), rendered.index('home/deadlines'))

    def test_view_tabs_cannot_be_rendered_as_separate_databases(self):
        supplied = fragments('planning')
        supplied['schedule'] += '\n<database url="https://example.org/second">Second DB</database>'
        with self.assertRaisesRegex(LayoutContractError, 'database_block_count'):
            render_page('planning', supplied)

    def test_management_sections_use_indented_details_not_fake_open_attribute(self):
        rendered = render_page('teaching', fragments('teaching'))
        self.assertIn('<details>\n<summary>시간표 원본 · 관리용</summary>\n\t##', rendered)
        self.assertNotIn('open=', rendered)
        spec = load_contract()['pages']['teaching']['sections']['timetable']
        self.assertIs(spec['default_open'], False)
        self.assertEqual('browser_ui_required', spec['collapse_verification'])

    def test_existing_complete_toggle_and_navigation_are_not_double_wrapped(self):
        supplied = fragments('home')
        supplied['nav'] = '<callout icon="🧭" color="gray_bg">\n\tExisting navigation\n</callout>'
        supplied['source_list'] = '<details>\n<summary>原本</summary>\n\tExisting source list\n</details>'
        rendered = render_page('home', supplied)
        self.assertIn(supplied['nav'], rendered)
        self.assertIn(supplied['source_list'], rendered)
        self.assertEqual(1, rendered.count('icon="🧭"'))

    def test_card_count_and_ratios_are_fixed_but_single_optional_card_is_full_width(self):
        supplied = fragments('home')
        supplied['quick'] = ['One', 'Two', 'Three']
        with self.assertRaisesRegex(LayoutContractError, 'card_count'):
            render_page('home', supplied)
        supplied['quick'] = 'A list of links is not a card layout'
        with self.assertRaisesRegex(LayoutContractError, 'card_layout_missing'):
            render_page('home', supplied)
        supplied = fragments('classroom', optional=False)
        supplied['school'] = ['Only selected school link']
        rendered = render_page('classroom', supplied)
        self.assertIn('## 학교 업무 바로가기\n<callout color="gray_bg">', rendered)
        self.assertNotIn('<column ratio="100">', rendered)

    def test_existing_correct_card_section_is_preserved(self):
        supplied = fragments('planning')
        cards = '<columns>\n' + '\n'.join(
            '\t<column ratio="25">\n\t\t<callout>Kept ' + str(i) + '</callout>\n\t</column>'
            for i in range(4)) + '\n</columns>'
        supplied['para'] = '## PARA 교직 업무\n' + cards
        self.assertIn(supplied['para'], render_page('planning', supplied))
        supplied['para'] = supplied['para'].replace('ratio="25"', 'ratio="20"', 1)
        with self.assertRaisesRegex(LayoutContractError, 'card_ratios'):
            render_page('planning', supplied)

    def test_table_width_is_relative_and_source_is_never_agenda(self):
        table = load_contract()['timetable']
        self.assertEqual([70, 150, 184, 184, 184, 184, 184], table['preferred_column_widths'])
        self.assertEqual('relative_preference_fit_available_page_width', table['width_policy'])
        self.assertTrue(table['fit_page_width'])
        self.assertTrue(table['same_synced_source'])
        self.assertFalse(table['copy_to_agenda'])


if __name__ == '__main__':
    unittest.main()
