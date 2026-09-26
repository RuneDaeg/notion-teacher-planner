import copy
import unittest
from datetime import date

from teacher_planner.dashboard import (
    dashboard_config, layout_spec, matrix_table, matrix_title, middle_columns,
    middle_content, quick_links, section_heading, top_columns,
)


def content(value):
    if isinstance(value, dict):
        if value.get('type') == 'text':
            return value['text']['content']
        return ''.join(content(v) for v in value.values())
    if isinstance(value, list):
        return ''.join(content(v) for v in value)
    return ''


def linked_urls(value):
    if isinstance(value, dict):
        if value.get('type') == 'text' and value['text'].get('link'):
            return [value['text']['link']['url']]
        return [url for v in value.values() for url in linked_urls(v)]
    if isinstance(value, list):
        return [url for v in value for url in linked_urls(v)]
    return []


def lesson(day='2026-09-28', period=1, **extra):
    return {'date': day, 'period': period, 'subject': '영어', 'class_name': '1학년 1반',
            'room': '', 'status': '예정', **extra}


def cells(table, row):
    return table['table']['children'][row]['table_row']['cells']


class DashboardConfigTests(unittest.TestCase):
    def test_defaults_are_complete_and_do_not_share_mutable_values(self):
        first = dashboard_config({})
        self.assertEqual(7, first['periods'])
        self.assertEqual(5, len(first['bookmarks']))
        self.assertTrue(all(bookmark['url'] == '' for bookmark in first['bookmarks']))
        first['bookmarks'][0]['label'] = '편집'
        self.assertNotEqual(first, dashboard_config({}))

    def test_overlay_preserves_defaults_and_does_not_mutate_input(self):
        supplied = {'dashboard': {'periods': 8, 'links': {'survey': 'https://example.org/survey'}}}
        before = copy.deepcopy(supplied)
        result = dashboard_config(supplied)
        self.assertEqual(8, result['periods'])
        self.assertEqual('', result['links']['shared_page'])
        self.assertEqual(5, len(result['bookmarks']))
        self.assertEqual(before, supplied)

    def test_invalid_period_count_and_unknown_fields_fail(self):
        for settings in ({'periods': True}, {'periods': 0}, {'periods': 21},
                         {'periods': '7'}, {'unrecognized': 1}, {'links': {'password': 'x'}}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                dashboard_config({'dashboard': settings})

    def test_dangerous_or_credential_bearing_bookmark_urls_fail(self):
        urls = ['javascript:alert(1)', 'file:///tmp/report', 'data:text/plain,hello',
                'https://user:password@example.org/', 'https://user@example.org/',
                'https://example.org/%0Aheader', 'https://example.org:wrong/',
                'https://example.org/a b', 'https://']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                dashboard_config({'dashboard': {'bookmarks': [{'label': '링크', 'url': url}]}})

    def test_url_encoded_paths_and_empty_bookmark_list_are_supported(self):
        settings = {'dashboard': {'bookmarks': [{'label': '파일', 'url': 'https://example.org/a%20b'}]}}
        self.assertEqual('https://example.org/a%20b', dashboard_config(settings)['bookmarks'][0]['url'])
        self.assertEqual([], dashboard_config({'dashboard': {'bookmarks': []}})['bookmarks'])

    def test_shared_links_apply_same_url_validation(self):
        for key in ('shared_page', 'survey'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                dashboard_config({'dashboard': {'links': {key: 'https://user:secret@example.org'}}})


class DashboardBlockTests(unittest.TestCase):
    def test_manifest_identity_and_section_styles_are_shared(self):
        spec = layout_spec()
        self.assertEqual(('teacher-notebook', '교무수첩 기본 템플릿', 2),
                         (spec['id'], spec['name'], spec['version']))
        for key in ('things', 'meetings', 'students', 'schedule', 'archive'):
            self.assertTrue(spec['sections'][key]['title'])
            self.assertTrue(spec['sections'][key]['color'].endswith('_background'))

    def test_top_columns_are_shallow_and_match_reference_proportions(self):
        block = top_columns({}, {})
        columns = block['column_list']['children']
        self.assertEqual([0.625, 0.375], [x['column']['width_ratio'] for x in columns])
        self.assertTrue(all(len(x['column']['children']) == 1 for x in columns))
        left, right = [x['column']['children'][0] for x in columns]
        self.assertEqual('수업 시간표', content(left))
        self.assertEqual('green_background', left['heading_2']['color'])
        self.assertEqual('빠른 동작 / 즐겨찾기', content(right))
        self.assertEqual('purple_background', right['callout']['color'])
        self.assertNotIn('children', right['callout'])

    def test_middle_columns_have_three_shallow_scaffolds(self):
        columns = middle_columns({})['column_list']['children']
        self.assertEqual([0.21, 0.58, 0.21], [x['column']['width_ratio'] for x in columns])
        self.assertEqual(['Class', '학생 명렬표', 'School'], [content(x) for x in columns])
        self.assertAlmostEqual(1, sum(x['column']['width_ratio'] for x in columns))

    def test_quick_actions_use_actual_database_links(self):
        links = {'timetable': 'https://www.notion.so/timetable',
                 'lessons': {'id': '12345678-1234-1234-1234-123456789abc'}}
        blocks = quick_links({}, links)
        self.assertEqual(['https://www.notion.so/timetable',
                          'https://www.notion.so/12345678123412341234123456789abc'], linked_urls(blocks))
        self.assertIn('시간표', content(blocks))
        self.assertEqual(5, content(blocks).count('링크 추가'))
        self.assertNotIn('상담 기록', content(blocks))

    def test_custom_bookmarks_appear_without_generic_duplicates(self):
        c = {'dashboard': {'bookmarks': [{'label': '가상 학교', 'url': 'https://example.org'}]}}
        blocks = quick_links(c, {})
        self.assertIn('가상 학교', content(blocks))
        self.assertNotIn('학교 홈페이지', content(blocks))
        self.assertEqual(['https://example.org'], linked_urls(blocks))

    def test_middle_contents_use_only_available_modules_and_custom_share_links(self):
        links = {key: 'https://www.notion.so/' + key for key in
                 ('resources', 'semester_1', 'semester_2', 'students', 'staff', 'accounts', 'agenda')}
        c = {'dashboard': {'links': {'shared_page': 'https://example.org/shared'}}}
        left, center, right = middle_content(c, links)
        self.assertIn('1학기 수업 진도', content(left))
        self.assertIn('2학기 수업 진도', content(left))
        self.assertIn('https://example.org/shared', linked_urls(left))
        self.assertNotIn('학부모 연락', content(left))
        self.assertNotIn('상담 기록', content(left))
        self.assertEqual(['https://www.notion.so/students'], linked_urls(center))
        self.assertIn('바로 아래', content(center))
        self.assertIn('교직원 연락처', content(right))
        self.assertIn('학교 업무 계정 안내', content(right))

    def test_external_database_links_reject_credentials_too(self):
        with self.assertRaises(ValueError):
            quick_links({}, {'timetable': 'https://name:password@example.org'})

    def test_no_private_or_external_links_in_default_empty_layout(self):
        self.assertEqual([], linked_urls(quick_links({}, {}) + sum(middle_content({}, {}), [])))
        with self.assertRaises(ValueError):
            section_heading('제목', 'not_a_color')


class DashboardMatrixTests(unittest.TestCase):
    def test_blank_default_has_seven_periods_and_empty_common_row_times(self):
        table = matrix_table({})
        self.assertEqual(7, table['table']['table_width'])
        self.assertEqual(9, len(table['table']['children']))
        self.assertEqual(['교시', '수업시간', '월', '화', '수', '목', '금'],
                         [content(cell) for cell in cells(table, 0)])
        self.assertEqual('공동', content(cells(table, 8)[0]))
        for row in table['table']['children'][1:]:
            self.assertTrue(all(cell == [] for cell in row['table_row']['cells'][1:]))

    def test_header_dates_and_lesson_weekdays_cross_month_correctly(self):
        table = matrix_table({}, [lesson('2026-09-28'), lesson('2026-10-02', 3)])
        self.assertEqual(['월\n09/28', '화\n09/29', '수\n09/30', '목\n10/01', '금\n10/02'],
                         [content(cell) for cell in cells(table, 0)[2:]])
        self.assertIn('영어', content(cells(table, 1)[2]))
        self.assertIn('영어', content(cells(table, 3)[6]))
        self.assertEqual([], cells(table, 3)[2])

    def test_all_day_rows_leave_time_column_blank(self):
        row = lesson(date_range={'start': '2026-09-28'})
        self.assertEqual([], cells(matrix_table({}, [row]), 1)[1])

    def test_eighth_and_twentieth_period_expand_without_losing_rows(self):
        for period in (8, 20):
            table = matrix_table({}, [lesson(period=period)])
            with self.subTest(period=period):
                self.assertEqual(period + 2, len(table['table']['children']))
                self.assertIn('영어', content(cells(table, period)[2]))
                self.assertEqual('공동', content(cells(table, period + 1)[0]))

    def test_configured_height_keeps_empty_existing_periods(self):
        table = matrix_table({'dashboard': {'periods': 12}}, [lesson()])
        self.assertEqual(14, len(table['table']['children']))
        self.assertEqual('12교시', content(cells(table, 12)[0]))

    def test_multiple_weeks_and_wrong_explicit_week_fail(self):
        with self.assertRaises(ValueError):
            matrix_table({}, [lesson(), lesson('2026-10-05')])
        with self.assertRaises(ValueError):
            matrix_table({}, [lesson()], week_start='2026-09-21')
        with self.assertRaises(ValueError):
            matrix_table({}, [], week_start='2026-09-29')

    def test_weekends_are_not_put_into_weekday_columns(self):
        table = matrix_table({}, [lesson('2026-10-03', subject='토요일 수업'),
                                  lesson('2026-10-04', subject='일요일 수업')])
        self.assertNotIn('토요일 수업', content(table))
        self.assertNotIn('일요일 수업', content(table))
        self.assertEqual('월\n09/28', content(cells(table, 0)[2]))

    def test_multiple_records_in_a_slot_are_all_visible(self):
        table = matrix_table({}, [lesson(subject='영어'), lesson(subject='수학', class_name='2학년 2반')])
        cell = cells(table, 1)[2]
        self.assertIn('영어', content(cell))
        self.assertIn('수학', content(cell))
        self.assertIn('1학년 1반', content(cell))
        self.assertIn('2학년 2반', content(cell))

    def test_class_colors_are_stable_across_input_order_and_date(self):
        first = lesson()
        second = lesson('2026-09-29', class_name='2학년 2반')
        a = matrix_table({}, [first, second])
        b = matrix_table({}, [second, first])
        self.assertEqual(cells(a, 1)[2][0]['annotations']['color'],
                         cells(b, 1)[2][0]['annotations']['color'])
        next_week = matrix_table({}, [lesson('2026-10-05')])
        self.assertEqual(cells(a, 1)[2][0]['annotations']['color'],
                         cells(next_week, 1)[2][0]['annotations']['color'])

    def test_cancellation_is_explicit_and_changed_lesson_is_marked(self):
        table = matrix_table({}, [lesson(status='휴강'), lesson('2026-09-29', status='변경')])
        self.assertIn('취소', content(cells(table, 1)[2]))
        self.assertTrue(cells(table, 1)[2][0]['annotations']['strikethrough'])
        self.assertIn('변경', content(cells(table, 1)[3]))

    def test_actual_times_and_utc_offsets_are_rendered_in_korean_time(self):
        row = lesson(date_range={'start': '2026-09-28T00:00:00Z', 'end': '2026-09-28T00:45:00Z'})
        self.assertEqual('09:00–09:45', content(cells(matrix_table({}, [row]), 1)[1]))

    def test_different_times_in_same_period_stay_in_their_own_cells(self):
        rows = [lesson(date_range={'start': '2026-09-28T09:00:00+09:00', 'end': '2026-09-28T09:45:00+09:00'}),
                lesson('2026-09-29', date_range={'start': '2026-09-29T09:10:00+09:00', 'end': '2026-09-29T09:55:00+09:00'})]
        table = matrix_table({}, rows)
        self.assertEqual('수업별 상이', content(cells(table, 1)[1]))
        self.assertIn('09:00–09:45', content(cells(table, 1)[2]))
        self.assertIn('09:10–09:55', content(cells(table, 1)[3]))

    def test_invalid_period_and_mismatched_timed_date_fail(self):
        for period in (True, 0, 21, '1'):
            with self.subTest(period=period), self.assertRaises(ValueError):
                matrix_table({}, [lesson(period=period)])
        with self.assertRaises(ValueError):
            matrix_table({}, [lesson(date_range={'start': '2026-10-05T09:00:00+09:00'})])

    def test_caption_matches_friday_and_can_be_undated(self):
        self.assertEqual('paragraph', matrix_title('2026-09-28')['type'])
        self.assertIn('2026-10-02', content(matrix_title(date(2026, 9, 28))))
        self.assertNotIn('2026', content(matrix_title(None)))


if __name__ == '__main__':
    unittest.main()
