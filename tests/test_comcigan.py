"""Offline protocol and data-loss checks using entirely invented school data."""
import base64
import copy
import json
import tempfile
import unittest
from datetime import date
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit

from teacher_planner.comcigan import (
    CLIENT_URL, ComciganError, Protocol, _fetch, discover, fetch_week, normalize, parse_week,
)
from teacher_planner.install import install
from teacher_planner.model import config as load_config
from teacher_planner.timetable import apply_changes, changes, text_property
from test_planner import FakeNotion


MONDAY = date(2026, 9, 28)
PROTOCOL = Protocol('13579_T?', '24680_', '자료11', '자료12', '자료13',
                    '자료14', '자료15', '자료16')


def client_html():
    # These deliberately tiny role expressions are synthetic, not a saved client.
    return """<meta charset="euc-kr"><script>
      var request = './13579_T?' + btoa(value);
      sc_data('24680_', code, day, mode);
      var name = 자료.자료11[i];
      과목명 = Q과목명(자료.자료12[sb]);
      교사자료 = Q자료(자료.자료13[교사][요일][교시]);
      원자료 = Q자료(자료.자료14[학년][반][요일][교시]);
      var m3 = 자료.자료15[학년][반][요일][교시];
      da1 = H시간표.자료16;
    </script>"""


def week_grid(blank=0):
    return [[blank] * 9 for _ in range(6)]


def payload():
    original = [[], [[], week_grid()], [[], week_grid()], []]
    current = [[], week_grid(), week_grid()]
    rooms = [[], [[], week_grid('')], [[], week_grid('')], []]
    original[1][1][1][1] = 1001  # English / teacher 1, Monday period 1.
    current[1][1][1] = 1101     # English / class 101, unchanged.
    original[1][1][2][2] = 2001  # Math originally on Tuesday period 2.
    current[1][2][2] = 0       # Explicit zero means cancellation.
    current[1][3][3] = '>1201'  # Added English in class 201 on Wednesday.
    rooms[2][1][3][3] = '1_가상 실습실'
    current[2][5][4] = 2101    # Another teacher must never appear in the result.
    return {
        '시작일': '2026-09-28', '열람제한일': '', '교사수': 2,
        '학급수': [0, 1, 1, 0], '분리': 1000, '변경알림': 1, '강의실': 1,
        '자료11': ['', '가상 교사', '다른 가상 교사'],
        '자료12': ['', '영어', '수학'], '자료13': current,
        '자료14': original, '자료15': rooms, '자료16': '가상 수정 시각',
        '일자자료': [[1, '가상 첫째 주']], '오늘r': 1,
        '학교전달': '전체 학교 응답은 결과에 남기지 않습니다.',
    }


def parse(data=None, requested=MONDAY, teacher_id=1):
    return parse_week(payload() if data is None else data, PROTOCOL,
                      '98765', teacher_id, requested)


def config():
    return {
        'academic_year': 2026, 'timezone': 'Asia/Seoul', 'teacher': '내 표시 이름',
        'subjects': ['영어', '수학'],
        'classes': [{'name': '1학년 1반'}, {'name': '2학년 1반'}],
    }


class ProtocolDiscoveryTests(unittest.TestCase):
    def test_discovers_synthetic_rotating_keys(self):
        self.assertEqual(PROTOCOL, discover(client_html()))
        rotated = client_html().replace('13579_T', '97531_T').replace('자료13', '자료991')
        actual = discover(rotated)
        self.assertEqual('97531_T?', actual.endpoint)
        self.assertEqual('자료991', actual.current)

    def test_missing_or_ambiguous_role_fails_closed(self):
        for html in (client_html().replace('교사자료 =', 'unrecognized ='),
                     client_html() + '교사자료=Q자료(자료.자료999[교사][요일][교시]);'):
            with self.subTest(html=html):
                with self.assertRaises(ComciganError):
                    discover(html)

    def test_absolute_or_traversing_endpoint_is_rejected(self):
        for endpoint in ('http://example.invalid/13579_T?', './../13579_T?'):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ComciganError):
                    discover(client_html().replace('./13579_T?', endpoint))


class ParseWeekTests(unittest.TestCase):
    def test_selected_teacher_normal_changed_and_cancelled_rows(self):
        result = parse()
        rows = result['rows']
        self.assertEqual('가상 교사', result['teacher_name'])
        self.assertEqual('2026-09-28', result['week_start'])
        self.assertEqual(0, result['omitted_slots'])
        self.assertEqual([
            ('2026-09-28', 1, '1학년 1반', '영어', '예정'),
            ('2026-09-29', 2, '1학년 1반', '수학', '휴강'),
            ('2026-09-30', 3, '2학년 1반', '영어', '변경'),
        ], [(r['date'], r['period'], r['class_name'], r['subject'], r['status']) for r in rows])
        self.assertEqual('가상 실습실', rows[2]['room'])
        self.assertNotIn('학교전달', result)
        self.assertNotIn('다른 가상 교사', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('자료13', result)

    def test_any_date_in_same_week_resolves_to_monday(self):
        self.assertEqual('2026-09-28', parse(requested=date(2026, 10, 4))['week_start'])

    def test_wrong_week_and_non_monday_source_fail(self):
        for start in ('2026-09-21', '2026-09-29'):
            data = payload()
            data['시작일'] = start
            with self.subTest(start=start), self.assertRaises(ComciganError):
                parse(data)

    def test_hidden_week_fails_and_following_saturday_is_allowed(self):
        for limit in ('2026-09-01', '2026-09-28', '2026-10-02'):
            data = payload()
            data['열람제한일'] = limit
            with self.subTest(limit=limit), self.assertRaises(ComciganError):
                parse(data)
        for limit in ('2026-10-03', '2013-01-01'):
            data = payload()
            data['열람제한일'] = limit
            self.assertEqual(3, len(parse(data)['rows']))

    def test_invalid_dates_fail_without_guessing(self):
        for key, value in (('시작일', '9/28'), ('시작일', '2026-02-30'),
                           ('열람제한일', 'not a date')):
            data = payload()
            data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ComciganError):
                parse(data)
        data = payload()
        data['시작일'] = '2026.9.28.'
        self.assertEqual('2026-09-28', parse(data)['week_start'])

    def test_missing_current_day_fails(self):
        data = payload()
        data['자료13'][1] = data['자료13'][1][:5]
        with self.assertRaises(ComciganError):
            parse(data)

    def test_missing_trailing_current_slot_never_becomes_cancellation(self):
        data = payload()
        data['자료13'][1][2] = [0, 0]  # Tuesday period 2 is absent, not explicit zero.
        result = parse(data)
        self.assertEqual(7, result['omitted_slots'])
        self.assertEqual(2, len(result['rows']))
        self.assertFalse(any(r['status'] == '휴강' for r in result['rows']))

    def test_empty_day_is_missing_but_count_only_day_is_known_sparse(self):
        data = payload()
        data['자료13'][1][2] = []
        with self.assertRaises(ComciganError):
            parse(data)
        data['자료13'][1][2] = [0]
        result = parse(data)
        self.assertEqual(8, result['omitted_slots'])
        self.assertEqual(2, len(result['rows']))

    def test_missing_original_data_is_not_guessed(self):
        data = payload()
        del data['자료14']
        with self.assertRaises(ComciganError):
            parse(data)

    def test_duplicate_original_slot_fails(self):
        data = payload()
        data['자료14'][2][1][1][1] = 1001
        with self.assertRaises(ComciganError):
            parse(data)

    def test_trailing_missing_original_slot_is_not_a_cancellation(self):
        data = payload()
        data['자료14'][1][1][2] = [0]
        self.assertEqual(2, len(parse(data)['rows']))

    def test_invalid_current_cells_fail(self):
        for cell in (None, True, -1, 1101.0, '>bad', '>>1101', '>'):
            data = payload()
            data['자료13'][1][1][1] = cell
            with self.subTest(cell=cell), self.assertRaises(ComciganError):
                parse(data)

    def test_invalid_teacher_identifiers_fail(self):
        for teacher in (0, -1, True, 3, 1.5, 'invalid'):
            with self.subTest(teacher=teacher), self.assertRaises(ComciganError):
                parse(teacher_id=teacher)

    def test_unknown_subject_and_invalid_class_fail(self):
        for cell in (9101, 1000, 1001, 1301, 1102):
            data = payload()
            data['자료13'][1][1][1] = cell
            with self.subTest(cell=cell), self.assertRaises(ComciganError):
                parse(data)

    def test_ninth_period_is_not_silently_dropped(self):
        data = payload()
        data['자료13'][1][1].append(1101)
        with self.assertRaises(ComciganError):
            parse(data)

    def test_omitted_final_room_cell_is_blank_and_explicit_room_survives(self):
        for rooms in ([], [0], {}):
            data = payload()
            data['자료15'][1][1][1] = rooms
            with self.subTest(rooms=rooms):
                rows = parse(data)['rows']
                self.assertEqual('', rows[0]['room'])
                self.assertEqual('가상 실습실', rows[2]['room'])

    def test_group_subject_uses_base_subject_and_keeps_group(self):
        data = payload()
        data['자료13'][1][1][1] = '>1001101'
        row = parse(data)['rows'][0]
        self.assertEqual(('영어', 'A', '변경'), (row['subject'], row['group'], row['status']))

    def test_without_marker_mode_compares_actual_lesson(self):
        data = payload()
        data['변경알림'] = 0
        data['자료13'][1][3][3] = 1201
        self.assertEqual(['예정', '휴강', '변경'], [r['status'] for r in parse(data)['rows']])

    def test_legacy_original_encoding_still_identifies_selected_teacher(self):
        data = payload()
        data['분리'] = 100
        data['자료14'][1][1][1][1] = 101
        data['자료14'][1][1][2][2] = 102
        self.assertEqual(['영어', '수학', '영어'], [r['subject'] for r in parse(data)['rows']])

    def test_string_indexed_protocol_arrays_are_supported(self):
        def as_dict(value):
            if isinstance(value, list):
                return {str(i): as_dict(v) for i, v in enumerate(value)}
            return value
        data = payload()
        for key in ('학급수', '자료11', '자료12', '자료13', '자료14', '자료15'):
            data[key] = as_dict(data[key])
        self.assertEqual(3, len(parse(data)['rows']))


class FetchWeekTests(unittest.TestCase):
    def test_network_failure_hides_school_query_and_handles_incomplete_reads(self):
        for error in (URLError('encoded-school-query'), IncompleteRead(b'partial data')):
            with self.subTest(error=type(error).__name__), patch('teacher_planner.comcigan.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(ComciganError) as caught:
                    _fetch(CLIENT_URL)
                self.assertNotIn('encoded-school-query', str(caught.exception))
                self.assertNotIn('partial data', str(caught.exception))

    def test_fetch_discovers_protocol_and_strips_non_json_suffix(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url == CLIENT_URL:
                return client_html().encode('cp949')
            return json.dumps(payload(), ensure_ascii=False).encode('cp949') + b'\x00\x00'

        result = fetch_week('98765', 1, MONDAY, fetch=fetch)
        self.assertEqual(3, len(result['rows']))
        self.assertEqual(2, len(calls))
        self.assertEqual('/13579_T', urlsplit(calls[1]).path)
        self.assertEqual('24680_98765_0_1', base64.b64decode(urlsplit(calls[1]).query).decode())

    def test_only_advertised_other_week_is_requested_and_verified(self):
        requested_selectors = []
        initial = payload()
        initial['시작일'] = '2026-09-21'
        initial['일자자료'] = [[1, '지난 주'], [7, '표시만으로 날짜를 믿지 않음']]

        def fetch(url):
            if url == CLIENT_URL:
                return client_html().encode('utf-8')
            selector = int(base64.b64decode(urlsplit(url).query).decode().rsplit('_', 1)[1])
            requested_selectors.append(selector)
            return json.dumps(initial if selector == 1 else payload()).encode()

        self.assertEqual('2026-09-28', fetch_week('98765', 1, MONDAY, fetch=fetch)['week_start'])
        self.assertEqual([1, 7], requested_selectors)

    def test_unavailable_week_is_not_relabelled(self):
        data = payload()
        data['시작일'] = '2026-09-21'

        def fetch(url):
            return client_html().encode() if url == CLIENT_URL else json.dumps(data).encode()

        with self.assertRaises(ComciganError):
            fetch_week('98765', 1, MONDAY, fetch=fetch)

    def test_invalid_identifiers_make_no_network_requests(self):
        def fetch(url):
            self.fail('Invalid identifiers must fail before network access.')
        for school, teacher in (('../th', 1), ('12?3', 1), ('98765', True), ('98765', 0)):
            with self.subTest(school=school, teacher=teacher), self.assertRaises(ComciganError):
                fetch_week(school, teacher, MONDAY, fetch=fetch)

    def test_non_json_school_response_fails(self):
        for raw in (b'', b' {}\x00', b'[]', b'<html>Unavailable</html>'):
            def fetch(url):
                return client_html().encode() if url == CLIENT_URL else raw
            with self.subTest(raw=raw), self.assertRaises(ComciganError):
                fetch_week('98765', 1, MONDAY, fetch=fetch)


class NormalizeTests(unittest.TestCase):
    def test_default_all_day_rows_do_not_invent_times(self):
        rows = normalize(parse(), config())
        self.assertEqual({'start': '2026-09-28'}, rows[0]['date_range'])
        self.assertTrue(rows[0]['external_id'].startswith('tt:comci:'))
        self.assertEqual(3, len({r['external_id'] for r in rows}))

    def test_ids_stay_stable_when_lesson_changes_but_distinguish_identity(self):
        snap = parse()
        original = normalize(snap, config())[0]['external_id']
        changed = copy.deepcopy(snap)
        changed['rows'][0].update(subject='수학', room='다른 교실', status='변경', class_name='2학년 1반')
        self.assertEqual(original, normalize(changed, config())[0]['external_id'])
        for key, value in (('school_code', '87654'), ('teacher_id', 2)):
            different = copy.deepcopy(snap)
            different[key] = value
            self.assertNotEqual(original, normalize(different, config())[0]['external_id'])

    def test_explicit_period_times_use_korean_timezone(self):
        times = {str(p): {'start': f'{p + 8:02}:00', 'end': f'{p + 8:02}:45'} for p in range(1, 4)}
        row = normalize(parse(), config(), period_times=times)[0]
        self.assertEqual({'start': '2026-09-28T09:00:00+09:00',
                          'end': '2026-09-28T09:45:00+09:00'}, row['date_range'])

    def test_incomplete_or_invalid_period_times_fail(self):
        for times in ({}, {'1': {'start': '09:00'}}, {'1': {'start': '09:45', 'end': '09:00'}},
                      {'1': {'start': '9:00', 'end': '09:45'}}):
            with self.subTest(times=times), self.assertRaises(ComciganError):
                normalize(parse(), config(), period_times=times)

    def test_mapping_connects_source_class_codes_and_subject_names(self):
        c = config()
        c['classes'] = [{'name': '수업반 A'}, {'name': '수업반 B'}]
        c['subjects'] = ['영어 A', '수학']
        mapped = normalize(parse(), c, mapping={
            'classes': {'1-1': '수업반 A', '2-1': '수업반 B'},
            'subjects': {'영어': '영어 A'},
        })
        self.assertEqual(('수업반 A', '영어 A'), (mapped[0]['class_name'], mapped[0]['subject']))

    def test_unknown_classes_subjects_and_academic_year_fail(self):
        for key, value in (('classes', []), ('subjects', ['다른 교과']), ('academic_year', 2025)):
            c = config()
            c[key] = value
            with self.subTest(key=key), self.assertRaises(ComciganError):
                normalize(parse(), c)

    def test_duplicate_rows_and_dates_outside_selected_week_fail(self):
        snap = parse()
        snap['rows'].append(copy.deepcopy(snap['rows'][0]))
        with self.assertRaises(ComciganError):
            normalize(snap, config())
        snap = parse()
        snap['rows'][0]['date'] = '2026-09-27'
        with self.assertRaises(ComciganError):
            normalize(snap, config())

    def test_overlapping_active_periods_fail(self):
        snap = parse()
        snap['rows'] = [snap['rows'][0], {**snap['rows'][0], 'period': 2}]
        times = {'1': {'start': '09:00', 'end': '09:50'},
                 '2': {'start': '09:30', 'end': '10:20'}}
        with self.assertRaises(ComciganError):
            normalize(snap, config(), period_times=times)


class ComciganNotionIntegrationTests(unittest.TestCase):
    """Exercise parsing through the actual planner, replacing only Notion I/O."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / 'state.json'
        self.c = load_config(Path(__file__).resolve().parents[1] / 'config.example.json')
        self.c['subjects'] = ['영어', '수학']
        self.c['classes'].append({'name': '2학년 1반', 'grade': 2,
                                  'class_name': '1', 'homeroom': False})
        self.api = FakeNotion()
        install(self.api, self.c, self.api.parent, self.state_path)
        self.state = json.loads(self.state_path.read_text())
        self.api.calls.clear()

    def sync(self, source):
        rows = normalize(parse(source), self.c)
        ops = changes(self.api, self.c, self.state, rows, source='컴시간 어댑터')
        return apply_changes(self.api, self.state_path, ops)

    def pages(self, source):
        return self.api.pages(self.state['databases'][source]['data_source_id'])

    def test_initial_import_then_second_poll_is_noop(self):
        self.assertEqual(6, self.sync(payload()))
        self.assertEqual(3, len(self.pages('timetable')))
        self.assertEqual(3, len(self.pages('agenda')))
        self.api.calls.clear()
        self.assertEqual(0, self.sync(payload()))
        self.assertEqual([], self.api.calls)
        self.assertTrue(all(p['properties']['출처']['select']['name'] == '컴시간 어댑터'
                            for p in self.pages('timetable')))

    def test_changed_subject_updates_existing_records_and_keeps_links(self):
        self.sync(payload())
        before = {source: {p['id'] for p in self.pages(source)}
                  for source in ('timetable', 'agenda')}
        data = payload()
        data['자료13'][1][1][1] = '>2101'
        self.api.calls.clear()
        self.assertEqual(2, self.sync(data))
        self.assertTrue(all(method == 'PATCH' for method, _, _ in self.api.calls))
        for source in ('timetable', 'agenda'):
            self.assertEqual(before[source], {p['id'] for p in self.pages(source)})
        first = self.pages('timetable')[0]['properties']
        self.assertEqual('수학', text_property({'properties': first}, '교과'))
        self.assertEqual('변경', first['상태']['select']['name'])
        self.assertIn(first['업무·일정']['relation'][0]['id'], before['agenda'])
        self.assertEqual(0, self.sync(data))

    def test_explicit_zero_cancels_timetable_and_agenda_once(self):
        self.sync(payload())
        data = payload()
        data['자료13'][1][1][1] = 0
        self.api.calls.clear()
        self.assertEqual(2, self.sync(data))
        self.assertEqual('휴강', self.pages('timetable')[0]['properties']['상태']['select']['name'])
        self.assertEqual('취소', self.pages('agenda')[0]['properties']['상태']['select']['name'])
        self.api.calls.clear()
        self.assertEqual(0, self.sync(data))
        self.assertEqual([], self.api.calls)

    def test_missing_slot_preserves_the_previously_synced_lesson(self):
        self.sync(payload())
        before = self.pages('timetable')[0]
        data = payload()
        data['자료13'][1][1] = [0]
        self.api.calls.clear()
        self.assertEqual(0, self.sync(data))
        self.assertEqual(before, self.pages('timetable')[0])
        self.assertEqual([], self.api.calls)

    def test_invalid_week_fails_before_any_notion_write(self):
        self.sync(payload())
        data = payload()
        data['시작일'] = '2026-09-21'
        self.api.calls.clear()
        with self.assertRaises(ComciganError):
            self.sync(data)
        self.assertEqual([], self.api.calls)


if __name__ == '__main__':
    unittest.main()
