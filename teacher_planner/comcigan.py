"""Optional, read-only adapter for the public Comcigan teacher web client.

Protocol facts are discovered from the public client; no JavaScript is executed.
The service has no stable, documented API. Unknown shapes fail before Notion writes.
"""
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

BASE = 'http://comci.net:4082'
CLIENT_URL = BASE + '/th'
MAX_BYTES = 8 * 1024 * 1024


class ComciganError(ValueError):
    pass


@dataclass(frozen=True)
class Protocol:
    endpoint: str
    prefix: str
    teachers: str
    subjects: str
    current: str
    original: str
    rooms: str
    updated: str


def discover(html):
    """Extract role-specific names, without pinning rotating numeric keys."""
    def one(pattern):
        found = set(re.findall(pattern, html))
        if len(found) != 1:
            raise ComciganError('컴시간 공개 클라이언트 구조가 바뀌었습니다. 어댑터를 점검하세요.')
        return found.pop()

    return Protocol(
        endpoint=one(r"['\"]\./([A-Za-z0-9_]+_T\?)['\"]\s*\+\s*btoa\("),
        prefix=one(r"sc_data\(\s*['\"]([0-9]+_)['\"]"),
        teachers=one(r'자료\.(자료\d+)\s*\[i\]'),
        subjects=one(r'과목명\s*=\s*Q과목명\(자료\.(자료\d+)\[sb\]\)'),
        current=one(r'교사자료\s*=\s*Q자료\(자료\.(자료\d+)\[교사\]\[요일\]\[교시\]\)'),
        original=one(r'원자료\s*=\s*Q자료\(자료\.(자료\d+)\[학년\]\[반\]\[요일\]\[교시\]\)'),
        rooms=one(r'var\s+m3\s*=\s*자료\.(자료\d+)\[학년\]\[반\]\[요일\]\[교시\]'),
        updated=one(r'da1\s*=\s*H시간표\.(자료\d+)'),
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(url):
    # Only the fixed public service is contacted; never pass Notion credentials.
    if not url.startswith(BASE + '/'):
        raise ComciganError('지원하지 않는 컴시간 주소입니다.')
    try:
        request = Request(url, headers={'User-Agent': 'notion-teacher-planner/0.2', 'Cache-Control': 'no-cache'})
        with build_opener(_NoRedirect).open(request, timeout=20) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ComciganError('컴시간 응답 크기가 예상 범위를 넘었습니다.')
        return body
    except (HTTPError, URLError, TimeoutError, OSError, HTTPException):
        # urllib errors contain the URL, including the encoded school code.
        raise ComciganError('컴시간 웹 조회에 실패했습니다. 네트워크와 서비스 상태를 확인하세요.') from None


def _decode(raw):
    if isinstance(raw, str):
        return raw
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ComciganError('컴시간 응답 문자 인코딩을 읽을 수 없습니다.')


def _json(raw):
    try:
        payload, _ = json.JSONDecoder().raw_decode(_decode(raw).lstrip())
    except (ValueError, TypeError):
        raise ComciganError('컴시간이 시간표 JSON을 반환하지 않았습니다. 학교 코드를 확인하세요.') from None
    if not isinstance(payload, dict) or not payload:
        raise ComciganError('컴시간에 조회 가능한 학교 자료가 없습니다.')
    return payload


def _integer(value, label, minimum=0, maximum=10**12):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'\d+', str(value)):
        raise ComciganError(f'컴시간 {label} 값이 올바른 정수가 아닙니다.')
    number = int(value)
    if not minimum <= number <= maximum:
        raise ComciganError(f'컴시간 {label} 값이 지원 범위를 벗어났습니다.')
    return number


def _identifiers(school_code, teacher_id):
    school_code = str(school_code)
    if not re.fullmatch(r'[0-9]{1,20}', school_code):
        raise ComciganError('학교 코드는 숫자로 입력하세요.')
    return school_code, _integer(teacher_id, '교사 번호', 1, 10000)


def _date(value, label):
    # Source dates can use hyphens or dots; never infer a missing year.
    match = re.fullmatch(r'(\d{4})[-.](\d{1,2})[-.](\d{1,2})\.?', str(value).strip())
    try:
        if match:
            return date(*(int(x) for x in match.groups()))
    except ValueError:
        pass
    raise ComciganError(f'컴시간 {label} 날짜를 읽을 수 없습니다.')


def _monday(requested=None):
    if requested is None:
        requested = datetime.now(ZoneInfo('Asia/Seoul')).date()
    if isinstance(requested, str):
        requested = _date(requested, '요청')
    if not isinstance(requested, date) or isinstance(requested, datetime):
        raise ComciganError('요청 날짜는 YYYY-MM-DD 형식이어야 합니다.')
    return requested - timedelta(days=requested.weekday())


def _at(array, index, label):
    try:
        if isinstance(array, list):
            return array[index]
        if isinstance(array, dict):
            return array[str(index)]
    except (IndexError, KeyError):
        pass
    raise ComciganError(f'컴시간 {label} 구조가 없거나 불완전합니다.')


def _slots(array):
    if not isinstance(array, (list, dict)):
        raise ComciganError('컴시간 교시 배열이 없습니다.')
    if isinstance(array, list):
        if not array or len(array) > 9:
            raise ComciganError('컴시간 교시 배열이 지원 범위를 벗어났습니다.')
        return {i: array[i] for i in range(1, len(array))}
    if any(not re.fullmatch(r'[0-8]', str(k)) for k in array):
        raise ComciganError('컴시간 교시 번호가 지원 범위를 벗어났습니다.')
    return {int(k): v for k, v in array.items() if str(k) != '0'}


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ComciganError(f'컴시간 {label} 값이 비어 있거나 너무 깁니다.')
    return value.strip()


def parse_week(payload, protocol, school_code, teacher_id, requested):
    school_code, teacher_id = _identifiers(school_code, teacher_id)
    start = _date(payload.get('시작일'), '시작일')
    if start != _monday(requested):
        raise ComciganError('컴시간 응답이 요청한 주와 다릅니다. 기존 시간표는 변경하지 않습니다.')
    restriction = payload.get('열람제한일')
    if restriction not in (None, '', 0, '0'):
        limit = _date(restriction, '열람 제한')
        if limit.year >= 2014 and limit <= start + timedelta(days=4):
            raise ComciganError('요청한 주에 컴시간 열람 제한 날짜가 포함됩니다. 공개 후 다시 조회하세요.')
    teacher_count = _integer(payload.get('교사수'), '교사 수', 1, 10000)
    if teacher_id > teacher_count:
        raise ComciganError('학교 자료에 해당 교사 번호가 없습니다.')
    name = _text(_at(payload.get(protocol.teachers), teacher_id, '교사 목록'), '교사 이름')
    current = _at(payload.get(protocol.current), teacher_id, '교사 시간표')
    divisor = _integer(payload.get('분리', 100), '분리', 100, 1000)
    if divisor not in (100, 1000):
        raise ComciganError('지원하지 않는 컴시간 인코딩입니다.')
    counts = {g: _integer(_at(payload.get('학급수'), g, '학급 수'), '학급 수', 0, 99) for g in range(1, 4)}
    original = {}
    # Reconstruct only this teacher's original slots from the class grid.
    for grade, count in counts.items():
        if not count:
            continue
        grade_grid = _at(payload.get(protocol.original), grade, '원시간표 학년')
        for cls in range(1, count + 1):
            class_grid = _at(grade_grid, cls, '원시간표 학급')
            for day in range(1, 6):
                for period, raw in _slots(_at(class_grid, day, '원시간표 요일')).items():
                    number = _integer(raw, '원시간표 셀')
                    if not number:
                        continue
                    teacher = number // divisor if divisor == 100 else number % divisor
                    subject = number % divisor if divisor == 100 else number // divisor
                    if teacher == teacher_id:
                        key = day, period
                        if key in original:
                            raise ComciganError('동일 교시 원수업이 중복됩니다. 합반 시간표는 직접 확인하세요.')
                        original[key] = grade, cls, subject

    rows, omitted = [], 0
    for day in range(1, 6):
        slots = _slots(_at(current, day, '교사 시간표 요일'))
        omitted += 8 - len(slots)
        for period, raw in sorted(slots.items()):
            marked = isinstance(raw, str) and raw.startswith('>')
            number = _integer(raw[1:] if marked else raw, '교사 시간표 셀')
            old = original.get((day, period))
            if number:
                class_code, subject = number % 1000, number // 1000
                lesson = class_code // 100, class_code % 100, subject
                changed = marked if payload.get('변경알림') == 1 else lesson != old
                status = '변경' if changed else '예정'
            elif old:
                lesson, status = old, '휴강'
            else:
                continue
            grade, cls, subject = lesson
            if grade not in counts or not 1 <= cls <= counts[grade] or subject % divisor == 0:
                raise ComciganError('교사 시간표의 학급 또는 교과 번호가 올바르지 않습니다.')
            subject_name = _text(_at(payload.get(protocol.subjects), subject % divisor, '교과 목록'), '교과 이름')
            room = ''
            if payload.get('강의실') == 1 and status != '휴강':
                room_grid = payload.get(protocol.rooms)
                for idx in (grade, cls, day):
                    room_grid = _at(room_grid, idx, '교실')
                # The public client treats a missing final room cell as blank.
                # It is optional metadata, not a missing lesson/cancellation.
                if isinstance(room_grid, list):
                    room_grid = room_grid[period] if period < len(room_grid) else None
                elif isinstance(room_grid, dict):
                    room_grid = room_grid.get(str(period))
                else:
                    raise ComciganError('컴시간 교실 교시 배열이 없습니다.')
                if room_grid is not None:
                    if not isinstance(room_grid, str) or len(room_grid) > 200:
                        raise ComciganError('교실 값이 올바르지 않습니다.')
                    prefix, sep, label = room_grid.partition('_')
                    if sep and prefix.isdigit() and int(prefix) > 0:
                        room = label.strip()
            group = subject // divisor
            rows.append({'date': (start + timedelta(days=day - 1)).isoformat(), 'period': period,
                         'class_name': f'{grade}학년 {cls}반', 'class_code': f'{grade}-{cls}',
                         'subject': subject_name, 'group': chr(64 + group) if 1 <= group <= 26 else '',
                         'room': room, 'status': status})
    # The full school payload is never persisted or returned to CLI callers.
    return {'provider': 'comcigan', 'school_code': school_code, 'teacher_id': teacher_id,
            'teacher_name': name, 'week_start': start.isoformat(),
            'source_updated': str(payload.get(protocol.updated, ''))[:200],
            'fetched_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
            'omitted_slots': omitted, 'rows': rows}


def fetch_week(school_code, teacher_id, requested=None, fetch=None):
    school_code, teacher_id = _identifiers(school_code, teacher_id)
    target = _monday(requested)
    fetch = fetch or _fetch
    protocol = discover(_decode(fetch(CLIENT_URL)))

    def get(selector):
        query = base64.b64encode(f'{protocol.prefix}{school_code}_0_{selector}'.encode('ascii')).decode('ascii')
        return _json(fetch(BASE + '/' + protocol.endpoint + query))

    payload = get(1)
    if _date(payload.get('시작일'), '시작일') == target:
        return parse_week(payload, protocol, school_code, teacher_id, target)
    # Only request date choices actually advertised by the service, never guess
    # unpublished weeks. Check returned dates instead of trusting display labels.
    options = payload.get('일자자료')
    if not isinstance(options, list) or len(options) > 12:
        raise ComciganError('컴시간에서 조회 가능한 주 목록을 확인할 수 없습니다.')
    selectors = []
    for option in options:
        if not isinstance(option, list) or len(option) < 2:
            raise ComciganError('컴시간 주 선택 목록 형식이 바뀌었습니다.')
        selector = _integer(option[0], '주 선택값', 0, 10000)
        if selector != 1 and selector not in selectors:
            selectors.append(selector)
    for selector in selectors:
        candidate = get(selector)
        if _date(candidate.get('시작일'), '시작일') == target:
            return parse_week(candidate, protocol, school_code, teacher_id, target)
    raise ComciganError('컴시간이 요청한 주를 제공하지 않습니다. 다른 주의 시간표를 대신 사용하지 않습니다.')


def normalize(snapshot, c, mapping=None, period_times=None):
    """Convert a validated snapshot into dated rows for the Notion sync engine."""
    mapping = {} if mapping is None else mapping
    if not isinstance(mapping, dict) or set(mapping) - {'classes', 'subjects'}:
        raise ComciganError('매핑에는 classes와 subjects만 사용할 수 있습니다.')
    for group in ('classes', 'subjects'):
        value = mapping.get(group, {})
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in value.items()):
            raise ComciganError('학급·교과 매핑은 문자열 이름끼리 연결하세요.')
    times = {}
    if period_times is not None:
        if not isinstance(period_times, dict):
            raise ComciganError('교시 시각 설정은 교시 번호별 객체여야 합니다.')
        for period, span in period_times.items():
            p = _integer(period, '교시', 1, 8)
            if not isinstance(span, dict) or set(span) != {'start', 'end'}:
                raise ComciganError('각 교시에 start와 end 시각을 지정하세요.')
            if any(not isinstance(v, str) or not re.fullmatch(r'\d{2}:\d{2}', v) for v in span.values()):
                raise ComciganError('교시 시각은 HH:MM 형식이어야 합니다.')
            try:
                a, b = time.fromisoformat(span['start']), time.fromisoformat(span['end'])
            except ValueError:
                raise ComciganError('교시 시각을 확인하세요.') from None
            if a >= b:
                raise ComciganError('교시 종료는 시작보다 늦어야 합니다.')
            times[p] = a, b
    school, teacher = _identifiers(snapshot['school_code'], snapshot['teacher_id'])
    week = _monday(snapshot['week_start'])
    classes = {x['name'] for x in c['classes']}
    zone = ZoneInfo(c['timezone'])
    if c['timezone'] != 'Asia/Seoul':
        raise ComciganError('컴시간 수업 시각의 시간대는 Asia/Seoul로 설정하세요.')
    rows, keys, intervals = [], set(), {}
    for r in snapshot['rows']:
        day = _date(r['date'], '수업일')
        if not week <= day <= week + timedelta(days=4):
            raise ComciganError('시간표에 요청한 주 밖의 날짜가 있습니다.')
        if not date(c['academic_year'], 3, 1) <= day < date(c['academic_year'] + 1, 3, 1):
            raise ComciganError('컴시간 수업일이 설정 학년도 밖에 있습니다.')
        period = _integer(r['period'], '교시', 1, 8)
        cls = mapping.get('classes', {}).get(r.get('class_code'), r['class_name'])
        subject = mapping.get('subjects', {}).get(r['subject'], r['subject'])
        if cls not in classes or subject not in c['subjects']:
            raise ComciganError(f'설정에 없는 수업: {cls} / {subject}. --mapping 또는 설치 전 담당 반·교과 설정을 확인하세요.')
        if r['status'] not in ('예정', '변경', '휴강'):
            raise ComciganError('컴시간 수업 상태가 올바르지 않습니다.')
        key = f'{day}|{period}'
        if key in keys:
            raise ComciganError('동일 날짜·교시 수업이 중복됩니다.')
        keys.add(key)
        date_range = {'start': day.isoformat()}
        if period_times is not None:
            if period not in times:
                raise ComciganError(f'{period}교시 start/end 시각이 빠져 있습니다.')
            a, b = times[period]
            if r['status'] != '휴강':
                if any(a < old_b and b > old_a for old_a, old_b in intervals.get(day, [])):
                    raise ComciganError('교시 시각이 겹칩니다.')
                intervals.setdefault(day, []).append((a, b))
            date_range = {'start': datetime.combine(day, a, zone).isoformat(), 'end': datetime.combine(day, b, zone).isoformat()}
        external_id = 'tt:comci:' + hashlib.sha256(f'{school}|{teacher}|{key}'.encode()).hexdigest()[:24]
        rows.append({**r, 'period': period, 'class_name': cls, 'subject': subject,
                     'date_range': date_range, 'external_id': external_id})
    return rows
