"""Bounded, checkpointed NEIS updates for an explicitly enrolled Notion root.

This module neither schedules work nor reads local installation files. The host
owns OAuth tokens, durable storage, and a per-installation queue lease. ``save``
must commit synchronously or raise; a failed checkpoint prevents remote writes.
"""
import copy
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from .blocks import _same_id, children
from .client import NotionError
from .install import fingerprint
from .meals import meal_block
from .model import rich
from .school_calendar import (
    _check_info, _check_page, _period_plan, _rich_value, grouped_schedule, source_info,
)
from .timetable import _changed_properties, text_property, unique_index

SOURCE_MARKER = 'teacher-planner:neis-source:v1:'
REQUIRED_PROPERTIES = {'이름': 'title', '일정': 'date', '학년도': 'number',
                       '종류': 'select', '상태': 'select', '업무 분류': 'select',
                       '외부 ID': 'rich_text', '보관': 'checkbox'}
ID_FIELDS = ('root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id')


def _uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('클라우드 연결의 Notion ID 형식을 확인하세요.') from None


def _active(obj, kind=None):
    if (not isinstance(obj, dict) or not obj.get('id')
            or obj.get('archived') or obj.get('in_trash')
            or (kind and obj.get('object') != kind)):
        raise ValueError('클라우드 연결 대상의 유형·휴지통 상태를 확인하세요.')
    return obj


def validate_targets(client, manifest):
    """Verify readable OAuth root and every target's live ancestry, without writes.

    The OAuth host must bind this manifest to the authorization request. Merely
    knowing an ID never grants a target outside this root to the worker.
    """
    if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest['version'] != 1:
        raise ValueError('지원하지 않는 클라우드 연결 명세입니다.')
    ids = {key: _uuid(manifest.get(key)) for key in ID_FIELDS}
    if len(set(ids.values())) != len(ids):
        raise ValueError('클라우드 연결 대상 ID가 서로 중복됩니다.')
    cache = {}

    def get(kind, identifier):
        identifier = _uuid(identifier)
        key = kind, identifier
        if key not in cache:
            endpoint = {'page': 'pages', 'block': 'blocks', 'database': 'databases', 'data_source': 'data_sources'}[kind]
            obj = _active(client.request('GET', f'/{endpoint}/{identifier}'), kind)
            if not _same_id(obj['id'], identifier):
                raise ValueError('Notion 대상 ID가 응답과 다릅니다.')
            cache[key] = obj
        return cache[key]

    root = get('page', ids['root_page_id'])  # Proves this OAuth token can read it.

    def inside(obj):
        visited = set()
        for _ in range(32):
            if _same_id(obj['id'], root['id']):
                return
            key = obj.get('object'), _uuid(obj['id'])
            if key in visited:
                break
            visited.add(key)
            parent = obj.get('parent', {})
            kind = parent.get('type')
            if kind not in ('page_id', 'block_id', 'database_id', 'data_source_id'):
                break
            obj = get(kind.removesuffix('_id'), parent.get(kind))
        raise ValueError('클라우드 갱신 대상이 승인한 교무수첩 루트 밖에 있거나 부모를 확인할 수 없습니다.')

    agenda = get('data_source', ids['agenda_data_source_id'])
    inside(agenda)
    for name, kind in REQUIRED_PROPERTIES.items():
        if agenda.get('properties', {}).get(name, {}).get('type') != kind:
            raise ValueError('업무·일정 데이터 소스의 필수 속성 유형이 다릅니다: ' + name)
    blocks = {}
    for name in ('meals', 'status'):
        block = get('block', ids[name + '_block_id'])
        if block.get('type') != 'callout':
            raise ValueError('급식·자동 갱신 상태 영역은 지정된 콜아웃이어야 합니다.')
        if block.get('has_children'):
            raise ValueError('급식·자동 갱신 상태에는 하위 내용이 없는 전용 콜아웃을 지정하세요. 기존 기록은 자동 삭제하지 않습니다.')
        inside(block)
        blocks[name] = block
    return {'root': root, 'agenda': agenda, **blocks}


def _today():
    return datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()


def _checkpoint(state, save):
    # Give durable-store adapters a value snapshot, not a reference subsequently
    # mutated by this invocation. Do not catch storage failures.
    save(copy.deepcopy(state))


def _plain(block):
    return ''.join(part[0] for part in _rich_value(block.get('callout', {}).get('rich_text')))


def _marker(key):
    return SOURCE_MARKER + key


def _info(snapshot, row):
    plain = source_info(snapshot, row)
    result = copy.deepcopy(plain)
    result['callout']['rich_text'].insert(0, {'type': 'text', 'text': {'content': _marker(row['external_id']) + '\n'}})
    if len(result['callout']['rich_text']) > 100:
        raise ValueError('클라우드 학사일정 원본 안내가 Notion 크기 제한을 넘습니다.')
    return plain, result


def _find_info(client, page_id, key, *, expected_plain=None, saved_id=None):
    candidates = []
    for block in children(client, page_id):
        if block.get('type') != 'callout' or block.get('archived') or block.get('in_trash'):
            continue
        marked = _plain(block).split('\n', 1)[0] == _marker(key)
        exact_legacy = (expected_plain is not None and _rich_value(block['callout'].get('rich_text'))
                        == _rich_value(expected_plain['callout']['rich_text']))
        if marked or exact_legacy:
            _check_info(block, page_id)
            candidates.append(block)
    if len(candidates) > 1:
        raise ValueError('NEIS 원본 안내 블록이 중복되어 자동 수정을 중단합니다.')
    found = candidates[0] if candidates else None
    if saved_id and (found is None or not _same_id(found['id'], saved_id)):
        raise ValueError('클라우드 소유 원본 안내가 삭제·이동·수정되었습니다. 기존 기록을 확인하세요.')
    return found


def _properties(row, manifest, *, new=False):
    span = {'start': row['date']}
    if row.get('end'):
        span['end'] = row['end']
    props = {'이름': {'title': rich(row['title'])}, '일정': {'date': span}}
    if new:
        props.update({'학년도': {'number': manifest['academic_year']}, '종류': {'select': {'name': '행사'}},
                      '상태': {'select': {'name': '예정'}}, '업무 분류': {'select': {'name': '행정'}},
                      '외부 ID': {'rich_text': rich(row['external_id'])}, '보관': {'checkbox': False}})
    return props


def _validate_event(page, manifest):
    _active(page, 'page')
    _check_page(page, manifest['agenda_data_source_id'], manifest['academic_year'])


def _adopt_groups(rows, old, state, manifest):
    """Bootstrap only unambiguous existing periods; never hide legacy daily pages."""
    groups = copy.deepcopy(state.get('groups', {}))
    for row in rows:
        if any(set(row['source_ids']).intersection(group['source_ids']) for group in groups.values()):
            continue
        candidates = [(key, old[key]) for key in row['source_ids'] if key in old]
        if not candidates:
            continue
        for _, page in candidates:
            _validate_event(page, manifest)
        active = [(key, page) for key, page in candidates if not page['properties'].get('보관', {}).get('checkbox')]
        possible = active or candidates
        if len(possible) != 1:
            exact = [(key, page) for key, page in possible
                     if (page['properties'].get('일정', {}).get('date') or {}).get('start') == row['date']
                     and ((page['properties'].get('일정', {}).get('date') or {}).get('end')
                          or (page['properties'].get('일정', {}).get('date') or {}).get('start'))
                     == (row['end'] or row['date'])]
            # Multiple active daily pages still require a separate, reviewed
            # consolidation. A matching range never licenses archiving others.
            if active or len(exact) != 1:
                raise ValueError('같은 NEIS 기간에 기존 행사 페이지가 여러 개 있습니다. 먼저 기존 기록을 확인·통합하세요.')
            possible = exact
        key, page = possible[0]
        if text_property(page, '이름') != row['title']:
            raise ValueError('기존 NEIS 행사명과 원본이 다릅니다. 초기 연결 대상을 확인하세요.')
        span = page['properties'].get('일정', {}).get('date') or {}
        first, last = span.get('start'), span.get('end') or span.get('start')
        if (not isinstance(first, str) or not isinstance(last, str) or len(first) != 10 or len(last) != 10
                or not row['date'] <= first <= last <= (row['end'] or row['date'])):
            raise ValueError('기존 NEIS 기간이 원본보다 넓거나 날짜가 다릅니다. 축소·분리를 확인하세요.')
        members = [source['external_id'] for source in row['source_rows'] if first <= source['date'] <= last]
        if key not in members:
            raise ValueError('기존 NEIS 기간의 대표 식별값과 날짜가 다릅니다.')
        archives = []
        for external_id, original in candidates:
            if external_id == key:
                continue
            if not original['properties'].get('보관', {}).get('checkbox'):
                raise ValueError('클라우드 갱신은 기존 일별 행사 페이지를 자동 보관하지 않습니다.')
            day = next(source['date'] for source in row['source_rows'] if source['external_id'] == external_id)
            archives.append({'external_id': external_id, 'id': original['id'], 'date': day})
        groups[key] = {'source_ids': members, 'start': first, 'end': last,
                       'title': row['title'], 'course': row['course'], 'day_night': row['day_night'],
                       'page_id': page['id'], 'archived_pages': archives}
    return groups


def _recover(client, manifest, state, save):
    pending = state.get('pending')
    if not pending:
        return
    key = pending['external_id']
    if pending['kind'] == 'page':
        found = [page for page in client.pages(manifest['agenda_data_source_id'],
                 {'property': '외부 ID', 'rich_text': {'equals': key}}) if text_property(page, '외부 ID') == key]
        if len(found) != 1:
            raise ValueError('생성 결과가 불확실합니다. 외부 ID로 한 페이지를 확인할 때까지 다시 생성하지 않습니다.')
        page = found[0]
        _validate_event(page, manifest)
        if _changed_properties(page, pending['properties']):
            raise ValueError('불확실한 생성 후보의 속성이 요청과 달라 수동 확인이 필요합니다.')
        page_id = page['id']
    elif pending['kind'] == 'info':
        page_id = pending['page_id']
        page = client.request('GET', '/pages/' + page_id)
        _validate_event(page, manifest)
        if text_property(page, '외부 ID') != key:
            raise ValueError('원본 안내 생성 대상의 외부 ID가 달라졌습니다.')
    else:
        raise ValueError('알 수 없는 클라우드 생성 체크포인트입니다.')
    block = _find_info(client, page_id, key)
    if block is None or fingerprint(_rich_value(block['callout']['rich_text'])) != pending['info_hash']:
        raise ValueError('원본 안내 생성 결과를 확정할 수 없습니다. 새 블록을 추가하지 않습니다.')
    group = copy.deepcopy(pending['group'])
    group['page_id'] = page_id
    state.setdefault('groups', {})[key] = group
    state.setdefault('records', {})[key] = {'page_id': page_id, 'info_block_id': block['id'],
                                          'source_hash': pending['source_hash']}
    state.pop('pending')
    _checkpoint(state, save)


def _create(client, state, save, pending, method, path, payload):
    state['pending'] = pending
    _checkpoint(state, save)
    try:
        return client.request(method, path, payload)
    except NotionError as error:
        if error.status in (400, 401, 403, 404, 429):
            state.pop('pending')
            _checkpoint(state, save)
        raise


def _patch(client, state, save, path, payload):
    state['mutation'] = {'path': path, 'payload_hash': fingerprint(payload)}
    _checkpoint(state, save)
    client.request('PATCH', path, payload)
    state.pop('mutation')
    _checkpoint(state, save)


def sync_once(client, manifest, calendar_snapshot, meals_snapshot, state, save, max_events=8):
    """Apply at most ``max_events`` event groups and return a continuation result.

    Inputs are fetched by the host, not from local `.env` or MCP state. Successful
    empty snapshots are distinct from fetch exceptions. ``complete`` becomes true
    only after the full event pass and today's meal block have been reconciled.
    """
    if type(max_events) is not int or not 1 <= max_events <= 50:
        raise ValueError('클라우드 학사일정 처리 묶음은 1~50건이어야 합니다.')
    targets = validate_targets(client, manifest)
    if type(manifest.get('academic_year')) is not int:
        raise ValueError('수첩 학년도를 확인하세요.')
    year = manifest['academic_year']
    rows = grouped_schedule(calendar_snapshot, {'academic_year': year})
    if (calendar_snapshot['start'], calendar_snapshot['end']) != (
            date(year, 3, 1).isoformat(), (date(year + 1, 3, 1) - timedelta(days=1)).isoformat()):
        raise ValueError('클라우드 학사일정은 전체 학년도를 조회해야 합니다.')
    desired_meal = meal_block(meals_snapshot, year)
    for snapshot in (calendar_snapshot, meals_snapshot):
        if any(snapshot.get(field) != manifest.get(field) for field in ('office_code', 'school_code')):
            raise ValueError('NEIS 응답의 학교 코드가 등록한 수첩과 다릅니다.')
        if snapshot.get('school_name') and snapshot['school_name'] != manifest.get('school_name'):
            raise ValueError('NEIS 응답의 학교명이 등록한 수첩과 다릅니다.')
    if meals_snapshot['date'] != _today():
        raise ValueError('클라우드 급식은 한국 시간 오늘의 조회 결과만 반영합니다.')
    binding = fingerprint({key: manifest[key] for key in ('version', 'office_code', 'school_code',
                                                         'school_name', 'academic_year', *ID_FIELDS)})
    if state.get('binding') and state['binding'] != binding:
        raise ValueError('클라우드 상태가 다른 학교 또는 수첩 연결에 속합니다.')
    state['binding'] = binding
    _checkpoint(state, save)
    _recover(client, manifest, state, save)
    source_hash = fingerprint({'rows': calendar_snapshot['rows'], 'start': calendar_snapshot['start'],
                               'end': calendar_snapshot['end']})
    count = len(calendar_snapshot['rows'])
    summary = {'complete': False, 'source_hash': source_hash, 'daily_record_count': count,
               'event_count': len(rows), 'processed': 0, 'changed': 0, 'meals_changed': False}
    unchanged = state.get('calendar_hash') == source_hash and state.get('calendar_count') == count
    if not unchanged:
        queried = client.pages(manifest['agenda_data_source_id'],
                               {'property': '외부 ID', 'rich_text': {'starts_with': 'neis:'}})
        old = unique_index((page for page in queried if text_property(page, '외부 ID').startswith('neis:')), '외부 ID')
        saved_groups = _adopt_groups(rows, old, state, manifest)
        plan, claimed = [], set()
        for row in rows:
            key, _, group = _period_plan(row, saved_groups, old, calendar_snapshot, False)
            if key in claimed:
                raise ValueError('기존 NEIS 기간이 여러 행사로 갈라졌습니다.')
            claimed.add(key)
            for archive in group['archived_pages']:
                original = old[archive['external_id']]
                _validate_event(original, manifest)
                if not original['properties'].get('보관', {}).get('checkbox'):
                    raise ValueError('통합 전 NEIS 원본 페이지가 다시 활성화되었습니다. 자동 보관하지 않습니다.')
            row['external_id'], row['archived_pages'] = key, group['archived_pages']
            plain, info = _info(calendar_snapshot, row)
            source = fingerprint({'row': row, 'info': info})
            existing = old.get(key)
            if existing:
                _validate_event(existing, manifest)
            plan.append((row, group, plain, info, source, existing))
        cycle = state.get('cycle', {})
        cursor = cycle.get('cursor', 0) if cycle.get('source_hash') == source_hash else 0
        if type(cursor) is not int or not 0 <= cursor <= len(plan):
            raise ValueError('클라우드 작업 체크포인트가 올바르지 않습니다.')
        state['cycle'] = {'source_hash': source_hash, 'cursor': cursor, 'total': len(plan)}
        _checkpoint(state, save)
        for index in range(cursor, min(cursor + max_events, len(plan))):
            row, group, plain, info, item_hash, existing = plan[index]
            key = row['external_id']
            record = state.setdefault('records', {}).get(key, {})
            changed = False
            if existing is None:
                pending = {'kind': 'page', 'external_id': key, 'group': group, 'source_hash': item_hash,
                           'properties': _properties(row, manifest, new=True),
                           'info_hash': fingerprint(_rich_value(info['callout']['rich_text']))}
                _create(client, state, save, pending, 'POST', '/pages', {
                    'parent': {'type': 'data_source_id', 'data_source_id': manifest['agenda_data_source_id']},
                    'properties': pending['properties'], 'children': [info]})
                # Successful responses and lost-response retries take the same
                # external-ID + marker verification path before checkpointing.
                _recover(client, manifest, state, save)
                changed = True
            else:
                page_id = existing['id']
                if record.get('page_id') and not _same_id(record['page_id'], page_id):
                    raise ValueError('클라우드 행사 페이지 ID가 바뀌었습니다.')
                properties = _changed_properties(existing, _properties(row, manifest))
                if properties:
                    _patch(client, state, save, '/pages/' + page_id, {'properties': properties})
                    changed = True
                block = _find_info(client, page_id, key, expected_plain=plain,
                                   saved_id=record.get('info_block_id'))
                if block:
                    if _rich_value(block['callout'].get('rich_text')) != _rich_value(info['callout']['rich_text']):
                        _patch(client, state, save, '/blocks/' + block['id'],
                               {'callout': {'rich_text': info['callout']['rich_text']}})
                        changed = True
                    group['page_id'] = page_id
                    state.setdefault('groups', {})[key] = group
                    state['records'][key] = {'page_id': page_id, 'info_block_id': block['id'], 'source_hash': item_hash}
                else:
                    pending = {'kind': 'info', 'external_id': key, 'page_id': page_id, 'group': group,
                               'source_hash': item_hash, 'info_hash': fingerprint(_rich_value(info['callout']['rich_text']))}
                    _create(client, state, save, pending, 'PATCH', '/blocks/' + page_id + '/children',
                            {'children': [info], 'position': {'type': 'start'}})
                    _recover(client, manifest, state, save)
                    changed = True
            state['cycle']['cursor'] = index + 1
            state.pop('mutation', None)
            _checkpoint(state, save)
            summary['processed'] += 1
            summary['changed'] += int(changed)
        if state['cycle']['cursor'] < len(plan):
            summary['remaining'] = len(plan) - state['cycle']['cursor']
            return summary
        state['calendar_hash'], state['calendar_count'] = source_hash, count
        state['calendar_checked_at'] = calendar_snapshot.get('fetched_at')
        state.pop('cycle', None)
        _checkpoint(state, save)
    # A long continuation can cross Seoul midnight after its initial validation.
    # Preserve the meal display rather than writing yesterday under today's run.
    if meals_snapshot['date'] != _today():
        raise ValueError('작업 중 한국 날짜가 바뀌었습니다. 오늘의 급식을 다시 조회하세요.')
    if _rich_value(targets['meals']['callout'].get('rich_text')) != _rich_value(desired_meal['callout']['rich_text']):
        _patch(client, state, save, '/blocks/' + manifest['meals_block_id'],
               {'callout': {'rich_text': desired_meal['callout']['rich_text']}})
        summary['meals_changed'] = True
    state['meals_date'], state['meals_checked_at'] = meals_snapshot['date'], meals_snapshot['fetched_at']
    state['completed_at'] = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    state.pop('mutation', None)
    _checkpoint(state, save)
    return {**summary, 'complete': True, 'remaining': 0, 'calendar_unchanged': unchanged}
