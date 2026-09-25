"""Explicit dated timetable import; no unofficial scraping or inferred school days."""
import csv
import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .install import Journal
from .model import selected, values

FIELDS = {'date', 'period', 'start', 'end', 'class_name', 'subject', 'room', 'status'}


def read_rows(path, c):
    path = Path(path)
    with path.open(encoding='utf-8-sig', newline='') as f:
        if path.suffix.lower() == '.json':
            rows = json.load(f)
        else:
            reader = csv.DictReader(f)
            if set(reader.fieldnames or []) != FIELDS:
                raise ValueError('CSV 열은 ' + ', '.join(sorted(FIELDS)) + ' 이어야 합니다.')
            rows = list(reader)
    if not isinstance(rows, list):
        raise ValueError('JSON 최상위는 시간표 행 배열이어야 합니다.')
    zone = ZoneInfo(c['timezone'])
    classes = {r['name'] for r in c['classes']}
    result, keys = [], set()
    intervals = {}
    for i, r in enumerate(rows, 2):
        if not isinstance(r, dict) or set(r) != FIELDS or any(v is None for v in r.values()):
            raise ValueError(f'{i}행: 필수 열을 확인하세요.')
        r = {k: str(v).strip() for k, v in r.items()}
        try:
            day = date.fromisoformat(r['date'])
            period = int(r['period'])
            start_t, end_t = time.fromisoformat(r['start']), time.fromisoformat(r['end'])
        except ValueError:
            raise ValueError(f'{i}행: 날짜 YYYY-MM-DD / 시간 HH:MM / 교시 정수를 확인하세요.') from None
        if start_t.tzinfo or end_t.tzinfo:
            raise ValueError(f'{i}행: 시간에는 오프셋을 쓰지 마세요. 설정의 시간대를 사용합니다.')
        if not 1 <= period <= 20 or start_t >= end_t:
            raise ValueError(f'{i}행: 교시 범위 또는 수업 시작·종료 시간을 확인하세요.')
        if not date(c['academic_year'], 3, 1) <= day < date(c['academic_year'] + 1, 3, 1):
            raise ValueError(f'{i}행: 설정 학년도(3월~다음 해 2월) 밖의 날짜입니다.')
        if r['class_name'] not in classes or r['subject'] not in c['subjects']:
            raise ValueError(f'{i}행: 설정에 없는 학급 또는 교과입니다.')
        if r['status'] not in ('예정', '변경', '휴강', '완료'):
            raise ValueError(f'{i}행: 상태는 예정/변경/휴강/완료 중 하나여야 합니다.')
        if any(len(s) > 200 for s in r.values()):
            raise ValueError(f'{i}행: 시간표 값이 너무 깁니다.')
        # Keep the ID when class/subject/room/time changes in the same dated slot.
        key = 'tt:' + hashlib.sha256(f"{c['academic_year']}|{c['teacher']}|{day}|{period}".encode()).hexdigest()[:24]
        if key in keys:
            raise ValueError(f'{i}행: 동일 날짜·교시가 중복됩니다. 합반은 하나의 학급명으로 설정하세요.')
        keys.add(key)
        if r['status'] != '휴강':
            for a, b in intervals.get(day, []):
                if start_t < b and end_t > a:
                    raise ValueError(f'{i}행: 같은 교사의 수업 시간이 겹칩니다.')
            intervals.setdefault(day, []).append((start_t, end_t))
        result.append({**r, 'period': period, 'external_id': key,
                       'date_range': {'start': datetime.combine(day, start_t, zone).isoformat(),
                                      'end': datetime.combine(day, end_t, zone).isoformat()}})
    return result


def text_property(page, name):
    prop = page['properties'].get(name, {})
    return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in prop.get(prop.get('type', ''), []) if isinstance(x, dict))


def unique_index(pages, name):
    index = {}
    for row in pages:
        key = text_property(row, name)
        if not key:
            continue
        if key in index:
            raise ValueError(f'{name} 중복이 있어 가져오기를 중단합니다. 원본 표에서 중복을 정리하세요.')
        index[key] = row
    return index


def changes(client, c, state, rows):
    """Resolve every class and both record sets before the first write."""
    dbs = state['databases']
    definitions = {d['key']: d['properties'] for d in selected(c)[0]}
    current_year = {'property': '학년도', 'number': {'equals': c['academic_year']}}
    classes = unique_index(client.pages(dbs['classes']['data_source_id'], {'and': [current_year, {'property': '보관', 'checkbox': {'equals': False}}]}), '이름')
    old = {k: unique_index(client.pages(dbs[k]['data_source_id'], {'property': '외부 ID', 'rich_text': {'starts_with': 'tt:'}}), '외부 ID') for k in ('timetable', 'agenda')}
    ops = []
    for r in rows:
        if r['class_name'] not in classes:
            raise ValueError('Notion에서 대상 학급을 찾지 못했습니다. 학급 설정과 보관 상태를 확인하세요.')
        cls = classes[r['class_name']]['id']
        title = f"{r['period']}교시 · {r['subject']} · {r['class_name']}"
        common = {'이름': title, '학년도': c['academic_year'], '학급': [cls], '외부 ID': r['external_id']}
        tt = {**common, '교사': c['teacher'], '수업일': r['date_range'], '교시': r['period'], '교과': r['subject'], '교실': r['room'], '상태': r['status'], '출처': '파일 가져오기'}
        agenda = {**common, '종류': '수업', '일정': r['date_range'], '업무 분류': '수업', '상태': {'휴강': '취소', '완료': '완료'}.get(r['status'], '예정')}
        for source, props in (('agenda', agenda), ('timetable', tt)):
            existing = old[source].get(r['external_id'])
            # User-authored notes, priority and archive flag are not overwritten.
            if not existing:
                props['보관'] = False
            body = values(props, definitions[source])
            ops.append({'source': source, 'external_id': r['external_id'], 'existing': existing['id'] if existing else None, 'properties': body})
    return ops


def apply_changes(client, state_path, ops):
    j = Journal(state_path, client)
    j.ready()
    agenda_ids = {}
    for op in ops:
        key, source = op['external_id'], op['source']
        props = dict(op['properties'])
        if source == 'timetable':
            props['업무·일정'] = {'relation': [{'id': agenda_ids[key]}]}
            props['동기화 시각'] = {'date': {'start': datetime.now(ZoneInfo(j.data['config']['timezone'])).isoformat()}}
        if op['existing']:
            client.request('PATCH', '/pages/' + op['existing'], {'properties': props})
            obj_id = op['existing']
        else:
            journal_key = 'import:' + source + ':' + key
            if journal_key in j.data['objects']:
                raise ValueError('이전에 가져온 행이 원본 표에서 사라졌습니다. 보관·휴지통 상태를 확인하세요.')
            obj = j.create(journal_key, '/pages', {'parent': {'type': 'data_source_id', 'data_source_id': j.data['databases'][source]['data_source_id']}, 'properties': props})
            obj_id = obj['id']
        if source == 'agenda':
            agenda_ids[key] = obj_id
    return len(ops)
