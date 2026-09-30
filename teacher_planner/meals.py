"""Display dated public meals in one owned home block, never in task databases."""
import json
import re
from datetime import date, datetime

from .blocks import _same_id
from .install import Journal, fingerprint
from .school_calendar import _rich_value

MEAL_KEY = 'extras:meals'
SOURCE_URL = ('https://open.neis.go.kr/portal/data/service/selectServicePage.do'
              '?infId=OPEN17320190722180924242823&infSeq=2')


def checked_block(client, identifier, root):
    block = client.request('GET', '/blocks/' + identifier)
    parent = block.get('parent', {})
    if (block.get('type') != 'callout' or block.get('archived') or block.get('in_trash')
            or parent.get('type') not in ('page_id', 'block_id')
            or not _same_id(parent.get(parent.get('type')), root)):
        raise ValueError('급식 블록의 위치·유형·휴지통 상태가 바뀌었습니다. 기존 블록을 확인하세요.')
    return block


def meal_block(snapshot, academic_year):
    if not isinstance(snapshot, dict) or snapshot.get('source') != 'neis-meals':
        raise ValueError('NEIS 급식 응답을 확인하세요.')
    office, school = snapshot.get('office_code'), snapshot.get('school_code')
    if (not isinstance(office, str) or not re.fullmatch(r'[A-Z][0-9]{2}', office)
            or not isinstance(school, str) or not re.fullmatch(r'[0-9]{7}', school)):
        raise ValueError('급식 교육청·표준학교코드를 확인하세요.')
    raw_date = snapshot.get('date')
    if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
        raise ValueError('급식 날짜를 확인하세요.')
    day = date.fromisoformat(raw_date)
    if not date(academic_year, 3, 1) <= day < date(academic_year + 1, 3, 1):
        raise ValueError('급식 날짜가 수첩 학년도 밖입니다.')
    fetched = snapshot.get('fetched_at')
    if not isinstance(fetched, str) or len(fetched) > 60:
        raise ValueError('급식 조회 시각을 확인하세요.')
    stamp = datetime.fromisoformat(fetched.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('급식 조회 시각에 시간대가 필요합니다.')
    from zoneinfo import ZoneInfo
    checked = stamp.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST')

    def text(value):
        if not isinstance(value, str) or len(value) > 20000:
            raise ValueError('급식 내용 형식·길이를 확인하세요.')
        return value

    name = text(snapshot.get('school_name'))
    rows = snapshot.get('rows')
    if not isinstance(rows, list) or len(rows) > 3:
        raise ValueError('급식 행 배열을 확인하세요.')
    if rows and not name.strip():
        raise ValueError('급식 학교명이 없습니다.')
    lines = [f'오늘의 중식 · {day.isoformat()} ({"월화수목금토일"[day.weekday()]})',
             name or f'교육청 {office} · 학교 {school}', f'조회: {checked}', '']
    codes = set()
    lunch = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('급식 행 형식을 확인하세요.')
        code = row.get('meal_code')
        if code not in ('1', '2', '3') or code in codes:
            raise ValueError('급식 구분이 없거나 중복됩니다.')
        codes.add(code)
        meal_name = {'1': '조식', '2': '중식', '3': '석식'}[code]
        if row.get('meal_name') != meal_name or not text(row.get('menu')).strip():
            raise ValueError('급식 구분·메뉴를 확인하세요.')
        # Validate the complete snapshot even though home displays only lunch.
        details = {field: text(row.get(field, '')) for field in ('calories', 'origin', 'nutrition')}
        if code == '2':
            lunch = (row['menu'], details['calories'])
    if lunch is not None:
        menu, calories = lunch
        lines.append(menu)
        if calories:
            lines.append('열량: ' + calories)
        lines.append('')
    else:
        lines += ['해당 날짜에 공개된 중식 정보가 없습니다.', '미등록·미제공일 수 있으므로 중식 미실시로 단정하지 않습니다.', '']
    lines += ['메뉴의 알레르기 번호는 원문 표시입니다. 번호가 없다고 알레르기 성분이 없다는 뜻은 아닙니다.',
              '마지막 조회 결과입니다. 표시 날짜를 확인하세요.']
    body = '\n'.join(lines)
    rich_text = [{'type': 'text', 'text': {'content': body[i:i + 2000]}} for i in range(0, len(body), 2000)]
    rich_text.append({'type': 'text', 'text': {'content': '\nNEIS 급식식단정보', 'link': {'url': SOURCE_URL}}})
    block = {'object': 'block', 'type': 'callout', 'callout': {
        'rich_text': rich_text, 'icon': {'type': 'emoji', 'emoji': '🍱'}, 'color': 'yellow_background'}}
    if len(rich_text) > 100 or len(json.dumps(block).encode()) > 400000:
        raise ValueError('급식 내용이 Notion 요청 크기 제한을 넘습니다.')
    return block


def update_meals(client, state_path, snapshot):
    j = Journal(state_path, client)
    j.ready()
    year = j.data['config']['academic_year']
    desired = meal_block(snapshot, year)  # Validate the complete payload before writes.
    owned = j.data['objects'].get(MEAL_KEY)
    if not owned:
        raise ValueError('홈 급식 칸이 없습니다. setup-extras --apply를 먼저 실행하세요.')
    root = j.data['objects']['root']['id']
    actual = checked_block(client, owned['id'], root)
    binding = fingerprint({'office_code': snapshot['office_code'], 'school_code': snapshot['school_code'],
                           'academic_year': year})
    for field in ('neis_source', 'neis_meals_source'):
        if j.data.get(field) and j.data[field] != binding:
            raise ValueError('기존 NEIS 연결과 급식 학교·교육청·학년도가 다릅니다.')
    if snapshot['rows'] and not j.data.get('neis_meals_source'):
        j.data['neis_meals_source'] = binding
        j.save()
    changed = _rich_value(actual['callout'].get('rich_text')) != _rich_value(desired['callout']['rich_text'])
    if changed:
        client.request('PATCH', '/blocks/' + owned['id'], {'callout': {'rich_text': desired['callout']['rich_text']}})
    j.data['neis_meals_last_checked_at'] = snapshot['fetched_at']
    j.data['neis_meals_date'] = snapshot['date']
    j.save()
    return int(changed)
