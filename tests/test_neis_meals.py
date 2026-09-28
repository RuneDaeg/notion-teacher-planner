"""NEIS meal fixtures use invented schools, meal content and keys; no live API."""
import copy
import io
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from teacher_planner.neis_meals import (
    ENDPOINT, MAX_BYTES, MEAL_NAMES, SOURCE_URL, TIMEOUT,
    NeisMealsError, _NoRedirect, fetch_meals,
)

OFFICE = 'Z99'
SCHOOL = '0000001'
KEY = 'fictional-neis-meal-key-DO-NOT-LOG'
DAY = '2026-09-28'


def meal(code='2', **changes):
    result = {'ATPT_OFCDC_SC_CODE': OFFICE, 'SD_SCHUL_CODE': SCHOOL,
              'SCHUL_NM': '가상 테스트 학교', 'MMEAL_SC_CODE': code,
              'MMEAL_SC_NM': MEAL_NAMES.get(code, '가상 식사'), 'MLSV_YMD': '20260928',
              'DDISH_NM': '가상 쌀밥<br/>가상 달걀국 (1.5.6.)<br/>가상 우유 (2.)',
              'CAL_INFO': '650.0 Kcal', 'ORPLC_INFO': '쌀 : 국내산<br>달걀 : 국내산',
              'NTR_INFO': '탄수화물(g) : 80<br />단백질(g) : 20', 'LOAD_DTM': '20260927000000'}
    result.update(changes)
    return result


def page(rows, total=None, code='INFO-000'):
    return {'mealServiceDietInfo': [
        {'head': [{'list_total_count': len(rows) if total is None else total},
                  {'RESULT': {'CODE': code, 'MESSAGE': '가상 응답'}}]}, {'row': rows}]}


class Response(io.BytesIO):
    def __init__(self, payload, url, status=200):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8'))
        self.url, self.status, self.read_limits = url, status, []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_limits.append(size)
        return super().read(size)


class FakeOpener:
    def __init__(self, *pages):
        self.pages, self.calls, self.responses = list(pages), [], []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if not self.pages:
            raise AssertionError('Unexpected page request')
        payload = self.pages.pop(0)
        if isinstance(payload, Exception):
            raise payload
        response = payload if isinstance(payload, Response) else Response(payload, request.full_url)
        self.responses.append(response)
        return response


def fetch(opener, requested=DAY):
    return fetch_meals(OFFICE, SCHOOL, requested, api_key=KEY, opener=opener)


class NeisMealsTests(unittest.TestCase):
    def test_exact_contract_all_meals_sorted_and_allergen_numbers_preserved(self):
        opener = FakeOpener(page([meal('3'), meal('1'), meal('2')]))
        snapshot = fetch(opener)
        self.assertEqual(snapshot['source'], 'neis-meals')
        self.assertEqual(snapshot['source_url'], SOURCE_URL)
        self.assertEqual(snapshot['office_code'], OFFICE)
        self.assertEqual(snapshot['school_code'], SCHOOL)
        self.assertEqual(snapshot['school_name'], '가상 테스트 학교')
        self.assertEqual(snapshot['date'], DAY)
        self.assertEqual(datetime.fromisoformat(snapshot['fetched_at']).utcoffset().total_seconds(), 0)
        self.assertEqual([row['meal_code'] for row in snapshot['rows']], ['1', '2', '3'])
        self.assertEqual([row['meal_name'] for row in snapshot['rows']], ['조식', '중식', '석식'])
        self.assertEqual(snapshot['rows'][0], {
            'meal_code': '1', 'meal_name': '조식',
            'menu': '가상 쌀밥\n가상 달걀국 (1.5.6.)\n가상 우유 (2.)',
            'calories': '650.0 Kcal', 'origin': '쌀 : 국내산\n달걀 : 국내산',
            'nutrition': '탄수화물(g) : 80\n단백질(g) : 20', 'updated_at': '20260927000000'})
        self.assertNotIn(KEY, json.dumps(snapshot))

    def test_fixed_endpoint_exact_date_query_no_meal_filter_or_year(self):
        opener = FakeOpener(page([meal()]))
        fetch(opener)
        request, timeout = opener.calls[0]
        url = urlsplit(request.full_url)
        self.assertEqual(url.scheme + '://' + url.netloc + url.path, ENDPOINT)
        self.assertEqual(parse_qs(url.query), {
            'KEY': [KEY], 'Type': ['json'], 'pSize': ['1000'], 'pIndex': ['1'],
            'ATPT_OFCDC_SC_CODE': [OFFICE], 'SD_SCHUL_CODE': [SCHOOL], 'MLSV_YMD': ['20260928']})
        self.assertEqual(timeout, TIMEOUT)
        self.assertEqual(opener.responses[0].read_limits, [MAX_BYTES + 1])

    def test_default_date_is_today_in_seoul_across_utc_midnight(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                instant = cls(2026, 9, 27, 15, 1, tzinfo=timezone.utc)
                return instant.astimezone(tz)
        opener = FakeOpener(page([meal()]))
        with patch('teacher_planner.neis_meals.datetime', Clock):
            snapshot = fetch(opener, requested=None)
        self.assertEqual(snapshot['date'], '2026-09-28')
        self.assertTrue(snapshot['fetched_at'].startswith('2026-09-27T15:01:00'))

    def test_date_objects_and_leap_day_supported(self):
        opener = FakeOpener(page([meal(MLSV_YMD='20240229')]))
        self.assertEqual(fetch(opener, date(2024, 2, 29))['date'], '2024-02-29')

    def test_dates_use_four_digit_years_even_before_1000(self):
        opener = FakeOpener(page([meal(MLSV_YMD='00010928')]))
        self.assertEqual(fetch(opener, '0001-09-28')['date'], '0001-09-28')
        self.assertEqual(parse_qs(urlsplit(opener.calls[0][0].full_url).query)['MLSV_YMD'], ['00010928'])

    def test_invalid_request_dates_fail_before_network(self):
        for value in ('20260928', '2026-9-28', '2026-02-30', '2026-09-28T00:00:00',
                      datetime(2026, 9, 28), True, 20260928, ''):
            opener = FakeOpener()
            with self.subTest(value=value), self.assertRaises(NeisMealsError):
                fetch(opener, value)
            self.assertEqual(opener.calls, [])

    def test_identifiers_fail_before_network(self):
        for office, school in (('', SCHOOL), ('z99', SCHOOL), ('Z999', SCHOOL), ('Z99?KEY=x', SCHOOL),
                               (OFFICE, '123'), (OFFICE, '../secret'), (OFFICE, True), (OFFICE, None)):
            opener = FakeOpener()
            with self.subTest(office=office, school=school), self.assertRaises(NeisMealsError):
                fetch_meals(office, school, DAY, api_key=KEY, opener=opener)
            self.assertEqual(opener.calls, [])

    def test_missing_sample_or_invalid_keys_fail_before_network(self):
        for key in (None, '', ' ', 'sample', 'SAMPLE', 'sample key', ' sample_key ', 'bad\nkey', '한글', 123):
            opener = FakeOpener()
            with self.subTest(key=key), self.assertRaises(NeisMealsError):
                fetch_meals(OFFICE, SCHOOL, DAY, api_key=key, opener=opener)
            self.assertEqual(opener.calls, [])

    def test_no_published_data_is_empty_without_inventing_school_or_meals(self):
        for payload in ({'RESULT': {'CODE': 'INFO-200'}}, page([]), page([], code='INFO-200')):
            with self.subTest(payload=payload):
                result = fetch(FakeOpener(payload))
                self.assertEqual(result['rows'], [])
                self.assertEqual(result['school_name'], '')
                self.assertEqual(result['date'], DAY)

    def test_api_errors_never_become_empty_or_echo_message(self):
        for code in ('ERROR-290', 'ERROR-337', 'INFO-300', 'ERROR-300', 'ERROR-500', 'INFO-100', KEY):
            with self.subTest(code=code), self.assertRaises(NeisMealsError) as error:
                fetch(FakeOpener({'RESULT': {'CODE': code, 'MESSAGE': ENDPOINT + '?KEY=' + KEY}}))
            self.assertNotIn(KEY, str(error.exception))
            self.assertNotIn('https://', str(error.exception))

    def test_error_in_head_and_conflicting_top_level_result_fail(self):
        conflicting = page([meal()])
        conflicting['RESULT'] = {'CODE': 'INFO-200'}
        for payload in (page([], code='ERROR-290'), conflicting, page([meal()], code='INFO-200')):
            with self.subTest(payload=payload), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(payload))

    def test_other_office_school_and_date_fail(self):
        for field, value in [('ATPT_OFCDC_SC_CODE', 'Z98'), ('SD_SCHUL_CODE', '0000002'),
                             ('MLSV_YMD', '20260927'), ('MLSV_YMD', '2026-09-28'), ('MLSV_YMD', None)]:
            with self.subTest(field=field, value=value), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(page([meal(**{field: value})])))

    def test_school_name_must_be_consistent_and_present(self):
        for payload in (page([meal('1'), meal('2', SCHUL_NM='다른 가상 학교')]), page([meal(SCHUL_NM=None)])):
            with self.subTest(payload=payload), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(payload))

    def test_meal_codes_and_names_must_agree(self):
        for change in ({'MMEAL_SC_CODE': '4'}, {'MMEAL_SC_CODE': 2}, {'MMEAL_SC_CODE': '02'},
                       {'MMEAL_SC_NM': '조식'}, {'MMEAL_SC_NM': None}):
            with self.subTest(change=change), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(page([meal(**change)])))

    def test_same_meal_code_is_rejected_even_if_duplicate_is_identical(self):
        for second in (meal(), meal(DDISH_NM='서로 다른 메뉴')):
            with self.subTest(menu=second['DDISH_NM']), self.assertRaisesRegex(NeisMealsError, '중복'):
                fetch(FakeOpener(page([meal(), second])))

    def test_all_br_variants_entities_and_linebreaks_are_normalized(self):
        raw = meal(DDISH_NM='  밥&nbsp;&amp;&nbsp;국 (1.2.5.6.13.)<BR>김치 (9.)<br />우유 (2.)&lt;br/&gt;과일\r\n\t후식  ',
                   ORPLC_INFO='쌀&nbsp;:&nbsp;국내산', NTR_INFO='단백질(g)&nbsp;:&nbsp;20&#10;지방(g) : 8')
        row = fetch(FakeOpener(page([raw])))['rows'][0]
        self.assertEqual(row['menu'], '밥 & 국 (1.2.5.6.13.)\n김치 (9.)\n우유 (2.)\n과일\n후식')
        self.assertEqual(row['origin'], '쌀 : 국내산')
        self.assertEqual(row['nutrition'], '단백질(g) : 20\n지방(g) : 8')

    def test_tags_urls_scripts_and_comments_are_not_output_as_markup(self):
        raw = meal(DDISH_NM='<p>밥 (5.)</p><div><b>국 (1.)</b></div>'
                   '<script>secret_script()</script><style>secret_style</style>'
                   '<a href="https://example.invalid/?KEY=hidden">김치 (9.)</a><!-- hidden_comment -->')
        menu = fetch(FakeOpener(page([raw])))['rows'][0]['menu']
        self.assertEqual(menu, '밥 (5.)\n국 (1.)\n김치 (9.)')
        self.assertNotIn('hidden', menu)
        self.assertNotIn('secret', menu)
        self.assertNotIn('<', menu)

    def test_less_than_and_ampersand_as_text_are_kept(self):
        menu = fetch(FakeOpener(page([meal(DDISH_NM='당 &lt; 3g &amp; 가상 메뉴 (1.)')])))['rows'][0]['menu']
        self.assertEqual(menu, '당 < 3g & 가상 메뉴 (1.)')

    def test_blank_or_malformed_menu_cannot_be_valid_meal(self):
        for value in (None, '', ' <br> ', '<script>not a menu</script>', [], '밥\x00국', '가' * 50001):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(page([meal(DDISH_NM=value)])))

    def test_optional_fields_always_return_strings_without_invented_values(self):
        raw = meal(CAL_INFO=None, ORPLC_INFO=None, NTR_INFO=None)
        raw.pop('LOAD_DTM')
        row = fetch(FakeOpener(page([raw])))['rows'][0]
        for key in ('calories', 'origin', 'nutrition', 'updated_at'):
            self.assertEqual(row[key], '')

    def test_wrong_optional_types_fail_and_extra_fields_are_not_propagated(self):
        with self.assertRaises(NeisMealsError):
            fetch(FakeOpener(page([meal(NTR_INFO=['not text'])])))
        raw = meal(API_URL='https://example.invalid/?KEY=' + KEY)
        self.assertNotIn(KEY, json.dumps(fetch(FakeOpener(page([raw])))))

    def test_pagination_complete_when_page_size_is_smaller_than_meal_count(self):
        opener = FakeOpener(page([meal('3'), meal('1')], 3), page([meal('2')], 3))
        with patch('teacher_planner.neis_meals.PAGE_SIZE', 2):
            snapshot = fetch(opener)
        self.assertEqual([row['meal_code'] for row in snapshot['rows']], ['1', '2', '3'])
        self.assertEqual([parse_qs(urlsplit(call[0].full_url).query)['pIndex'] for call in opener.calls], [['1'], ['2']])

    def test_changed_count_empty_later_page_repeated_page_and_short_page_fail(self):
        openers = [FakeOpener(page([meal('1')], 3)),
                   FakeOpener(page([meal('1'), meal('2')], 3), page([meal('3')], 2)),
                   FakeOpener(page([meal('1'), meal('2')], 3), {'RESULT': {'CODE': 'INFO-200'}}),
                   FakeOpener(page([meal('1'), meal('2')], 3), page([], 3)),
                   FakeOpener(page([meal('1'), meal('2')], 3), page([meal('1')], 3))]
        for opener in openers:
            with self.subTest(pages=len(opener.pages)), patch('teacher_planner.neis_meals.PAGE_SIZE', 2), \
                    self.assertRaises(NeisMealsError):
                fetch(opener)

    def test_exact_full_page_never_requests_empty_followup(self):
        opener = FakeOpener(page([meal('1'), meal('2')]))
        with patch('teacher_planner.neis_meals.PAGE_SIZE', 2):
            self.assertEqual(len(fetch(opener)['rows']), 2)
        self.assertEqual(len(opener.calls), 1)

    def test_malformed_page_count_and_structure_fail(self):
        payloads = [None, [], {}, {'mealServiceDietInfo': {}}, {'mealServiceDietInfo': []},
                    page([], -1), page([], 4), page([], True), page([], 'bad'), page([], 1),
                    page([meal()], 0), page([None])]
        missing_result = page([])
        missing_result['mealServiceDietInfo'][0]['head'].pop()
        duplicate_head = page([])
        duplicate_head['mealServiceDietInfo'].append(copy.deepcopy(duplicate_head['mealServiceDietInfo'][0]))
        payloads.extend([missing_result, duplicate_head])
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(payload))

    def test_invalid_json_duplicate_json_keys_and_large_body_rejected(self):
        for body in (b'<html>error</html>', b'{"broken":', b'\xff', b'x' * (MAX_BYTES + 1),
                     b'{"RESULT":{"CODE":"ERROR-290","CODE":"INFO-200"}}'):
            opener = FakeOpener(body)
            with self.subTest(size=len(body)), self.assertRaises(NeisMealsError):
                fetch(opener)
            self.assertEqual(opener.responses[0].read_limits, [MAX_BYTES + 1])

    def test_network_failures_do_not_leak_key_query_or_chained_exception(self):
        secret_url = ENDPOINT + '?KEY=' + KEY
        for error in (URLError(secret_url), HTTPError(secret_url, 401, KEY, {}, None),
                      TimeoutError(KEY), RuntimeError(secret_url), NeisMealsError(secret_url)):
            with self.subTest(kind=type(error).__name__), self.assertRaises(NeisMealsError) as caught:
                fetch(FakeOpener(error))
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn('https://', str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_wrong_response_host_scheme_path_and_status_fail(self):
        urls = ['http://open.neis.go.kr/hub/mealServiceDietInfo', 'https://evil.invalid/hub/mealServiceDietInfo',
                'https://open.neis.go.kr/hub/SchoolSchedule',
                'https://user:password@open.neis.go.kr/hub/mealServiceDietInfo']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(NeisMealsError):
                fetch(FakeOpener(Response(page([]), url)))
        with self.assertRaises(NeisMealsError):
            fetch(FakeOpener(Response(page([]), ENDPOINT, status=302)))

    def test_default_network_client_refuses_redirects(self):
        opener = FakeOpener(page([]))
        with patch('teacher_planner.neis_meals.build_opener') as builder:
            builder.return_value.open = opener
            fetch_meals(OFFICE, SCHOOL, DAY, api_key=KEY)
        builder.assert_called_once_with(_NoRedirect)
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))


if __name__ == '__main__':
    unittest.main()
