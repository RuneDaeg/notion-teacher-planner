"""Import NEIS school events into the existing agenda without replacing teacher notes.

NEIS does not expose a stable event identifier: a renamed or rescheduled source
entry has a new external ID. Missing entries are retained for manual review.
"""
import copy
import json
from datetime import date, timedelta

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


def group_events(rows):
    """Group validated daily rows by title/course/day-night and calendar adjacency.

    Daily identities and every source detail remain available to MCP callers in
    ``source_rows``. The first daily ID is an initial anchor only; installed
    groups reuse their saved anchor when the source extends their date range.
    A Friday and Monday are separate unless the source includes the weekend.
    """
    ordered = sorted(rows, key=lambda row: (row['title'], row['course'], row['day_night'], row['date']))
    groups = []
    for row in ordered:
        key = (row['title'], row['course'], row['day_night'])
        previous = groups[-1] if groups else None
        if (previous is not None
                and key == (previous['title'], previous['course'], previous['day_night'])
                and _day(row['date']) == _day(previous['source_rows'][-1]['date']) + timedelta(days=1)):
            previous['source_rows'].append(copy.deepcopy(row))
            previous['source_ids'].append(row['external_id'])
            previous['end'] = row['date']
        else:
            groups.append({**copy.deepcopy(row), 'end': None,
                           'source_rows': [copy.deepcopy(row)], 'source_ids': [row['external_id']]})
    return sorted(groups, key=lambda row: (row['date'], row['title'], row['course'], row['day_night']))


def grouped_schedule(snapshot, c):
    """Validate the snapshot before exposing grouped periods to REST or MCP."""
    return group_events(_validated_rows(snapshot, c))


def _daily_text(row):
    grades = ', '.join(str(grade) for grade in row['grades']) + '학년' if row['grades'] else '제공된 학년 정보 없음'
    text = ('날짜: ' + row['date'] + '\n'
            + '대상: ' + grades + '\n'
            + '학교 과정: ' + (row['course'] or '제공된 정보 없음') + '\n'
            + '주야 과정: ' + (row['day_night'] or '제공된 정보 없음') + '\n'
            + '수업 구분: ' + (row.get('day_type') or '제공된 정보 없음') + '\n'
            + '내용: ' + (row['description'] or '제공된 내용 없음') + '\n')
    if row.get('updated_at'):
        text += '원본 수정일: ' + row['updated_at'] + '\n'
    return text


def source_info(snapshot, row):
    """Plain source text plus a fixed public documentation link; never an API key URL."""
    text = ('NEIS 학사일정\n'
            + '학교: ' + row['school_name'] + '\n'
            + '교육청 / 학교 코드: ' + snapshot['office_code'] + ' / ' + snapshot['school_code'] + '\n'
            + '행사: ' + row['title'] + '\n')
    daily = row.get('source_rows', [row])
    if len(daily) > 1:
        text += ('기간: ' + row['date'] + ' ~ ' + row['end'] + ' (종료일 포함)\n'
                 + '연속된 일별 행사 ' + str(len(daily)) + '건을 한 일정으로 표시합니다.\n\n')
    text += '\n'.join(_daily_text(source) for source in daily)
    rich_text = [{'type': 'text', 'text': {'content': text[i:i + 2000]}}
                 for i in range(0, len(text), 2000)]
    archives = row.get('archived_pages', [])
    if archives:
        rich_text.append({'type': 'text', 'text': {'content': '\n통합 전 일별 페이지 (보관함):\n'}})
        for archive in archives:
            rich_text.append({'type': 'text', 'text': {'content': archive['date'] + ' 원본 페이지\n',
                              'link': {'url': 'https://www.notion.so/' + archive['id'].replace('-', '')}}})
    rich_text.append({'type': 'text', 'text': {'content': '출처: '}})
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


def _period_plan(row, saved_groups, old, snapshot, merge_existing):
    members = set(row['source_ids'])
    for saved in saved_groups.values():
        same_event = ((saved['title'], saved['course'], saved['day_night'])
                      == (row['title'], row['course'], row['day_night']))
        touches = (_day(row['date']) <= _day(saved['end']) + timedelta(days=1)
                   and _day(row['end'] or row['date']) >= _day(saved['start']) - timedelta(days=1))
        if same_event and touches and not snapshot['start'] <= saved['start'] <= saved['end'] <= snapshot['end']:
            raise ValueError('부분 조회가 기존 NEIS 기간 행사와 겹치거나 이어집니다. 전체 학년도를 다시 조회하세요.')
    matches = [(key, value) for key, value in saved_groups.items()
               if members.intersection(value['source_ids'])]
    if len(matches) > 1:
        raise ValueError('기존 NEIS 기간 행사 두 개가 하나로 이어집니다. 기존 기록을 확인한 뒤 수동 통합하세요.')
    if matches:
        key, previous = matches[0]
        if previous.get('page_id') and (key not in old or not _same_id(previous['page_id'], old[key]['id'])):
            raise ValueError('이전에 가져온 NEIS 일정이 사라졌거나 ID가 다릅니다. 보관·휴지통을 확인하세요.')
        if (previous['title'], previous['course'], previous['day_night']) != (row['title'], row['course'], row['day_night']):
            raise ValueError('NEIS 기간 행사 식별 정보가 설치 상태와 다릅니다.')
        if not snapshot['start'] <= previous['start'] <= previous['end'] <= snapshot['end']:
            raise ValueError('부분 조회가 기존 NEIS 기간 행사를 자릅니다. 전체 학년도를 다시 조회하세요.')
        if not set(previous['source_ids']).issubset(members):
            raise ValueError('기존 NEIS 기간 행사가 축소·분리되었습니다. 원본과 기존 기록을 확인하세요.')
    else:
        previous = None
        # Reuse a real legacy page even if a newly discovered earlier day sorts
        # first. Once chosen, the anchor is persisted independently of bounds.
        existing_keys = [source['external_id'] for source in row['source_rows'] if source['external_id'] in old]
        active_keys = [key for key in existing_keys
                       if not old[key].get('properties', {}).get('보관', {}).get('checkbox')]
        key = next(iter(active_keys or existing_keys), row['external_id'])
    known_archives = {entry['external_id']: entry for entry in (previous or {}).get('archived_pages', [])}
    archives = []
    for source in row['source_rows']:
        external_id = source['external_id']
        if external_id == key:
            continue
        page = old.get(external_id)
        known = known_archives.get(external_id)
        if known and (page is None or not _same_id(known['id'], page['id'])):
            raise ValueError('통합 전 NEIS 보관 페이지가 사라졌거나 ID가 다릅니다.')
        if page is not None:
            if not known and not merge_existing:
                raise ValueError('통합할 기존 NEIS 일별 페이지가 있습니다. 검토 후 --merge-existing --apply를 사용하세요.')
            archives.append({'external_id': external_id, 'id': page['id'], 'date': source['date']})
    if archives and any(entry['external_id'] not in known_archives for entry in archives):
        year = snapshot['academic_year']
        if (snapshot['start'], snapshot['end']) != (date(year, 3, 1).isoformat(),
                                                   (date(year + 1, 3, 1) - timedelta(days=1)).isoformat()):
            raise ValueError('기존 NEIS 일별 페이지 통합은 전체 학년도 조회로 진행하세요.')
    desired = {'source_ids': row['source_ids'], 'start': row['date'], 'end': row['end'] or row['date'],
               'title': row['title'], 'course': row['course'], 'day_night': row['day_night'],
               'archived_pages': archives, 'page_id': old.get(key, {}).get('id')}
    return key, previous, desired


def changes(client, c, state, snapshot, *, merge_existing=False):
    """Preflight all source IDs, pages and owned info blocks before any Notion write."""
    if state.get('pending'):
        raise ValueError('완료 여부가 불확실한 생성 작업이 있습니다. docs/RECOVERY.md를 확인하세요.')
    rows = grouped_schedule(snapshot, c)
    ds = state['databases']['agenda']['data_source_id']
    definitions = next(d['properties'] for d in selected(c)[0] if d['key'] == 'agenda')
    queried = client.pages(ds, {'property': '외부 ID', 'rich_text': {'starts_with': 'neis:'}})
    old = unique_index((p for p in queried if text_property(p, '외부 ID').startswith('neis:')), '외부 ID')
    objects, ops = state['objects'], []
    saved_groups = state.get('neis_groups', {})
    claimed = set()
    for row in rows:
        external_id, previous_group, group = _period_plan(row, saved_groups, old, snapshot, merge_existing)
        if external_id in claimed:
            raise ValueError('기존 NEIS 기간 행사가 여러 기간으로 분리되었습니다. 원본을 확인하세요.')
        claimed.add(external_id)
        row['external_id'] = external_id
        row['archived_pages'] = group['archived_pages']
        existing = old.get(external_id)
        saved = objects.get(page_key(external_id))
        if saved and (existing is None or not _same_id(saved['id'], existing['id'])):
            raise ValueError('이전에 가져온 NEIS 일정이 사라졌거나 ID가 다릅니다. 보관·휴지통을 확인하세요.')
        info = objects.get(info_key(external_id))
        if existing is None and info:
            raise ValueError('NEIS 원본 안내만 남아 있습니다. 기존 일정과 설치 상태를 확인하세요.')
        schedule = {'start': row['date']}
        if row['end'] is not None:
            schedule['end'] = row['end']
        props = {'이름': row['title'], '일정': schedule}
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
        retired = []
        for archive in group['archived_pages']:
            archived_page = old[archive['external_id']]
            _check_page(archived_page, ds, c['academic_year'])
            saved_archive = objects.get(page_key(archive['external_id']))
            if saved_archive and not _same_id(saved_archive['id'], archive['id']):
                raise ValueError('통합 전 NEIS 일별 페이지의 설치 ID가 다릅니다.')
            retire_properties = values({'보관': True}, definitions)
            retired.append({**archive, 'changed_properties': _changed_properties(archived_page, retire_properties)})
        ops.append({'source': 'agenda', 'external_id': external_id, 'row': row,
                    'existing': existing['id'] if existing else None,
                    'properties': desired, 'changed_properties': _changed_properties(existing, desired),
                    'info_existing': info['id'] if info else None, 'info_block': info_block,
                    'info_changed': info_changed, 'retired': retired,
                    'previous_group': copy.deepcopy(previous_group), 'group': group})
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
        if j.data.get('neis_groups', {}).get(key) != op['previous_group']:
            raise ValueError('NEIS 기간 행사 상태가 바뀌었습니다. 조회부터 다시 실행하세요.')
    # Persist membership and merge authorization before reversible writes. If a
    # response is lost, a fresh diff can reuse the same page and finish archiving
    # without making new pages or guessing which old records were consolidated.
    if ops:
        groups = j.data.setdefault('neis_groups', {})
        for op in ops:
            groups[op['external_id']] = copy.deepcopy(op['group'])
        j.save()
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
        if j.data['neis_groups'][op['external_id']].get('page_id') != identifier:
            j.data['neis_groups'][op['external_id']]['page_id'] = identifier
            j.save()
        if op['info_existing']:
            if op['info_changed']:
                client.request('PATCH', '/blocks/' + op['info_existing'],
                               {'callout': {'rich_text': copy.deepcopy(op['info_block']['callout']['rich_text'])}})
                written = True
        else:
            writer.append(info_key(op['external_id']), identifier, op['info_block'], position={'type': 'start'})
            written = True
        # Only a notebook property changes: original pages, notes and relations
        # stay intact and are linked from the surviving event's owned callout.
        for retired in op['retired']:
            if retired['changed_properties']:
                client.request('PATCH', '/pages/' + retired['id'], {'properties': retired['changed_properties']})
                written = True
        changed += int(written)
    return changed
