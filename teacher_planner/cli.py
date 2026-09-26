import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .client import Client, NotionError
from .install import Journal, compact, fingerprint, install, locked, page_id
from .model import blueprint, config, dashboard_views, selected
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
    week = commands.add_parser('comcigan-week', help='컴시간 웹에서 선택한 교사의 주간 시간표 조회')
    sync = commands.add_parser('comcigan-sync', help='컴시간 주간 시간표 검증 및 Notion 반영')
    for command in (week, sync):
        command.add_argument('--school-code', default=os.getenv('COMCIGAN_SCHOOL_CODE', ''), help='컴시간 학교 코드; 환경변수 COMCIGAN_SCHOOL_CODE')
        command.add_argument('--teacher-id', type=int, default=os.getenv('COMCIGAN_TEACHER_ID'), help='교사 번호; 환경변수 COMCIGAN_TEACHER_ID')
        command.add_argument('--date', help='조회할 주에 속한 YYYY-MM-DD 날짜; 기본은 서울 기준 오늘')
    week.add_argument('--output', default='.local/comcigan-week.json', help='.local/ 또는 private/ 아래 저장 경로')
    sync.add_argument('--config', default='.local/config.json')
    sync.add_argument('--state', default='.local/state.json')
    sync.add_argument('--mapping', help='학급·교과 이름 매핑 JSON 경로')
    sync.add_argument('--period-times', help='교시별 시작·종료 시각 JSON 경로')
    sync.add_argument('--apply', action='store_true')
    sync.add_argument('--watch', action='store_true', help='현재 터미널에서 주기적으로 확인; --apply 필요')
    sync.add_argument('--interval', type=int, default=600, help='확인 간격 초, 최소 300 (기본 600)')
    verify = commands.add_parser('verify', help='설치된 원격 속성·관계·캘린더·홈 배치 확인')
    verify.add_argument('--state', default='.local/state.json')
    rec = commands.add_parser('recover', help='불확실한 생성 요청의 기존 Notion 객체 연결')
    rec.add_argument('--state', default='.local/state.json')
    rec.add_argument('--id', required=True, help='Notion에서 확인한 생성 객체 ID')
    args = p.parse_args(argv)
    try:
        if args.command in ('comcigan-week', 'comcigan-sync'):
            return run_comcigan(args)
        elif args.command in ('plan', 'install'):
            c = config(args.config)
            ds, vs = selected(c)
            if args.command == 'plan' or not args.apply:
                report = {'title': c['title'], 'academic_year': c['academic_year'], 'modules': c['modules'], 'database_count': len(ds), 'view_count': len(vs) + len(dashboard_views(c)), 'databases': ds if getattr(args, 'full', False) else [d['title'] for d in ds], 'applied': False}
                instances = dashboard_views(c)
                report.update(semester=c.get('semester', 1), workspace_page_count=4,
                              workspace_view_count=len(instances),
                              workspace_database_block_count=len({(v['workspace_page'], v['section']) for v in instances}))
                if getattr(args, 'full', False):
                    report['views'] = vs
                    report['workspace_views'] = instances
                    from .dashboard import layout_spec
                    report['dashboard'] = layout_spec()
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
                from .home import refresh_dashboard
                matrix_count = refresh_dashboard(client, args.state, rows)
            print(f'{len(rows)}개 수업의 시간표·일정 {count}개 행을 반영했습니다.')
            if matrix_count:
                print('홈 주간 시간표도 갱신했습니다.')
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
            print('원격 데이터베이스·속성·관계·뷰·홈 배치 확인 완료. 실제 화면과 공유 권한은 docs/ACCEPTANCE.md로 확인하세요.')
        elif args.command == 'recover':
            with locked(args.state):
                client = Client(os.getenv('NOTION_TOKEN'))
                j = Journal(args.state, client)
                pending = j.data.get('pending')
                if not pending:
                    raise ValueError('복구할 pending 작업이 없습니다.')
                if client.request('GET', '/users/me')['id'] != j.data.get('identity'):
                    raise ValueError('설치 당시의 연결이 아닙니다.')
                if pending.get('block_parent'):
                    from .blocks import recover_block
                    obj = client.request('GET', '/blocks/' + page_id(args.id))
                    recover_block(client, pending, obj)
                else:
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
    except KeyboardInterrupt:
        print('실행을 중지했습니다.')
        return 0


def read_optional_json(path):
    if path is None:
        return None
    with Path(path).open(encoding='utf-8-sig') as file:
        return json.load(file)


def private_json(path, payload):
    """Atomically replace a private output with owner-only file permissions."""
    path = Path(path).resolve()
    # macOS resolves temporary paths beneath the system /private directory;
    # that root directory is not the repository's ignored private/ folder.
    if not any(part in ('.local', 'private') for part in path.parts[2:-1]):
        raise ValueError('시간표 출력은 .local/ 또는 private/ 아래에 저장하세요.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_comcigan(args):
    # Keep the network provider out of the offline install/import paths.
    from .comcigan import fetch_week, normalize

    if not args.school_code or args.teacher_id is None or args.teacher_id <= 0:
        raise ValueError('컴시간 학교 코드와 양의 정수 교사 번호를 입력하세요.')
    requested = date.fromisoformat(args.date) if args.date else None
    if args.command == 'comcigan-sync':
        if args.watch and not args.apply:
            raise ValueError('--watch에는 --apply가 필요합니다.')
        if args.interval < 300:
            raise ValueError('조회 간격은 최소 300초여야 합니다.')
        if args.watch and args.date:
            raise ValueError('--watch와 --date는 함께 사용할 수 없습니다. 현재 주를 자동으로 조회합니다.')
        c = config(args.config)
        mapping = read_optional_json(args.mapping)
        period_times = read_optional_json(args.period_times)
    while True:
        snapshot = fetch_week(args.school_code, args.teacher_id, requested=requested)
        omissions = {'omitted_slots': snapshot['omitted_slots']} if snapshot.get('omitted_slots') else {}
        if args.command == 'comcigan-week':
            private_json(args.output, snapshot)
            print(json.dumps({'week_start': snapshot['week_start'], 'lesson_count': len(snapshot['rows']),
                              'output': args.output, 'applied': False, **omissions}, ensure_ascii=False, indent=2))
            return 0
        rows = normalize(snapshot, c, mapping=mapping, period_times=period_times)
        if not args.apply:
            print(json.dumps({'week_start': snapshot['week_start'], 'validated_rows': len(rows),
                              'applied': False, **omissions,
                              'behavior': '컴시간 조회와 형식 검증만 완료했습니다. Notion 반영은 --apply로 실행하세요. 누락 교시는 삭제하거나 휴강 처리하지 않습니다.'},
                             ensure_ascii=False, indent=2))
            return 0
        with locked(args.state):
            client = Client(os.getenv('NOTION_TOKEN'))
            j = Journal(args.state, client)
            j.ready()
            if not j.data.get('complete') or j.data['config'] != c:
                raise ValueError('설치를 완료하고 동일한 config와 state를 사용하세요.')
            if client.request('GET', '/users/me')['id'] != j.data['identity']:
                raise ValueError('설치 당시의 Notion 연결이 아닙니다.')
            binding = fingerprint({'school_code': str(snapshot['school_code']),
                                   'teacher_id': snapshot['teacher_id'], 'teacher_name': snapshot['teacher_name']})
            if j.data.get('comcigan_source') and j.data['comcigan_source'] != binding:
                raise ValueError('학교·교사 번호 또는 교사명이 기존 컴시간 연결과 다릅니다. 교사 번호 재배정 여부를 확인하고 docs/COMCIGAN.md의 연결 변경 절차를 따르세요.')
            ops = changes(client, c, j.data, rows, source='컴시간 어댑터')
            if not j.data.get('comcigan_source'):
                j.data['comcigan_source'] = binding
                j.save()
            count = apply_changes(client, args.state, ops)
            from .home import refresh_dashboard
            matrix_count = refresh_dashboard(client, args.state, rows, snapshot['week_start'])
            # apply_changes may have journaled newly created pages. Reload before
            # recording the successful poll so those IDs cannot be lost.
            latest = Journal(args.state, client)
            latest.data['comcigan_last_checked_at'] = datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
            latest.save()
        print(f"{snapshot['week_start']} 주간 {len(rows)}개 수업 확인, 시간표·일정 {count}개 행 반영.", flush=True)
        if matrix_count:
            print('홈 주간 시간표도 갱신했습니다.', flush=True)
        if omissions:
            print(f"원본에서 누락된 {snapshot['omitted_slots']}개 교시는 삭제·휴강 처리하지 않았습니다.", flush=True)
        if not args.watch:
            return 0
        sleep(args.interval)


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
    instances = dashboard_views(state['config']) if state.get('dashboard', {}).get('version', 0) >= 3 else None
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
            if instances is not None:
                keys.extend(row['instance_key'] for row in instances if row['key'] == v['key'])
            elif v['key'] in blueprint()['dashboard_views']:
                keys.append('home:' + v['key'])
            for key in keys:
                if key not in state['objects']:
                    issues.append(f'{key}: 설치 기록 없음. 이전 버전 수첩은 새 템플릿으로 자동 변환하지 않습니다.')
                    continue
                actual_view = client.request('GET', '/views/' + state['objects'][key]['id'])
                if actual_view.get('type') != v['type'] or actual_view.get('data_source_id') != ref['data_source_id']:
                    issues.append(f"{key}: 뷰 종류/원본 불일치")
                if actual_view.get('parent', {}).get('database_id') != state['objects'][key].get('parent', {}).get('database_id'):
                    issues.append(f"{key}: 뷰가 속한 데이터베이스 불일치")
                if 'date' in v:
                    conf = actual_view.get('configuration') or {}
                    if conf.get('view_range') != v['range'] or conf.get('date_property_id') != props[v['date']]['id']:
                        issues.append(f"{key}: 캘린더 날짜/주간·월간 범위 불일치")
    from .home import verify_dashboard
    return issues + verify_dashboard(client, state)
