import argparse
import json
import os
import sys
from zoneinfo import ZoneInfoNotFoundError

from .client import Client, NotionError
from .install import Journal, compact, install, locked, page_id
from .model import blueprint, config, selected
from .timetable import apply_changes, changes, read_rows


def main(argv=None):
    p = argparse.ArgumentParser(description='AI와 함께 만드는 Notion 미니 교무수첩')
    commands = p.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='오프라인 설치 명세 출력')
    plan.add_argument('--config', default='config.example.json')
    plan.add_argument('--full', action='store_true', help='AI가 읽을 속성·뷰 명세 포함')
    setup = commands.add_parser('install', help='Notion에 새 수첩 설치; 기본은 계획만 출력')
    setup.add_argument('--config', default='config.example.json')
    setup.add_argument('--parent', default=os.getenv('NOTION_PARENT_PAGE_ID', ''))
    setup.add_argument('--state', default='.local/state.json')
    setup.add_argument('--apply', action='store_true')
    imp = commands.add_parser('import-timetable', help='날짜별 CSV/JSON 시간표 가져오기')
    imp.add_argument('file')
    imp.add_argument('--config', default='config.example.json')
    imp.add_argument('--state', default='.local/state.json')
    imp.add_argument('--apply', action='store_true')
    verify = commands.add_parser('verify', help='설치된 원격 속성·관계·캘린더 확인')
    verify.add_argument('--state', default='.local/state.json')
    rec = commands.add_parser('recover', help='불확실한 생성 요청의 기존 Notion 객체 연결')
    rec.add_argument('--state', default='.local/state.json')
    rec.add_argument('--id', required=True, help='Notion에서 확인한 생성 객체 ID')
    args = p.parse_args(argv)
    try:
        if args.command in ('plan', 'install'):
            c = config(args.config)
            ds, vs = selected(c)
            if args.command == 'plan' or not args.apply:
                report = {'title': c['title'], 'academic_year': c['academic_year'], 'modules': c['modules'], 'database_count': len(ds), 'view_count': len(vs) + len(blueprint()['dashboard_views']), 'databases': ds if getattr(args, 'full', False) else [d['title'] for d in ds], 'applied': False}
                if getattr(args, 'full', False):
                    report['views'] = vs
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0
            with locked(args.state):
                url = install(Client(os.getenv('NOTION_TOKEN')), c, args.parent, args.state)
            print('설치 완료: ' + url)
            print('python -m teacher_planner verify 로 원격 결과를 확인하세요.')
        elif args.command == 'import-timetable':
            c = config(args.config)
            rows = read_rows(args.file, c)
            if not args.apply:
                print(json.dumps({'validated_rows': len(rows), 'applied': False, 'behavior': '날짜·교시 기준 추가/갱신. 빠진 행은 삭제하지 않습니다. 휴강은 명시해야 합니다.'}, ensure_ascii=False, indent=2))
                return 0
            with locked(args.state):
                client = Client(os.getenv('NOTION_TOKEN'))
                j = Journal(args.state, client)
                j.ready()
                if not j.data.get('complete') or j.data['config'] != c:
                    raise ValueError('설치를 완료하고 동일한 config와 state를 사용하세요.')
                if client.request('GET', '/users/me')['id'] != j.data['identity']:
                    raise ValueError('설치 당시의 Notion 연결이 아닙니다.')
                ops = changes(client, c, j.data, rows)
                count = apply_changes(client, args.state, ops)
            print(f'{len(rows)}개 수업의 시간표·일정 {count}개 행을 반영했습니다.')
        elif args.command == 'verify':
            client = Client(os.getenv('NOTION_TOKEN'))
            j = Journal(args.state, client)
            j.ready()
            if not j.data.get('complete'):
                raise ValueError('아직 설치가 완료되지 않았습니다.')
            if client.request('GET', '/users/me')['id'] != j.data['identity']:
                raise ValueError('설치 당시의 연결이 아닙니다.')
            issues = verify_remote(client, j.data)
            if issues:
                print('\n'.join(issues), file=sys.stderr)
                return 1
            print('원격 데이터베이스·속성·관계·뷰 설정 확인 완료. 실제 화면과 공유 권한은 docs/ACCEPTANCE.md로 확인하세요.')
        elif args.command == 'recover':
            with locked(args.state):
                client = Client(os.getenv('NOTION_TOKEN'))
                j = Journal(args.state, client)
                pending = j.data.get('pending')
                if not pending:
                    raise ValueError('복구할 pending 작업이 없습니다.')
                if client.request('GET', '/users/me')['id'] != j.data.get('identity'):
                    raise ValueError('설치 당시의 연결이 아닙니다.')
                obj = client.request('GET', pending['endpoint'] + '/' + page_id(args.id))
                validate_recovery(pending, obj)
                j.data['objects'][pending['key']] = compact(obj)
                j.data.pop('pending')
                j.save()
            print('기존 객체를 연결했습니다. 원래 명령을 다시 실행하세요.')
        return 0
    except (ValueError, KeyError, OSError, NotionError, ZoneInfoNotFoundError) as e:
        print('오류: ' + str(e), file=sys.stderr)
        return 1


def validate_recovery(pending, obj):
    payload = pending['payload']
    if obj.get('archived') or obj.get('in_trash'):
        raise ValueError('휴지통 객체는 복구 대상으로 쓸 수 없습니다.')
    if pending['endpoint'] == '/views':
        if 'create_database' in payload:
            raise ValueError('홈 연결 뷰는 부모 페이지까지 수동 검증해야 합니다. docs/RECOVERY.md를 확인하세요.')
        if obj.get('name') != payload['name'] or obj.get('data_source_id') != payload['data_source_id'] or obj.get('parent', {}).get('database_id') != payload['database_id'] or obj.get('type') != payload['type']:
            raise ValueError('뷰 이름·원본·부모·종류가 일치하지 않습니다.')
    else:
        expected = payload['parent']
        actual = obj.get('parent', {})
        for kind in ('page_id', 'data_source_id'):
            if kind in expected and actual.get(kind) != expected[kind]:
                raise ValueError('생성 대상 부모와 일치하지 않습니다.')
        if pending['endpoint'] == '/databases':
            before, after = payload['title'], obj['title']
        else:
            prop = next(v for v in payload['properties'].values() if 'title' in v)
            before = prop['title']
            after = next(v['title'] for v in obj['properties'].values() if v.get('type') == 'title')
        content = lambda xs: ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in xs)
        if content(before) != content(after):
            raise ValueError('생성 대상 제목과 일치하지 않습니다.')


def verify_remote(client, state):
    issues = []
    ds, vs = selected(state['config'])
    for d in ds:
        ref = state['databases'][d['key']]
        actual = client.request('GET', '/data_sources/' + ref['data_source_id'])
        props = actual['properties']
        for name, expected in d['properties'].items():
            if name not in props or props[name]['type'] != expected['type']:
                issues.append(f"{d['title']}: {name} 속성/유형 불일치")
            elif expected['type'] == 'relation' and props[name]['relation'].get('data_source_id') != state['databases'][expected['target']]['data_source_id']:
                issues.append(f"{d['title']}: {name} 관계 대상 불일치")
        for v in (v for v in vs if v['source'] == d['key']):
            keys = ['view:' + v['key']]
            if v['key'] in blueprint()['dashboard_views']:
                keys.append('home:' + v['key'])
            for key in keys:
                actual_view = client.request('GET', '/views/' + state['objects'][key]['id'])
                if actual_view.get('type') != v['type'] or actual_view.get('data_source_id') != ref['data_source_id']:
                    issues.append(f"{key}: 뷰 종류/원본 불일치")
                if 'date' in v:
                    conf = actual_view.get('configuration') or {}
                    if conf.get('view_range') != v['range'] or conf.get('date_property_id') != props[v['date']]['id']:
                        issues.append(f"{key}: 캘린더 날짜/주간·월간 범위 불일치")
    return issues
