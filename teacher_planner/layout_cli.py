"""Offline layout handoff and evidence validation; never writes to Notion."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .layout_contract import active_sections_for_config, load_contract, render_page, resolve_page_layout


def layout_plan(config):
    contract = load_contract()
    return {
        'contract_id': contract['id'], 'contract_version': contract['version'],
        'applied': False, 'layout_complete': False,
        'page_settings': contract['page_settings'], 'timetable': contract['timetable'],
        'pages': {key: resolve_page_layout(key, active_sections_for_config(config, key))
                  for key in contract['pages']},
        'required_finish': [
            '실제 페이지 내용을 먼저 가져와 원본 DB·하위 페이지·동기화 블록 ID를 보존합니다.',
            '공통 배치에 따라 기존 블록을 이동합니다. 지원하지 않는 도구에서는 완료를 보류합니다.',
            '네 핵심 페이지를 전체 너비, 작은 텍스트 끔으로 설정하고 실제 UI에서 확인합니다.',
            '관리용 목록을 접고 네 페이지의 실제 구조·화면을 다시 읽어 verify-layout으로 검사합니다.',
        ],
    }


def add_commands(commands):
    plan = commands.add_parser('layout-plan', help='공통 배치·폭·접기 명세 출력 (Notion 변경 없음)')
    plan.add_argument('--config', default='config.example.json')
    plan.add_argument('--output')
    render = commands.add_parser('render-layout', help='기존 구역을 공통 배치의 Notion Markdown으로 배치')
    render.add_argument('--page', required=True, choices=list(load_contract()['pages']))
    render.add_argument('--sections', required=True, help='구역 키 → 실제 Markdown/카드 배열 JSON')
    render.add_argument('--output', required=True, help='검토할 Markdown 파일; 자동 적용하지 않음')
    check = commands.add_parser('verify-layout', help='실제 구조·UI 증거 검사; 미확인은 실패')
    check.add_argument('--snapshot', required=True)
    check.add_argument('--report')


def write_text(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(content, encoding='utf-8')
    target.chmod(0o600)


def load_evidence(path, *, state=None, core_verified=False):
    """Bind evidence to this installation, not a different notebook's screenshots."""
    from .layout_verify import verify_layout_snapshot
    source = Path(path).resolve()
    snapshot = json.loads(source.read_text(encoding='utf-8'))
    if core_verified and state is None:
        raise ValueError('기능 검증 결과를 반영하려면 같은 설치 기록이 필요합니다.')
    if state is not None:
        from .install import page_id
        from .model import dashboard_views
        expected = state.get('dashboard', {}).get('pages', {})
        supplied = snapshot.get('expected_page_ids', {})
        if set(expected) != set(load_contract()['pages']) or any(
                page_id(supplied.get(key, '')) != page_id(value) for key, value in expected.items()):
            raise ValueError('배치 증거의 페이지 ID가 이 설치 기록과 다릅니다.')
        snapshot['expected_page_ids'] = dict(expected)
        for key in expected:
            required = set(active_sections_for_config(state['config'], key))
            actual = set(snapshot.get('active_sections', {}).get(key, []))
            if not required <= actual:
                raise ValueError(f'{key}: 선택한 모듈의 배치 증거가 빠졌습니다.')
        # Installation state is authoritative. A snapshot cannot legitimize a
        # replacement data source merely by declaring it in its own inventory.
        snapshot['expected_data_source_ids'] = {
            key: item['data_source_id'] for key, item in state['databases'].items()}
        views = dashboard_views(state['config'])
        groups = state.get('dashboard', {}).get('view_groups', [])
        for key in expected:
            page = snapshot.get('pages', {}).get(key)
            if not isinstance(page, dict):
                raise ValueError(f'{key}: 실제 페이지 증거가 빠졌습니다.')
            bindings = dict(page.get('source_bindings', {}))
            for section in {view['section'] for view in views if view['workspace_page'] == key}:
                matching = [view for view in views if view['workspace_page'] == key and view['section'] == section]
                sources = {view['source'] for view in matching}
                containers = [group['container_id'] for group in groups
                              if group['page'] == key and group['section'] == section]
                if len(sources) != 1 or len(containers) != 1:
                    raise ValueError(f'{key}/{section}: 설치 기록의 원본·연결 DB를 확인할 수 없습니다.')
                bindings[section] = {
                    'data_source_id': state['databases'][next(iter(sources))]['data_source_id'],
                    'database_id': containers[0],
                }
            page['source_bindings'] = bindings
        synced = state.get('dashboard', {}).get('matrix_sync_id')
        if synced:
            snapshot['expected_synced_block_id'] = synced
        if core_verified:
            # Called only after this invocation's remote functional checks pass.
            # The capture file's old/missing core attestation is not the result
            # of that current check, and must not leave a contradictory report.
            snapshot['core_verification'] = {
                'status': 'pass', 'checked_at': datetime.now(timezone.utc).isoformat(),
                'page_ids': dict(expected),
            }
    return verify_layout_snapshot(snapshot, base_dir=source.parent)


def run(args):
    if args.command == 'render-layout':
        sections = json.loads(Path(args.sections).read_text(encoding='utf-8'))
        write_text(args.output, render_page(args.page, sections))
        print('배치 초안 저장: ' + args.output + ' (실제 페이지 반영·검증 전)')
        return 0
    if args.command == 'layout-plan':
        from .model import config
        report = layout_plan(config(args.config))
    else:
        report = load_evidence(args.snapshot)
    content = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    output = getattr(args, 'output', None) or getattr(args, 'report', None)
    if output:
        write_text(output, content)
    print(content, end='')
    return 0 if args.command == 'layout-plan' or report['layout_complete'] else 1
