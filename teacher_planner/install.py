"""Resumable, creation-only workspace installation."""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from .client import NotionError
from .model import academic_label, blueprint, reciprocal_property, rich, schema, selected, values, view_payload


def page_id(text):
    # Ignore URL query (a view ID is not a parent page ID).
    raw = text.split('?')[0].rstrip('/')
    match = re.search(r'([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$', raw)
    if not match:
        raise ValueError('올바른 Notion 페이지 URL 또는 UUID를 입력하세요.')
    return str(UUID(match.group(1)))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_suffix('.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f'다른 실행이 진행 중이거나 잠금이 남아 있습니다: {lock}') from None
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink()


class Journal:
    def __init__(self, path, client):
        self.path, self.client = Path(path), client
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'objects': {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    def ready(self):
        if self.data.get('pending'):
            key = self.data['pending']['key']
            raise ValueError(f'완료 여부가 불확실한 생성 작업: {key}. docs/RECOVERY.md를 먼저 확인하세요.')

    def create(self, key, endpoint, payload):
        self.ready()
        if key in self.data['objects']:
            return self.data['objects'][key]
        self.data['pending'] = {'key': key, 'endpoint': endpoint, 'payload': payload}
        self.save()
        try:
            result = self.client.request('POST', endpoint, payload)
        except NotionError as e:
            # These are definitive rejections, not ambiguous writes.
            if e.status in (400, 401, 403, 404, 429):
                self.data.pop('pending', None)
                self.save()
            raise
        self.data['objects'][key] = compact(result)
        self.data.pop('pending')
        self.save()
        return self.data['objects'][key]


def compact(result):
    if not result.get('id'):
        raise ValueError('생성 응답에 ID가 없습니다. pending 상태를 확인하세요.')
    return {k: result[k] for k in ('id', 'object', 'url', 'data_sources', 'parent', 'name', 'type') if k in result}


def block(kind, text):
    return {'object': 'block', 'type': kind, kind: {'rich_text': rich(text)}}


def finish_reciprocal(j, key, source_id, source_name, target_id, expected_name, source):
    """Name only the reverse property made by this installation; resume by stable IDs.

    Notion documents dual_property:{} creation and renaming returned properties by ID:
    https://developers.notion.com/reference/property-object
    https://developers.notion.com/reference/update-data-source-properties
    """
    target_path = '/data_sources/' + target_id
    props = j.client.request('GET', target_path)['properties']
    name, reverse = reciprocal_property(source, source_name, source_id, target_id, props)
    plans = j.data.setdefault('reciprocal_relations', {})
    plan = plans.get(key)
    if plan is None:
        # An existing relation is reusable only if already exactly as requested.
        if name != expected_name:
            raise ValueError('기존 역방향 관계 이름이 다릅니다. 자동 변경을 중단합니다.')
        plan = plans[key] = {'source_id': source_id, 'target_id': target_id, 'name': expected_name}
    if (plan.get('source_id'), plan.get('target_id'), plan.get('name')) != (source_id, target_id, expected_name):
        raise ValueError('저장된 역방향 관계 계획이 다릅니다. 자동 변경을 중단합니다.')
    if plan.get('creation_pending') and name != expected_name:
        raise ValueError('양방향 관계 생성 응답이 불확실합니다. 역속성 이름을 확인한 뒤 재시도하세요. 자동 개명하지 않습니다.')
    owns_pair = (plan.get('creating') and plan.get('source_property_id') == source['id']
                 and plan.get('target_property_id') == reverse['id'])
    for field, actual in (('source_property_id', source['id']), ('target_property_id', reverse['id'])):
        if plan.get(field) not in (None, actual):
            raise ValueError('저장된 양방향 관계 속성 ID가 다릅니다. 자동 변경을 중단합니다.')
        plan[field] = actual
    if name != expected_name:
        if (plan.get('complete') or not owns_pair or expected_name in props
                or plan.get('initial_name', name) != name):
            raise ValueError('기존 역방향 관계 이름이 다르거나 충돌합니다. 자동 변경을 중단합니다.')
        plan['initial_name'] = name
        j.save()  # The exact property ID and original name precede the rename.
        j.client.request('PATCH', target_path, {'properties': {reverse['id']: {'name': expected_name}}})
        source = j.client.request('GET', '/data_sources/' + source_id)['properties'][source_name]
        props = j.client.request('GET', target_path)['properties']
        name, reverse = reciprocal_property(source, source_name, source_id, target_id, props)
        if name != expected_name or reverse['id'] != plan['target_property_id']:
            raise ValueError('역방향 관계 이름 변경을 확인하지 못했습니다.')
    plan['complete'] = True
    plan.pop('creating', None)
    plan.pop('creation_pending', None)
    j.save()


def install(client, c, parent, state_path):
    parent = page_id(parent)
    j = Journal(state_path, client)
    j.ready()
    identity = client.request('GET', '/users/me')['id']
    from .dashboard import layout_spec
    signature = fingerprint({'config': c, 'blueprint': blueprint(), 'dashboard': layout_spec()})
    if j.data.get('identity') and (j.data['identity'] != identity or j.data['parent'] != parent or j.data['signature'] != signature):
        raise ValueError('기존 설치와 계정·상위 페이지·설정·설계가 다릅니다. 새 기본 템플릿은 별도 state 경로로 새 수첩을 만드세요. 기존 수첩은 자동 변경하지 않습니다.')
    client.request('GET', '/pages/' + parent)
    if 'academic_label' not in j.data:
        # Older installations implicitly displayed semester 1 even when config
        # omitted it. Preserve that label through partial retries without
        # changing the signed config, saved requests, or existing Notion blocks.
        label_config = c
        if j.data.get('identity') and 'semester' not in c:
            label_config = {**c, 'semester': 1}
        j.data['academic_label'] = academic_label(label_config)
    j.data.update(identity=identity, parent=parent, signature=signature, config=c)
    j.save()
    root = j.create('root', '/pages', {
        'parent': {'type': 'page_id', 'page_id': parent},
        'icon': {'type': 'emoji', 'emoji': '📒'},
        'properties': {'title': {'title': rich(f"{j.data['academic_label']} · {c['title']}")}},
        'children': [block('paragraph', '오늘의 수업과 꼭 해야 할 일을 한곳에. 작은 기록으로 가볍게 시작하세요.')]})
    sections = {}
    for name in ('운영 자료', '학생 기록'):
        sections[name] = j.create('section:' + name, '/pages', {
            'parent': {'page_id': root['id']}, 'properties': {'title': {'title': rich(name)}},
            'children': [block('paragraph', '각 표의 현재 학년도 / 보관함 탭에서 기록을 관리합니다. 보관은 삭제가 아닙니다.')]})['id']
    definitions, views = selected(c)
    databases = {}
    for d in definitions:
        props = schema(d['properties'])
        if d['key'] == 'classes':
            props['담당 교과'] = {'multi_select': {'options': [{'name': s} for s in c['subjects']]}}
        obj = j.create('db:' + d['key'], '/databases', {
            'parent': {'type': 'page_id', 'page_id': sections[d['section']]},
            'title': rich(d['title']), 'is_inline': False,
            'initial_data_source': {'properties': props}})
        # Retrieve canonical IDs instead of assuming database_id == data_source_id.
        actual = client.request('GET', '/databases/' + obj['id'])
        if len(actual.get('data_sources', [])) != 1:
            raise ValueError('설치 데이터베이스의 데이터 소스가 하나가 아닙니다. 자동 재생성을 중단합니다.')
        databases[d['key']] = {'id': obj['id'], 'data_source_id': actual['data_sources'][0]['id']}
    j.data['databases'] = databases
    j.save()
    ids = {k: v['data_source_id'] for k, v in databases.items()}
    for d in definitions:
        ds = ids[d['key']]
        current = client.request('GET', '/data_sources/' + ds)['properties']
        relations = {name: spec for name, spec in schema(d['properties'], ids).items() if 'relation' in spec}
        missing = {}
        for name, prop in relations.items():
            if name not in current:
                reciprocal = d['properties'][name].get('reciprocal')
                if reciprocal:
                    key = d['key'] + ':' + name
                    target_id = prop['relation']['data_source_id']
                    plans = j.data.setdefault('reciprocal_relations', {})
                    plan = plans.get(key)
                    if plan and plan.get('source_property_id'):
                        raise ValueError('저장된 양방향 관계 속성이 없어 자동 재생성을 중단합니다.')
                    if plan and plan.get('creation_pending'):
                        raise ValueError('양방향 관계 생성 응답이 불확실합니다. 실제 생성 여부를 확인하기 전 자동 재생성을 중단합니다.')
                    if plan and (plan.get('source_id'), plan.get('target_id'), plan.get('name')) != (ds, target_id, reciprocal):
                        raise ValueError('저장된 역방향 관계 계획이 다릅니다. 자동 변경을 중단합니다.')
                    target_props = client.request('GET', '/data_sources/' + target_id)['properties']
                    if reciprocal in target_props:
                        raise ValueError('역방향 관계 이름이 기존 속성과 충돌합니다. 자동 변경을 중단합니다.')
                    plans[key] = {'source_id': ds, 'target_id': target_id, 'name': reciprocal, 'creation_pending': True}
                    j.save()
                missing[name] = prop
            elif (current[name].get('relation', {}).get('data_source_id') != prop['relation']['data_source_id']
                  or current[name].get('relation', {}).get('type') != prop['relation']['type']):
                raise ValueError(f"{d['title']} / {name} 관계가 달라 자동 변경을 중단합니다.")
        if missing:
            duals = [name for name in missing if d['properties'][name].get('reciprocal')]
            try:
                result = client.request('PATCH', '/data_sources/' + ds, {'properties': missing})
            except NotionError as error:
                if error.status in (400, 401, 403, 404, 429):
                    for name in duals:
                        j.data['reciprocal_relations'].pop(d['key'] + ':' + name)
                    j.save()
                raise
            # A plan alone does not establish ownership after a crash/unknown response.
            # Persist the successful response's IDs before any read or reverse rename.
            for name in duals:
                created = result.get('properties', {}).get(name, {})
                reverse_id = created.get('relation', {}).get('dual_property', {}).get('synced_property_id')
                if not created.get('id') or not reverse_id:
                    raise ValueError('양방향 관계 생성 응답의 속성 ID를 확인하지 못했습니다. 자동 개명하지 않습니다.')
                plan = j.data['reciprocal_relations'][d['key'] + ':' + name]
                plan.update(source_property_id=created['id'], target_property_id=reverse_id, creating=True)
                plan.pop('creation_pending')
            if duals:
                j.save()
        current = client.request('GET', '/data_sources/' + ds)['properties']
        for name, prop in d['properties'].items():
            if prop.get('reciprocal'):
                finish_reciprocal(j, d['key'] + ':' + name, ds, name, ids[prop['target']], prop['reciprocal'], current[name])
    # A later source may add a reciprocal to an earlier source (e.g. students).
    actual_props = {key: client.request('GET', '/data_sources/' + ds)['properties'] for key, ds in ids.items()}
    for v in views:
        j.create('view:' + v['key'], '/views', view_payload(v, databases[v['source']], actual_props[v['source']], c['academic_year']))
    from .home import install_dashboard
    install_dashboard(j, c, root['id'], databases, actual_props)
    from .extras import install_extras
    install_extras(j, c, root['id'], databases)
    seeds(j, c, definitions, databases)
    j.data['complete'] = True
    # `complete` remains the legacy data-creation checkpoint for sync/resume.
    # REST creation cannot prove full width, column reflow or collapsed UI state.
    from .layout_contract import load_contract
    contract = load_contract()
    j.data.setdefault('layout_acceptance', {
        'contract_id': contract['id'], 'contract_version': contract['version'],
        'status': 'pending', 'layout_complete': False,
        'required_command': 'verify --layout-snapshot .local/layout-snapshot.json',
    })
    j.save()
    return root.get('url', 'https://www.notion.so/' + root['id'].replace('-', ''))


def seeds(j, c, definitions, databases):
    definitions = {d['key']: d['properties'] for d in definitions}

    def add(key, source, props):
        props = {'학년도': c['academic_year'], '보관': False, **props}
        return j.create('seed:' + key, '/pages', {'parent': {'type': 'data_source_id', 'data_source_id': databases[source]['data_source_id']},
                                               'properties': values(props, definitions[source])})['id']
    for i, row in enumerate(c['classes']):
        add('class:' + str(i), 'classes', {'이름': row['name'], '학년': row['grade'], '반': row['class_name'], '담당 교과': c['subjects'], '담임': row['homeroom']})
    for name in ('수업', '담임', '행정', '평가', '상담', '연수'):
        add('area:' + name, 'areas', {'이름': name, '분류': name, '점검 주기': '매주'})
    if not c['demo']:
        return
    # All demo data is intentionally fictional and labelled.
    cls = j.data['objects']['seed:class:0']['id']
    student = add('demo:student', 'students', {'이름': '[예시] 가상학생', '학생 ID': 'DEMO-001', '학급': [cls], '번호': 1, '재적': '재학'})
    monday = date(c['academic_year'], 3, 2)
    monday += timedelta(days=(-monday.weekday()) % 7)
    agenda = add('demo:agenda', 'agenda', {'이름': '[예시] 첫 수업 자료 준비', '종류': '할 일', '상태': '예정', '우선순위': 'P2 계획', '업무 분류': '수업', '일정': monday.isoformat(), '마감': monday.isoformat(), '학급': [cls], '다음 행동': '학습지 문항 3개 고르기'})
    add('demo:lesson', 'lessons', {'이름': '[예시] 첫 단원', '학급': [cls], '교과': c['subjects'][0], '계획 차시': 4, '완료 차시': 1, '상태': '진행'})
    add('demo:counseling', 'counseling', {'이름': '[예시] 적응 상담', '학생': [student], '상담일': monday.isoformat(), '대상': '학생', '상태': '예정'})
    if c['modules']['attendance']:
        add('demo:attendance', 'attendance', {'이름': '[예시] 출결 확인', '학생': [student], '학급': [cls], '날짜': monday.isoformat(), '상태': '출석'})
        add('demo:submission', 'submissions', {'이름': '[예시] 학습지', '학생': [student], '학급': [cls], '항목': '학습지', '상태': '미제출', '업무·일정': [agenda]})
    if c['modules']['assessment']:
        add('demo:assessment', 'assessments', {'이름': '[예시] 발표 평가', '학급': [cls], '교과': c['subjects'][0], '유형': '수행', '대상 인원': 20, '채점 완료': 5, '상태': '채점'})
    if c['modules']['contact']:
        add('demo:contact', 'contacts', {'이름': '[예시] 연락 확인', '학생': [student], '상태': '연락 예정'})
    if c['modules']['meeting']:
        add('demo:meeting', 'meetings', {'이름': '[예시] 교과 협의', '업무 분류': '교과', '결정 사항': '공동 자료 정리', '후속 업무': [agenda]})
