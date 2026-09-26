"""Offline fixtures only: identifiers and school/event names are invented."""
import copy
import io
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from teacher_planner.neis import (
    ENDPOINT, GRADE_FIELDS, MAX_BYTES, MAX_ROWS, PAGE_SIZE, SOURCE_URL, TIMEOUT,
    NeisError, _NoRedirect, event_id, fetch_schedule,
)

OFFICE = 'Z99'
SCHOOL = '0000001'
KEY = 'fictional-neis-key-DO-NOT-LOG'


def event(title='가상 행사', day='20260303', **changes):
    result = {
        'ATPT_OFCDC_SC_CODE': OFFICE, 'SD_SCHUL_CODE': SCHOOL,
        'SCHUL_NM': '가상 테스트 학교', 'AY': '2026', 'AA_YMD': day,
        'EVENT_NM': title, 'EVENT_CNTNT': '가상 행사 설명',
        'DGHT_CRSE_SC_NM': '주간', 'SCHUL_CRSE_SC_NM': '중학교',
        'SBTR_DD_SC_NM': '해당없음', 'LOAD_DTM': '20260925',
        **{field: ('Y' if index < 3 else '*') for index, field in enumerate(GRADE_FIELDS)},
    }
    result.update(changes)
    return result


def page(rows, total=None, code='INFO-000'):
    return {'SchoolSchedule': [
        {'head': [{'list_total_count': len(rows) if total is None else total},
                  {'RESULT': {'CODE': code, 'MESSAGE': '가상 응답'}}]},
        {'row': rows},
    ]}


class Response(io.BytesIO):
    def __init__(self, body, url, status=200):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode('utf-8'))
        self.url = url
        self.status = status
        self.read_limits = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_limits.append(size)
        return super().read(size)


class FakeOpener:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []
        self.responses = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if not self.pages:
            raise AssertionError('Unexpected extra page request')
        payload = self.pages.pop(0)
        if isinstance(payload, Exception):
            raise payload
        response = payload if isinstance(payload, Response) else Response(payload, request.full_url)
        self.responses.append(response)
        return response


def fetch(opener, **kwargs):
    return fetch_schedule(OFFICE, SCHOOL, 2026, api_key=KEY, opener=opener, **kwargs)


class NeisProviderTests(unittest.TestCase):
    def test_complete_snapshot_and_fixed_authenticated_query(self):
        opener = FakeOpener(page([event()]))
        result = fetch(opener)
        self.assertEqual(result['source'], 'neis')
        self.assertEqual(result['source_url'], SOURCE_URL)
        self.assertEqual(result['office_code'], OFFICE)
        self.assertEqual(result['school_code'], SCHOOL)
        self.assertEqual(result['school_name'], '가상 테스트 학교')
        self.assertEqual(result['academic_year'], 2026)
        self.assertEqual((result['start'], result['end']), ('2026-03-01', '2027-02-28'))
        self.assertEqual(datetime.fromisoformat(result['fetched_at']).utcoffset().total_seconds(), 0)
        row = result['rows'][0]
        self.assertEqual(row['date'], '2026-03-03')
        self.assertEqual(row['grades'], [1, 2, 3])
        self.assertEqual(row['description'], '가상 행사 설명')
        self.assertEqual(row['updated_at'], '20260925')
        self.assertEqual(row['day_type'], '해당없음')
        self.assertEqual(row['external_id'], event_id(OFFICE, SCHOOL, '2026-03-03', '가상 행사', '주간', '중학교'))
        request, timeout = opener.calls[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.scheme + '://' + url.netloc + url.path, ENDPOINT)
        query = parse_qs(url.query)
        self.assertEqual(query, {
            'KEY': [KEY], 'Type': ['json'], 'pSize': ['1000'], 'pIndex': ['1'],
            'ATPT_OFCDC_SC_CODE': [OFFICE], 'SD_SCHUL_CODE': [SCHOOL],
            'AA_FROM_YMD': ['20260301'], 'AA_TO_YMD': ['20270228'],
        })
        self.assertEqual(timeout, TIMEOUT)
        self.assertEqual(opener.responses[0].read_limits, [MAX_BYTES + 1])
        self.assertNotIn(KEY, json.dumps(result))

    def test_paginate_exact_total_and_sort_results(self):
        rows = [event(f'가상 행사 {number:04d}') for number in range(PAGE_SIZE, -1, -1)]
        opener = FakeOpener(page(rows[:PAGE_SIZE], PAGE_SIZE + 1), page(rows[PAGE_SIZE:], PAGE_SIZE + 1))
        result = fetch(opener)
        self.assertEqual(len(result['rows']), PAGE_SIZE + 1)
        self.assertEqual(result['rows'][0]['title'], '가상 행사 0000')
        self.assertEqual([parse_qs(urlsplit(call[0].full_url).query)['pIndex'] for call in opener.calls], [['1'], ['2']])

    def test_exact_full_page_does_not_request_empty_next_page(self):
        opener = FakeOpener(page([event(f'가상 {i}') for i in range(PAGE_SIZE)]))
        self.assertEqual(len(fetch(opener)['rows']), PAGE_SIZE)
        self.assertEqual(len(opener.calls), 1)

    def test_info_200_first_page_is_legitimate_empty(self):
        result = fetch(FakeOpener({'RESULT': {'CODE': 'INFO-200', 'MESSAGE': '없음'}}))
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['school_name'], '')

    def test_zero_total_empty_page_is_legitimate(self):
        for code in ('INFO-000', 'INFO-200'):
            with self.subTest(code=code):
                self.assertEqual(fetch(FakeOpener(page([], code=code)))['rows'], [])

    def test_info_200_after_nonempty_page_is_incomplete(self):
        rows = [event(f'가상 {i}') for i in range(PAGE_SIZE)]
        with self.assertRaises(NeisError):
            fetch(FakeOpener(page(rows, PAGE_SIZE + 1), {'RESULT': {'CODE': 'INFO-200'}}))

    def test_api_error_is_not_empty_and_does_not_echo_raw_message(self):
        for code in ('ERROR-290', 'ERROR-337', 'INFO-300', 'ERROR-300', 'ERROR-500', 'INFO-100', KEY):
            with self.subTest(code=code), self.assertRaises(NeisError) as error:
                fetch(FakeOpener({'RESULT': {'CODE': code, 'MESSAGE': ENDPOINT + '?KEY=' + KEY}}))
            self.assertNotIn(KEY, str(error.exception))
            self.assertNotIn('https://', str(error.exception))

    def test_api_error_in_head_is_not_empty(self):
        with self.assertRaises(NeisError):
            fetch(FakeOpener(page([], 0, 'ERROR-290')))

    def test_empty_result_cannot_hide_a_nonempty_dataset(self):
        payload = page([event()])
        payload['RESULT'] = {'CODE': 'INFO-200'}
        with self.assertRaises(NeisError):
            fetch(FakeOpener(payload))

    def test_all_identifiers_validated_before_network(self):
        invalid = [('', SCHOOL, 2026), ('z99', SCHOOL, 2026), ('Z999', SCHOOL, 2026),
                   ('Z99?KEY=bad', SCHOOL, 2026), (OFFICE, '123', 2026),
                   (OFFICE, '../secret', 2026), (OFFICE, True, 2026),
                   (OFFICE, SCHOOL, True), (OFFICE, SCHOOL, 2026.0),
                   (OFFICE, SCHOOL, '2026x'), (OFFICE, SCHOOL, 9999),
                   (OFFICE, SCHOOL, '9' * 5000)]
        for office, school, year in invalid:
            opener = FakeOpener()
            with self.subTest(office=office, school=school, year=year), self.assertRaises(NeisError):
                fetch_schedule(office, school, year, api_key=KEY, opener=opener)
            self.assertEqual(opener.calls, [])

    def test_missing_or_sample_key_never_makes_request(self):
        for key in (None, '', ' ', 'sample', 'SAMPLE', 'sample key', ' sample_key ', 'bad\nkey', '키', 123):
            opener = FakeOpener()
            with self.subTest(key=key), self.assertRaises(NeisError):
                fetch_schedule(OFFICE, SCHOOL, 2026, api_key=key, opener=opener)
            self.assertEqual(opener.calls, [])

    def test_custom_range_accepts_iso_and_date_values(self):
        opener = FakeOpener(page([event()]))
        result = fetch(opener, start=date(2026, 3, 3), end='2026-03-03')
        self.assertEqual((result['start'], result['end']), ('2026-03-03', '2026-03-03'))

    def test_school_year_includes_next_february_and_leap_day(self):
        opener = FakeOpener(page([event(day='20240229', AY='2023')]))
        result = fetch_schedule(OFFICE, SCHOOL, 2023, api_key=KEY, opener=opener)
        self.assertEqual(result['end'], '2024-02-29')
        self.assertEqual(result['rows'][0]['date'], '2024-02-29')

    def test_invalid_or_outside_request_range_fails_before_network(self):
        pairs = [('2026-02-28', None), (None, '2027-03-01'), ('2026-03-04', '2026-03-03'),
                 ('20260303', None), ('2026-3-3', None), ('2026-02-30', None),
                 (datetime(2026, 3, 3, tzinfo=timezone.utc), None), (False, None)]
        for start, end in pairs:
            opener = FakeOpener()
            with self.subTest(start=start, end=end), self.assertRaises(NeisError):
                fetch(opener, start=start, end=end)
            self.assertEqual(opener.calls, [])

    def test_response_identity_or_year_mismatch_fails(self):
        for field, value in [('ATPT_OFCDC_SC_CODE', 'Z98'), ('SD_SCHUL_CODE', '0000002'), ('AY', '2025')]:
            with self.subTest(field=field), self.assertRaises(NeisError):
                fetch(FakeOpener(page([event(**{field: value})])))

    def test_response_dates_must_be_valid_and_inside_requested_range(self):
        for day in ('20260228', '20270301', '20260230', '2026-03-03', '202603', None):
            with self.subTest(day=day), self.assertRaises(NeisError):
                fetch(FakeOpener(page([event(day=day)])))
        with self.assertRaises(NeisError):
            fetch(FakeOpener(page([event(day='20260401')])), start='2026-03-01', end='2026-03-31')

    def test_required_fields_and_unknown_grade_flags_fail(self):
        for field in ('EVENT_NM', 'SCHUL_NM', 'AY', 'AA_YMD', 'DGHT_CRSE_SC_NM', 'SCHUL_CRSE_SC_NM', *GRADE_FIELDS):
            raw = event()
            del raw[field]
            with self.subTest(field=field), self.assertRaises(NeisError):
                fetch(FakeOpener(page([raw])))
        for change in ({'EVENT_NM': '  '}, {'EVENT_CNTNT': []}, {'ONE_GRADE_EVENT_YN': 'yes'}):
            with self.subTest(change=change), self.assertRaises(NeisError):
                fetch(FakeOpener(page([event(**change)])))

    def test_nullable_optional_fields_and_unknown_grade_do_not_invent_all_school(self):
        raw = event(EVENT_CNTNT=None, LOAD_DTM=None, SBTR_DD_SC_NM=None,
                    **{field: None for field in GRADE_FIELDS})
        result = fetch(FakeOpener(page([raw])))['rows'][0]
        self.assertEqual(result['description'], '')
        self.assertEqual(result['grades'], [])
        self.assertEqual(result['day_type'], '')
        self.assertNotIn('updated_at', result)

    def test_content_grade_and_modified_time_changes_preserve_id(self):
        original = fetch(FakeOpener(page([event()])))['rows'][0]
        changed = fetch(FakeOpener(page([event(EVENT_CNTNT='다른 설명', ONE_GRADE_EVENT_YN='N', LOAD_DTM='20260926')])))['rows'][0]
        self.assertEqual(original['external_id'], changed['external_id'])
        self.assertNotEqual(original['description'], changed['description'])
        self.assertNotEqual(original['grades'], changed['grades'])

    def test_source_identity_parts_have_distinct_ids(self):
        identity = [OFFICE, SCHOOL, '2026-03-03', '가상 행사', '주간', '중학교']
        original = event_id(*identity)
        for index in range(len(identity)):
            changed = list(identity)
            changed[index] += '다름'
            with self.subTest(index=index):
                self.assertNotEqual(original, event_id(*changed))
        self.assertNotEqual(event_id('a', 'bc', '', '', '', ''), event_id('ab', 'c', '', '', '', ''))

    def test_whitespace_normalized_before_id(self):
        first = fetch(FakeOpener(page([event()])))['rows'][0]
        second = fetch(FakeOpener(page([event(EVENT_NM='  가상 행사  ', DGHT_CRSE_SC_NM=' 주간 ')])))['rows'][0]
        self.assertEqual(first['external_id'], second['external_id'])

    def test_repeated_identity_is_rejected_even_if_exact_duplicate(self):
        for second in (event(), event(EVENT_CNTNT='다른 설명')):
            with self.subTest(second=second['EVENT_CNTNT']), self.assertRaises(NeisError):
                fetch(FakeOpener(page([event(), second])))

    def test_distinct_courses_and_daynight_stay_separate(self):
        rows = [event(), event(DGHT_CRSE_SC_NM='야간'), event(SCHUL_CRSE_SC_NM='고등학교')]
        self.assertEqual(len(fetch(FakeOpener(page(rows)))['rows']), 3)

    def test_conflicting_school_names_fail(self):
        with self.assertRaises(NeisError):
            fetch(FakeOpener(page([event(), event(title='다른 행사', SCHUL_NM='다른 가상 학교')])))

    def test_changed_count_or_short_page_fails(self):
        rows = [event(f'가상 {i}') for i in range(PAGE_SIZE)]
        invalid = [FakeOpener(page(rows[:5], 6)),
                   FakeOpener(page(rows, PAGE_SIZE + 1), page([event('마지막')], PAGE_SIZE + 2)),
                   FakeOpener(page(rows, PAGE_SIZE + 1), page([], PAGE_SIZE + 1)),
                   FakeOpener(page(rows, PAGE_SIZE + 1), page([event('가상 0')], PAGE_SIZE + 1))]
        for opener in invalid:
            with self.subTest(pages=len(opener.pages)), self.assertRaises(NeisError):
                fetch(opener)

    def test_total_and_shape_validation(self):
        payloads = [None, [], {}, {'SchoolSchedule': {}}, {'SchoolSchedule': []},
                    {'SchoolSchedule': [{'head': []}]}, page([event()], -1),
                    page([], MAX_ROWS + 1), page([], True), page([], 'not a count'),
                    page([event()], 0), page([], 1), page([None])]
        missing_result = page([])
        missing_result['SchoolSchedule'][0]['head'].pop()
        duplicated_head = page([])
        duplicated_head['SchoolSchedule'].append(copy.deepcopy(duplicated_head['SchoolSchedule'][0]))
        payloads += [missing_result, duplicated_head]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(NeisError):
                fetch(FakeOpener(payload))

    def test_invalid_json_and_oversized_response_are_bounded(self):
        for body in (b'<html>not json</html>', b'{"broken":', b'\xff', b'x' * (MAX_BYTES + 1),
                     b'{"RESULT":{"CODE":"ERROR-290","CODE":"INFO-200"}}'):
            opener = FakeOpener(body)
            with self.subTest(size=len(body)), self.assertRaises(NeisError):
                fetch(opener)
            self.assertEqual(opener.responses[0].read_limits, [MAX_BYTES + 1])

    def test_network_errors_never_expose_key_url_or_cause(self):
        secret_url = ENDPOINT + '?KEY=' + KEY
        for failure in (URLError(secret_url), HTTPError(secret_url, 401, KEY, {}, None),
                        TimeoutError(KEY), RuntimeError(secret_url), NeisError(secret_url)):
            with self.subTest(type=type(failure).__name__), self.assertRaises(NeisError) as error:
                fetch(FakeOpener(failure))
            self.assertNotIn(KEY, str(error.exception))
            self.assertNotIn('https://', str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)

    def test_redirects_and_non_200_responses_rejected(self):
        for url in ('http://open.neis.go.kr/hub/SchoolSchedule', 'https://evil.invalid/hub/SchoolSchedule',
                    'https://open.neis.go.kr/elsewhere', 'https://user:password@open.neis.go.kr/hub/SchoolSchedule'):
            with self.subTest(url=url), self.assertRaises(NeisError):
                fetch(FakeOpener(Response(page([]), url)))
        with self.assertRaises(NeisError):
            fetch(FakeOpener(Response(page([]), ENDPOINT, status=302)))
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))

    def test_default_opener_installs_redirect_blocker(self):
        opener = FakeOpener(page([]))
        with patch('teacher_planner.neis.build_opener') as build:
            build.return_value.open = opener
            fetch_schedule(OFFICE, SCHOOL, 2026, api_key=KEY)
        build.assert_called_once_with(_NoRedirect)
        self.assertEqual(len(opener.calls), 1)


if __name__ == '__main__':
    unittest.main()
