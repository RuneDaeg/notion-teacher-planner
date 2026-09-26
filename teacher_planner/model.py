"""Shared blueprint for the REST installer and AI/MCP workflow."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def blueprint():
    return json.loads(Path(__file__).with_name('blueprint.json').read_text(encoding='utf-8'))


def config(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(c, dict):
        raise ValueError('설정 최상위는 JSON 객체여야 합니다.')
    required = {'title', 'academic_year', 'timezone', 'teacher', 'subjects', 'classes', 'modules', 'demo'}
    if required - c.keys():
        raise ValueError('설정 필드 누락: ' + ', '.join(sorted(required - c.keys())))
    for key in ('title', 'teacher'):
        if not isinstance(c[key], str) or not c[key].strip() or len(c[key]) > 100:
            raise ValueError(f'{key}: 1~100자 문자열이어야 합니다.')
    if type(c['academic_year']) is not int or not 2000 <= c['academic_year'] <= 2200:
        raise ValueError('academic_year는 2000~2200 정수여야 합니다.')
    if not isinstance(c['timezone'], str):
        raise ValueError('timezone은 IANA 시간대 문자열이어야 합니다.')
    ZoneInfo(c['timezone'])
    if not isinstance(c['subjects'], list) or not c['subjects'] or any(not isinstance(s, str) or not s.strip() or ',' in s or len(s) > 100 for s in c['subjects']):
        raise ValueError('subjects에는 쉼표 없는 교과명을 입력하세요.')
    if len(c['subjects']) != len(set(c['subjects'])):
        raise ValueError('교과명은 중복될 수 없습니다.')
    if not isinstance(c['classes'], list) or not c['classes']:
        raise ValueError('classes에는 최소 한 학급이 필요합니다.')
    names = set()
    for row in c['classes']:
        if not isinstance(row, dict):
            raise ValueError('각 학급 설정은 JSON 객체여야 합니다.')
        name = row.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or name in names:
            raise ValueError('학급 이름은 중복 없는 1~100자 문자열이어야 합니다.')
        if type(row.get('grade')) is not int or not 1 <= row['grade'] <= 12:
            raise ValueError('학년은 1~12 정수여야 합니다.')
        if not isinstance(row.get('class_name'), str) or type(row.get('homeroom')) is not bool:
            raise ValueError('class_name 문자열과 homeroom 참/거짓이 필요합니다.')
        names.add(name)
    required_modules = {'attendance', 'assessment', 'contact', 'meeting'}
    if not isinstance(c['modules'], dict) or required_modules - set(c['modules']) or set(c['modules']) - (required_modules | {'staff', 'accounts'}):
        raise ValueError('modules에는 attendance, assessment, contact, meeting이 필요하며 staff, accounts를 선택할 수 있습니다.')
    if any(type(x) is not bool for x in c['modules'].values()) or type(c['demo']) is not bool:
        raise ValueError('모듈 선택과 demo는 true/false여야 합니다.')
    from .dashboard import dashboard_config
    dashboard_config(c)
    return c


def selected(c):
    b = blueprint()
    dbs = [d for d in b['databases'] if d['module'] == 'core' or c['modules'].get(d['module'], False)]
    keys = {d['key'] for d in dbs}
    return dbs, [v for v in b['views'] if v['source'] in keys]


def dashboard_views(c):
    keys = set(blueprint()['dashboard_views'])
    return [v for v in selected(c)[1] if v['key'] in keys]


def rich(text):
    text = str(text)
    if len(text) > 2000:
        raise ValueError('한 텍스트 값은 2000자 이하여야 합니다.')
    return [{'type': 'text', 'text': {'content': text}}]


def schema(properties, ids=None):
    result = {}
    for name, p in properties.items():
        t = p['type']
        if t == 'relation':
            if ids is not None:
                result[name] = {'relation': {'data_source_id': ids[p['target']], 'type': 'single_property', 'single_property': {}}}
        elif t in ('select', 'multi_select'):
            colors = ['red', 'blue', 'yellow', 'gray'] if name == '우선순위' else ['default'] * len(p['options'])
            result[name] = {t: {'options': [{'name': s, 'color': colors[i]} for i, s in enumerate(p['options'])]}}
        elif t == 'formula':
            result[name] = {'formula': {'expression': p['expression']}}
        elif t == 'number':
            result[name] = {'number': {'format': 'number'}}
        else:
            result[name] = {t: {}}
    return result


def values(properties, definitions):
    out = {}
    for name, value in properties.items():
        t = definitions[name]['type']
        if t in ('title', 'rich_text'):
            out[name] = {t: rich(value)}
        elif t == 'relation':
            out[name] = {'relation': [{'id': x} for x in value]}
        elif t == 'select':
            if value not in definitions[name]['options']:
                raise ValueError(f'{name}: 알 수 없는 선택 값')
            out[name] = {'select': {'name': value}}
        elif t == 'multi_select':
            out[name] = {t: [{'name': x} for x in value]}
        elif t == 'date':
            out[name] = {'date': value if isinstance(value, dict) else {'start': value}}
        else:
            out[name] = {t: value}
    return out


def view_payload(v, db, actual_properties, academic_year, parent=None, after=None):
    pid = lambda name: actual_properties[name]['id']
    conf = {'type': v['type']}
    if 'date' in v:
        conf.update(date_property_id=pid(v['date']), view_range=v['range'], show_weekends=True)
    if 'group' in v:
        prop = actual_properties[v['group']]
        t = prop['type']
        group = {'property_id': prop['id'], 'type': 'text' if t == 'rich_text' else t, 'sort': {'type': 'ascending'}}
        if t in ('rich_text', 'title'):
            group['group_by'] = 'exact'
        conf['group_by'] = group
    if 'show' in v:
        conf['properties'] = [{'property_id': prop['id'], 'visible': name in v['show']} for name, prop in actual_properties.items()]
    filters = [v['filter'], {'property': '학년도', 'number': {'equals': academic_year}}]
    payload = {'data_source_id': db['data_source_id'], 'name': v['name'], 'type': v['type'], 'configuration': conf, 'filter': {'and': filters}}
    if 'sorts' in v:
        payload['sorts'] = v['sorts']
    if parent:
        payload['create_database'] = {'parent': {'type': 'page_id', 'page_id': parent}}
        if after:
            payload['create_database']['position'] = {'type': 'after_block', 'block_id': after}
    else:
        payload['database_id'] = db['id']
    return payload
