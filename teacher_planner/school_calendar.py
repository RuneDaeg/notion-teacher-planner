"""Import NEIS school events into the existing agenda without replacing teacher notes.

NEIS does not expose a stable event identifier: a renamed or rescheduled source
entry has a new external ID. Missing entries are retained for manual review.
"""
import copy
import json
from datetime import date

from .blocks import BlockJournal, _same_id
from .install import Journal
from .model import selected, values
from .timetable import _changed_properties, text_property, unique_index

SOURCE_URL = ('https://open.neis.go.kr/portal/data/service/selectServicePage.do'
              '?infId=OPEN17220190722175038389180&infSeq=2')


def page_key(external_id):
    return 'import:agenda:' + external_id


def info_key(external_id):
    return 'school-calendar:info:' + external_id


def _text(value, name, *, blank=True, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        raise ValueError('NEIS 일정의 ' + name + ' 형식을 확인하세요.')
    return value


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('NEIS 일정 날짜는 YYYY-MM-DD여야 합니다.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('NEIS 일정 날짜를 확인하세요.') from None


def _validated_rows(snapshot, c):
    from .neis import event_id

    if not isinstance(snapshot, dict) or snapshot.get('source') != 'neis':
        raise ValueError('NEIS 학사일정 응답을 확인하세요.')
    if type(snapshot.get('academic_year')) is not int or snapshot['academic_year'] != c['academic_year']:
        raise ValueError('NEIS 일정과 설치 학년도가 다릅니다.')
    office = _text(snapshot.get('office_code'), '교육청 코드', blank=False)
    school = _text(snapshot.get('school_code'), '학교 코드', blank=False)
    _text(snapshot.get('school_name'), '학교명')
    start, end = _day(snapshot.get('start')), _day(snapshot.get('end'))
    if not date(c['academic_year'], 3, 1) <= start <= end < date(c['academic_year'] + 1, 3, 1):
        raise ValueError('NEIS 조회 범위가 설치 학년도 밖입니다.')
    if not isinstance(snapshot.get('rows'), list):
        raise ValueError('NEIS 일정 행 배열을 확인하세요.')
    rows, seen = [], set()
    for raw in snapshot['rows']:
        if not isinstance(raw, dict):
            raise ValueError('NEIS 일정 행 형식을 확인하세요.')
        row = {key: _text(raw.get(key), key, blank=key not in ('title', 'school_name'),
                         maximum=100000 if key == 'description' else 2000)
               for key in ('title', 'description', 'school_name', 'course', 'day_night')}
        if row['school_name'] != snapshot['school_name']:
            raise ValueError('NEIS 일정 행의 학교명이 응답과 다릅니다.')
        day = _day(raw.get('date'))
        if not start <= day <= end:
            raise ValueError('NEIS 일정 날짜가 조회 범위 밖입니다.')
        grades = raw.get('grades')
        if not isinstance(grades, list) or any(type(grade) is not int or not 1 <= grade <= 12 for grade in grades):
            raise ValueError('NEIS 대상 학년 배열을 확인하세요.')
        row.update(date=day.isoformat(), grades=sorted(set(grades)))
        row['updated_at'] = _text(raw.get('updated_at', ''), '원본 수정일')
        row['day_type'] = _text(raw.get('day_type', ''), '수업 구분')
        expected = event_id(office, school, row['date'], row['title'], row['day_night'], row['course'])
        if raw.get('external_id') != expected or expected in seen:
            raise ValueError('NEIS 일정 외부 ID가 일치하지 않거나 중복됩니다.')
        row['external_id'] = expected
        seen.add(expected)
        rows.append(row)
    return rows


def source_info(snapshot, row):
    """Plain source text plus a fixed public documentation link; never an API key URL."""
    grades = ', '.join(str(grade) for grade in row['grades']) + '학년' if row['grades'] else '제공된 학년 정보 없음'
    text = ('NEIS 학사일정\n'
            + '학교: ' + row['school_name'] + '\n'
            + '교육청 / 학교 코드: ' + snapshot['office_code'] + ' / ' + snapshot['school_code'] + '\n'
            + '행사: ' + row['title'] + '\n'
            + '날짜: ' + row['date'] + '\n'
            + '대상: ' + grades + '\n'
            + '학교 과정: ' + (row['course'] or '제공된 정보 없음') + '\n'
            + '주야 과정: ' + (row['day_night'] or '제공된 정보 없음') + '\n'
            + '수업 구분: ' + (row['day_type'] or '제공된 정보 없음') + '\n'
            + '내용: ' + (row['description'] or '제공된 내용 없음') + '\n')
    if row['updated_at']:
        text += '원본 수정일: ' + row['updated_at'] + '\n'
    text += '출처: '
    rich_text = [{'type': 'text', 'text': {'content': text[i:i + 2000]}}
                 for i in range(0, len(text), 2000)]
    rich_text.append({'type': 'text', 'text': {'content': 'NEIS 학사일정 Open API', 'link': {'url': SOURCE_URL}}})
    block = {'object': 'block', 'type': 'callout', 'callout': {
        'rich_text': rich_text, 'icon': {'type': 'emoji', 'emoji': '🏫'}, 'color': 'gray_background'}}
    # Client serializes non-ASCII characters as escapes. Check that actual wire
    # representation, leaving room below Notion's 500 KB request limit.
    if len(rich_text) > 100 or len(json.dumps({'children': [block]}).encode('utf-8')) > 400000:
        raise ValueError('NEIS 원본 안내가 Notion 요청 크기 제한을 넘습니다. 내용을 확인하세요.')
    return block


def _rich_value(items):
    """Ignore response metadata and formatting, including equivalent text splits."""
    parts = []
    for item in items or []:
        text = item.get('plain_text', item.get('text', {}).get('content', ''))
        url = (item.get('text', {}).get('link') or {}).get('url')
        if parts and parts[-1][1] == url:
            parts[-1] = (parts[-1][0] + text, url)
        else:
            parts.append((text, url))
    return parts


def _check_page(page, parent, year):
    if (page.get('archived') or page.get('in_trash')
            or not _same_id(page.get('parent', {}).get('data_source_id'), parent)
            or page.get('properties', {}).get('학년도', {}).get('number') != year):
        raise ValueError('기존 NEIS 일정의 부모·학년도·휴지통 상태를 확인하세요.')


def _check_info(block, parent):
    actual_parent = block.get('parent', {})
    if (block.get('type') != 'callout' or block.get('archived') or block.get('in_trash')
            or actual_parent.get('type') not in ('page_id', 'block_id')
            or not _same_id(actual_parent.get(actual_parent.get('type')), parent)):
        raise ValueError('NEIS 원본 안내 블록의 부모·유형·휴지통 상태가 달라 중단합니다.')


def changes(client, c, state, snapshot):
    """Preflight all source IDs, pages and owned info blocks before any Notion write."""
    if state.get('pending'):
        raise ValueError('완료 여부가 불확실한 생성 작업이 있습니다. docs/RECOVERY.md를 확인하세요.')
    rows = _validated_rows(snapshot, c)
    ds = state['databases']['agenda']['data_source_id']
    definitions = next(d['properties'] for d in selected(c)[0] if d['key'] == 'agenda')
    queried = client.pages(ds, {'property': '외부 ID', 'rich_text': {'starts_with': 'neis:'}})
    old = unique_index((p for p in queried if text_property(p, '외부 ID').startswith('neis:')), '외부 ID')
    objects, ops = state['objects'], []
    for row in rows:
        external_id = row['external_id']
        existing = old.get(external_id)
        saved = objects.get(page_key(external_id))
        if saved and (existing is None or not _same_id(saved['id'], existing['id'])):
            raise ValueError('이전에 가져온 NEIS 일정이 사라졌거나 ID가 다릅니다. 보관·휴지통을 확인하세요.')
        info = objects.get(info_key(external_id))
        if existing is None and info:
            raise ValueError('NEIS 원본 안내만 남아 있습니다. 기존 일정과 설치 상태를 확인하세요.')
        props = {'이름': row['title'], '일정': {'start': row['date']}}
        if existing:
            _check_page(existing, ds, c['academic_year'])
        else:
            props.update(학년도=c['academic_year'], 종류='행사', 상태='예정', 보관=False)
            props.update({'업무 분류': '행정', '외부 ID': external_id})
        desired = values(props, definitions)
        info_block = source_info(snapshot, row)
        info_changed = True
        if info:
            actual = client.request('GET', '/blocks/' + info['id'])
            _check_info(actual, existing['id'])
            info_changed = _rich_value(actual.get('callout', {}).get('rich_text')) != _rich_value(info_block['callout']['rich_text'])
        ops.append({'source': 'agenda', 'external_id': external_id, 'row': row,
                    'existing': existing['id'] if existing else None,
                    'properties': desired, 'changed_properties': _changed_properties(existing, desired),
                    'info_existing': info['id'] if info else None, 'info_block': info_block,
                    'info_changed': info_changed})
    return ops


def apply_changes(client, state_path, ops):
    """Apply prepared event changes, counting each changed event once.

    Page and block creation use the shared pending journal. Existing block PATCHes
    target a known owned ID and are reconciled by a fresh diff after a failure.
    """
    j = Journal(state_path, client)
    j.ready()
    seen = set()
    for op in ops:
        key = op['external_id']
        if key in seen or op.get('source') != 'agenda':
            raise ValueError('NEIS 반영 목록의 대상 또는 외부 ID가 중복됩니다.')
        seen.add(key)
        saved = j.data['objects'].get(page_key(key))
        if saved and not _same_id(saved['id'], op.get('existing')):
            raise ValueError('NEIS 일정 상태가 바뀌었습니다. 조회·차이 확인부터 다시 실행하세요.')
        info = j.data['objects'].get(info_key(key))
        if (info is None) != (op.get('info_existing') is None) or (info and not _same_id(info['id'], op['info_existing'])):
            raise ValueError('NEIS 원본 안내 상태가 바뀌었습니다. 조회부터 다시 실행하세요.')
    writer = BlockJournal(j)
    changed = 0
    for op in ops:
        written = False
        identifier = op['existing']
        if identifier:
            if op['changed_properties']:
                client.request('PATCH', '/pages/' + identifier, {'properties': op['changed_properties']})
                written = True
        else:
            obj = j.create(page_key(op['external_id']), '/pages', {
                'parent': {'type': 'data_source_id', 'data_source_id': j.data['databases']['agenda']['data_source_id']},
                'properties': op['properties']})
            identifier = obj['id']
            written = True
        if op['info_existing']:
            if op['info_changed']:
                client.request('PATCH', '/blocks/' + op['info_existing'],
                               {'callout': {'rich_text': copy.deepcopy(op['info_block']['callout']['rich_text'])}})
                written = True
        else:
            writer.append(info_key(op['external_id']), identifier, op['info_block'], position={'type': 'start'})
            written = True
        changed += int(written)
    return changed
