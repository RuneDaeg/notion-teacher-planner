"""Read-only NEIS SchoolSchedule adapter; credentials never enter snapshots.

The public service requires an issued key for complete pagination. A keyless
sample response contains at most five rows and is not an annual calendar.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = 'https://open.neis.go.kr/hub/SchoolSchedule'
SOURCE_URL = ('https://open.neis.go.kr/portal/data/service/selectServicePage.do'
              '?infId=OPEN17220190722175038389180&infSeq=2')
PAGE_SIZE = 1000
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
TIMEOUT = 20
GRADE_FIELDS = ('ONE_GRADE_EVENT_YN', 'TW_GRADE_EVENT_YN',
                'THREE_GRADE_EVENT_YN', 'FR_GRADE_EVENT_YN',
                'FIV_GRADE_EVENT_YN', 'SIX_GRADE_EVENT_YN')


class NeisError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, label, minimum, maximum):
    if (isinstance(value, bool) or not isinstance(value, (str, int))
            or not re.fullmatch(r'[0-9]+', str(value))):
        raise NeisError(f'NEIS {label} 값은 정수여야 합니다.')
    if len(str(value)) > len(str(maximum)):
        raise NeisError(f'NEIS {label} 값이 지원 범위를 벗어났습니다.')
    number = int(value)
    if not minimum <= number <= maximum:
        raise NeisError(f'NEIS {label} 값이 지원 범위를 벗어났습니다.')
    return number


def _identifiers(office_code, school_code, academic_year):
    if not isinstance(office_code, str) or not re.fullmatch(r'[A-Z][0-9]{2}', office_code):
        raise NeisError('NEIS 교육청 코드는 영문 대문자 1개와 숫자 2개여야 합니다.')
    if (isinstance(school_code, bool) or not isinstance(school_code, (str, int))
            or not re.fullmatch(r'[0-9]{7}', str(school_code))):
        raise NeisError('NEIS 학교 코드는 행정표준코드 숫자 7자리여야 합니다.')
    return office_code, str(school_code), _integer(academic_year, '학년도', 1900, 9998)


def _date(value, label):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise NeisError(f'NEIS {label} 날짜는 유효한 YYYY-MM-DD 형식이어야 합니다.')


def _range(academic_year, start, end):
    first = date(academic_year, 3, 1)
    last = date(academic_year + 1, 3, 1) - timedelta(days=1)
    start = first if start is None else _date(start, '시작')
    end = last if end is None else _date(end, '종료')
    if not first <= start <= end <= last:
        raise NeisError('NEIS 조회 기간은 해당 학년도 3월 1일~다음 해 2월 말 안이어야 합니다.')
    return start, end


def _key(api_key):
    if not isinstance(api_key, str):
        raise NeisError('전체 학사일정 조회에는 환경 변수 NEIS_API_KEY의 발급받은 인증키가 필요합니다.')
    value = api_key.strip()
    if (not value or value.lower() in {'sample', 'sample key', 'sample_key'}
            or len(value) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise NeisError('발급받은 NEIS_API_KEY가 필요합니다. 인증키 없는 5건 샘플은 사용할 수 없습니다.')
    return value


def _read(url, opener):
    # An injected opener has urllib's (request, timeout=...) -> response contract.
    # Production always uses a fixed HTTPS endpoint and refuses every redirect.
    # NEIS can return HTTP 500 for Accept: application/json; Type=json selects
    # the response format, which is still validated strictly below.
    try:
        request = Request(url, headers={'User-Agent': 'notion-teacher-planner/0.5',
                                        'Accept': '*/*',
                                        'Cache-Control': 'no-cache'})
        open_request = opener if opener is not None else build_opener(_NoRedirect).open
        with open_request(request, timeout=TIMEOUT) as response:
            final = urlsplit(response.geturl())
            if (final.scheme != 'https' or final.netloc != 'open.neis.go.kr'
                    or final.path != '/hub/SchoolSchedule' or final.fragment):
                raise NeisError('NEIS 응답 주소가 고정된 공개 API 주소와 다릅니다.')
            if response.status != 200:
                raise NeisError('NEIS 서버가 정상 응답을 반환하지 않았습니다.')
            body = response.read(MAX_BYTES + 1)
    except Exception:
        # Exceptions can include the entire URL (and KEY); do not relay them.
        raise NeisError('NEIS 학사일정 조회에 실패했습니다. 네트워크와 서비스 상태를 확인하세요.') from None
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise NeisError('NEIS 응답 크기 또는 형식이 지원 범위를 벗어났습니다.')
    try:
        payload = json.loads(body.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise NeisError('NEIS가 올바른 JSON 학사일정을 반환하지 않았습니다.') from None
    if not isinstance(payload, dict):
        raise NeisError('NEIS 학사일정 응답 구조가 올바르지 않습니다.')
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _result(value):
    if not isinstance(value, dict) or not isinstance(value.get('CODE'), str):
        raise NeisError('NEIS 처리 결과가 없거나 올바르지 않습니다.')
    code = value['CODE']
    if code in ('INFO-000', 'INFO-200'):
        return code
    messages = {
        'ERROR-290': 'NEIS 인증키가 유효하지 않습니다.',
        'ERROR-337': 'NEIS 일일 조회 한도를 넘었습니다. 나중에 다시 조회하세요.',
        'INFO-300': 'NEIS 인증키 사용이 제한되었습니다.',
        'ERROR-300': 'NEIS 필수 조회 인자가 누락되었습니다.',
    }
    raise NeisError(messages.get(code, 'NEIS 서비스가 학사일정 조회 오류를 반환했습니다.'))


def _page(payload, index):
    if 'RESULT' in payload:
        code = _result(payload['RESULT'])
        if code == 'INFO-200' and index == 1 and 'SchoolSchedule' not in payload:
            return 0, []
        raise NeisError('NEIS 조회가 완료되기 전에 데이터가 없거나 응답 구조가 달라졌습니다.')
    parts = payload.get('SchoolSchedule')
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise NeisError('NEIS 학사일정 페이지 구조가 올바르지 않습니다.')
    heads = [part['head'] for part in parts if 'head' in part]
    rows = [part['row'] for part in parts if 'row' in part]
    if len(heads) != 1 or len(rows) != 1 or not isinstance(heads[0], list) or not isinstance(rows[0], list):
        raise NeisError('NEIS 학사일정 페이지의 머리말 또는 행이 누락되었습니다.')
    head = heads[0]
    if any(not isinstance(item, dict) for item in head):
        raise NeisError('NEIS 페이지 머리말 구조가 올바르지 않습니다.')
    totals = [item['list_total_count'] for item in head if 'list_total_count' in item]
    results = [item['RESULT'] for item in head if 'RESULT' in item]
    if len(totals) != 1 or len(results) != 1:
        raise NeisError('NEIS 페이지의 전체 건수 또는 처리 결과가 누락되었습니다.')
    total = _integer(totals[0], '전체 건수', 0, MAX_ROWS)
    code = _result(results[0])
    if code == 'INFO-200' and (index != 1 or total != 0 or rows[0]):
        raise NeisError('NEIS 페이지가 전체 건수와 다른 빈 결과를 반환했습니다.')
    return total, rows[0]


def _text(value, label, *, required=False, maximum=1000):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or len(value) > maximum:
        raise NeisError(f'NEIS {label} 내용의 형식 또는 길이가 올바르지 않습니다.')
    value = value.strip()
    if required and not value:
        raise NeisError(f'NEIS {label} 내용이 비어 있습니다.')
    return value


def event_id(office_code, school_code, event_date, title, day_night, course):
    """Stable source identity; mutable descriptions, grades and dates of load excluded."""
    identity = [office_code, school_code, event_date, title, day_night, course]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'neis:' + hashlib.sha256(encoded).hexdigest()


def _row(raw, office_code, school_code, academic_year, start, end):
    if not isinstance(raw, dict):
        raise NeisError('NEIS 일정 행이 올바른 객체가 아닙니다.')
    identity = _identifiers(raw.get('ATPT_OFCDC_SC_CODE'), raw.get('SD_SCHUL_CODE'), raw.get('AY'))
    if identity != (office_code, school_code, academic_year):
        raise NeisError('NEIS 응답의 교육청·학교·학년도가 요청과 다릅니다.')
    source_date = raw.get('AA_YMD')
    if not isinstance(source_date, str) or not re.fullmatch(r'[0-9]{8}', source_date):
        raise NeisError('NEIS 학사일자는 연월일 8자리여야 합니다.')
    event_date = _date(source_date[:4] + '-' + source_date[4:6] + '-' + source_date[6:], '학사일')
    if not start <= event_date <= end:
        raise NeisError('NEIS 응답에 요청 기간 밖의 학사일정이 포함되어 있습니다.')
    title = _text(raw.get('EVENT_NM'), '행사명', required=True)
    school_name = _text(raw.get('SCHUL_NM'), '학교명', required=True, maximum=200)
    if 'DGHT_CRSE_SC_NM' not in raw or 'SCHUL_CRSE_SC_NM' not in raw:
        raise NeisError('NEIS 일정의 학교과정·주야과정이 누락되었습니다.')
    day_night = _text(raw['DGHT_CRSE_SC_NM'], '주야과정', maximum=100)
    course = _text(raw['SCHUL_CRSE_SC_NM'], '학교과정', maximum=100)
    grades = []
    for grade, field in enumerate(GRADE_FIELDS, 1):
        if field not in raw or raw[field] not in ('Y', 'N', '*', '', None):
            raise NeisError('NEIS 학년별 행사 여부가 누락되었거나 올바르지 않습니다.')
        if raw[field] == 'Y':
            grades.append(grade)
    result = {
        'external_id': event_id(office_code, school_code, event_date.isoformat(), title, day_night, course),
        'date': event_date.isoformat(), 'title': title,
        'description': _text(raw.get('EVENT_CNTNT'), '행사내용', maximum=100_000),
        'grades': grades, 'school_name': school_name, 'course': course, 'day_night': day_night,
        'day_type': _text(raw.get('SBTR_DD_SC_NM'), '수업공제일명', maximum=100),
    }
    updated_at = _text(raw.get('LOAD_DTM'), '수정일자', maximum=100)
    if updated_at:
        result['updated_at'] = updated_at
    return result


def fetch_schedule(office_code, school_code, academic_year, *, api_key,
                   start=None, end=None, opener=None):
    """Fetch a complete requested school-year range or fail without a snapshot.

    ``opener`` is an optional urllib-compatible callable for offline tests.
    Empty results are valid; they never imply deletion of previously imported rows.
    Exact duplicate source rows within one page are counted, then emitted once.
    Repeated IDs across pages or differing source rows still fail the snapshot.
    ``grades`` lists only explicitly reported Y values, not an all-school guess.
    """
    office_code, school_code, academic_year = _identifiers(office_code, school_code, academic_year)
    start, end = _range(academic_year, start, end)
    key = _key(api_key)
    query = {'KEY': key, 'Type': 'json', 'pSize': PAGE_SIZE,
             'ATPT_OFCDC_SC_CODE': office_code, 'SD_SCHUL_CODE': school_code,
             'AA_FROM_YMD': start.strftime('%Y%m%d'), 'AA_TO_YMD': end.strftime('%Y%m%d')}
    rows, seen, expected, index, school_name = [], {}, None, 1, ''
    received, duplicate_count = 0, 0
    while True:
        url = ENDPOINT + '?' + urlencode({**query, 'pIndex': index})
        total, page_rows = _page(_read(url, opener), index)
        if expected is None:
            expected = total
        elif expected != total:
            raise NeisError('NEIS 조회 도중 전체 건수가 바뀌었습니다. 처음부터 다시 조회하세요.')
        if len(page_rows) != min(PAGE_SIZE, expected - received):
            raise NeisError('NEIS 페이지 행 수가 전체 건수와 다릅니다. 일부 자료만 가져오지 않습니다.')
        for raw in page_rows:
            row = _row(raw, office_code, school_code, academic_year, start, end)
            if school_name and school_name != row['school_name']:
                raise NeisError('NEIS 응답에 서로 다른 학교명이 포함되어 있습니다.')
            school_name = row['school_name']
            # Compare every source field and JSON type, including unknown fields.
            raw_signature = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if row['external_id'] in seen:
                first_page, first_signature = seen[row['external_id']]
                if first_page == index and first_signature == raw_signature:
                    duplicate_count += 1
                    continue
                raise NeisError('NEIS 일정 식별값이 중복됩니다. 원본 내용 차이나 페이지 간 반복을 확인하세요.')
            seen[row['external_id']] = (index, raw_signature)
            rows.append(row)
        received += len(page_rows)
        if received == expected:
            break
        index += 1
    rows.sort(key=lambda row: (row['date'], row['title'], row['course'], row['day_night']))
    return {'source': 'neis', 'source_url': SOURCE_URL, 'office_code': office_code,
            'school_code': school_code, 'school_name': school_name,
            'academic_year': academic_year, 'start': start.isoformat(), 'end': end.isoformat(),
            'source_row_count': received, 'duplicate_row_count': duplicate_count,
            'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'rows': rows}
