"""Read one day's public NEIS meals; never infer allergy safety from the menu."""
import json
import re
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ENDPOINT = 'https://open.neis.go.kr/hub/mealServiceDietInfo'
SOURCE_URL = ('https://open.neis.go.kr/portal/data/service/selectServicePage.do'
              '?infId=OPEN17320190722180924242823&infSeq=2')
PAGE_SIZE = 1000
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 20
MEAL_NAMES = {'1': '조식', '2': '중식', '3': '석식'}


class NeisMealsError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden.append(tag)
        elif not self.hidden and tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def _text(value, label, *, required=False, maximum=50_000):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or len(value) > maximum:
        raise NeisMealsError(f'NEIS 급식 {label} 형식 또는 길이가 올바르지 않습니다.')
    parser = _PlainText()
    try:
        # NEIS uses both literal <br/> and entity-escaped strings. Output is
        # ordinary text; attributes/URLs are never carried into Notion links.
        parser.feed(unescape(value))
        parser.close()
    except Exception:
        raise NeisMealsError(f'NEIS 급식 {label} 내용을 읽을 수 없습니다.') from None
    plain = ''.join(parser.parts).replace('\r\n', '\n').replace('\r', '\n')
    if any(ord(char) < 32 and char not in ('\n', '\t') for char in plain):
        raise NeisMealsError(f'NEIS 급식 {label} 내용에 지원하지 않는 제어 문자가 있습니다.')
    lines = [re.sub(r'[^\S\n]+', ' ', line).strip() for line in plain.split('\n')]
    result = '\n'.join(line for line in lines if line)
    if required and not result:
        raise NeisMealsError(f'NEIS 급식 {label} 내용이 비어 있습니다.')
    return result


def _identifiers(office_code, school_code):
    if not isinstance(office_code, str) or not re.fullmatch(r'[A-Z][0-9]{2}', office_code):
        raise NeisMealsError('NEIS 교육청 코드는 영문 대문자 1개와 숫자 2개여야 합니다.')
    if (isinstance(school_code, bool) or not isinstance(school_code, (str, int))
            or not re.fullmatch(r'[0-9]{7}', str(school_code))):
        raise NeisMealsError('NEIS 학교 코드는 행정표준코드 숫자 7자리여야 합니다.')
    return office_code, str(school_code)


def _requested(value):
    if value is None:
        return datetime.now(ZoneInfo('Asia/Seoul')).date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise NeisMealsError('급식 조회 날짜는 유효한 YYYY-MM-DD 형식이어야 합니다.')


def _key(api_key):
    if not isinstance(api_key, str):
        raise NeisMealsError('급식 조회에는 환경 변수 NEIS_API_KEY의 발급받은 인증키가 필요합니다.')
    value = api_key.strip()
    if (not value or value.lower() in {'sample', 'sample key', 'sample_key'}
            or len(value) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise NeisMealsError('발급받은 NEIS_API_KEY가 필요합니다. 인증키 없는 샘플은 사용할 수 없습니다.')
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _read(url, opener):
    # Type=json selects JSON without triggering NEIS's application/json Accept
    # negotiation failure. The response must still pass JSON validation below.
    try:
        request = Request(url, headers={'User-Agent': 'notion-teacher-planner/0.6',
                                        'Accept': '*/*', 'Cache-Control': 'no-cache'})
        open_request = opener if opener is not None else build_opener(_NoRedirect).open
        with open_request(request, timeout=TIMEOUT) as response:
            final = urlsplit(response.geturl())
            if (final.scheme != 'https' or final.netloc != 'open.neis.go.kr'
                    or final.path != '/hub/mealServiceDietInfo' or final.fragment
                    or response.status != 200):
                raise ValueError('Unexpected response')
            body = response.read(MAX_BYTES + 1)
    except Exception:
        # Raw errors may contain the whole query URL and its authentication key.
        raise NeisMealsError('NEIS 급식 조회에 실패했습니다. 네트워크와 서비스 상태를 확인하세요.') from None
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise NeisMealsError('NEIS 급식 응답 크기 또는 형식이 지원 범위를 벗어났습니다.')
    try:
        payload = json.loads(body.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise NeisMealsError('NEIS가 올바른 급식 JSON을 반환하지 않았습니다.') from None
    if not isinstance(payload, dict):
        raise NeisMealsError('NEIS 급식 응답 구조가 올바르지 않습니다.')
    return payload


def _result(result):
    if not isinstance(result, dict) or not isinstance(result.get('CODE'), str):
        raise NeisMealsError('NEIS 급식 처리 결과가 없거나 올바르지 않습니다.')
    code = result['CODE']
    if code in ('INFO-000', 'INFO-200'):
        return code
    messages = {
        'ERROR-290': 'NEIS 인증키가 유효하지 않습니다.',
        'ERROR-337': 'NEIS 일일 조회 한도를 넘었습니다. 나중에 다시 조회하세요.',
        'INFO-300': 'NEIS 인증키 사용이 제한되었습니다.',
        'ERROR-300': 'NEIS 필수 조회 인자가 누락되었습니다.',
    }
    raise NeisMealsError(messages.get(code, 'NEIS 서비스가 급식 조회 오류를 반환했습니다.'))


def _page(payload, index):
    if 'RESULT' in payload:
        code = _result(payload['RESULT'])
        if code == 'INFO-200' and index == 1 and 'mealServiceDietInfo' not in payload:
            return 0, []
        raise NeisMealsError('NEIS 급식 조회가 끝나기 전에 빈 결과나 다른 응답을 반환했습니다.')
    parts = payload.get('mealServiceDietInfo')
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise NeisMealsError('NEIS 급식 페이지 구조가 올바르지 않습니다.')
    heads = [part['head'] for part in parts if 'head' in part]
    rows = [part['row'] for part in parts if 'row' in part]
    if len(heads) != 1 or len(rows) != 1 or not isinstance(heads[0], list) or not isinstance(rows[0], list):
        raise NeisMealsError('NEIS 급식 페이지의 머리말 또는 행이 누락되었습니다.')
    if any(not isinstance(item, dict) for item in heads[0]):
        raise NeisMealsError('NEIS 급식 머리말 구조가 올바르지 않습니다.')
    counts = [item['list_total_count'] for item in heads[0] if 'list_total_count' in item]
    results = [item['RESULT'] for item in heads[0] if 'RESULT' in item]
    if len(counts) != 1 or len(results) != 1:
        raise NeisMealsError('NEIS 급식 전체 건수 또는 처리 결과가 누락되었습니다.')
    total = counts[0]
    if (isinstance(total, bool) or not isinstance(total, (str, int))
            or not re.fullmatch(r'[0-3]', str(total))):
        raise NeisMealsError('한 학교·날짜의 급식은 중복 없는 조식·중식·석식이어야 합니다.')
    total = int(total)
    code = _result(results[0])
    if code == 'INFO-200' and (index != 1 or total != 0 or rows[0]):
        raise NeisMealsError('NEIS 급식 페이지가 전체 건수와 다른 빈 결과를 반환했습니다.')
    return total, rows[0]


def _row(raw, office_code, school_code, requested):
    if not isinstance(raw, dict):
        raise NeisMealsError('NEIS 급식 행이 올바른 객체가 아닙니다.')
    if _identifiers(raw.get('ATPT_OFCDC_SC_CODE'), raw.get('SD_SCHUL_CODE')) != (office_code, school_code):
        raise NeisMealsError('NEIS 급식 응답의 교육청·학교가 요청과 다릅니다.')
    if raw.get('MLSV_YMD') != requested.isoformat().replace('-', ''):
        raise NeisMealsError('NEIS 급식 응답 날짜가 요청한 날짜와 다릅니다.')
    meal_code = raw.get('MMEAL_SC_CODE')
    if not isinstance(meal_code, str) or meal_code not in MEAL_NAMES:
        raise NeisMealsError('NEIS 급식 식사코드는 조식·중식·석식 중 하나여야 합니다.')
    meal_name = _text(raw.get('MMEAL_SC_NM'), '식사명', required=True, maximum=100)
    if meal_name != MEAL_NAMES[meal_code]:
        raise NeisMealsError('NEIS 급식 식사명과 식사코드가 일치하지 않습니다.')
    school_name = _text(raw.get('SCHUL_NM'), '학교명', required=True, maximum=200)
    row = {'meal_code': meal_code, 'meal_name': meal_name,
           'menu': _text(raw.get('DDISH_NM'), '메뉴', required=True),
           'calories': _text(raw.get('CAL_INFO'), '열량', maximum=1000),
           'origin': _text(raw.get('ORPLC_INFO'), '원산지'),
           'nutrition': _text(raw.get('NTR_INFO'), '영양정보'),
           'updated_at': _text(raw.get('LOAD_DTM'), '수정일자', maximum=100)}
    return school_name, row


def fetch_meals(office_code, school_code, requested=None, *, api_key, opener=None):
    """Fetch every meal for exactly one date, defaulting to today in Seoul.

    ``opener(request, timeout=...)`` may be injected for offline tests. A valid
    empty response means no published data, not confirmation that no meal exists.
    Menu allergen numbers are kept verbatim; the adapter makes no safety claims.
    """
    office_code, school_code = _identifiers(office_code, school_code)
    requested = _requested(requested)
    key = _key(api_key)
    query = {'KEY': key, 'Type': 'json', 'pSize': PAGE_SIZE,
             'ATPT_OFCDC_SC_CODE': office_code, 'SD_SCHUL_CODE': school_code,
             'MLSV_YMD': requested.isoformat().replace('-', '')}
    rows, seen, school_name, expected, index = [], set(), '', None, 1
    while True:
        url = ENDPOINT + '?' + urlencode({**query, 'pIndex': index})
        total, source_rows = _page(_read(url, opener), index)
        if expected is None:
            expected = total
        elif expected != total:
            raise NeisMealsError('NEIS 급식 조회 중 전체 건수가 바뀌었습니다. 다시 조회하세요.')
        if len(source_rows) != min(PAGE_SIZE, expected - len(rows)):
            raise NeisMealsError('NEIS 급식 페이지 행 수가 전체 건수와 다릅니다. 일부 식사만 표시하지 않습니다.')
        for source in source_rows:
            name, row = _row(source, office_code, school_code, requested)
            if school_name and school_name != name:
                raise NeisMealsError('NEIS 급식 응답에 서로 다른 학교명이 포함되어 있습니다.')
            school_name = name
            if row['meal_code'] in seen:
                raise NeisMealsError('NEIS 급식 식사코드가 중복됩니다. 원본 자료를 확인하세요.')
            seen.add(row['meal_code'])
            rows.append(row)
        if len(rows) == expected:
            break
        index += 1
    rows.sort(key=lambda row: row['meal_code'])
    return {'source': 'neis-meals', 'source_url': SOURCE_URL,
            'office_code': office_code, 'school_code': school_code, 'school_name': school_name,
            'date': requested.isoformat(),
            'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'rows': rows}
