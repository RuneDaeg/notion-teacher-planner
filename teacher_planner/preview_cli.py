"""Read-only preview verification and private Notion layout handoff files."""
import json
import os
from pathlib import Path
import tempfile

from .preview_bundle import compile_bundle, read_json, validate_bundle


def add_commands(commands):
    verify = commands.add_parser('verify-preview', help='미리보기 설계·선택·지문 검사 (Notion 변경 없음)')
    verify.add_argument('--bundle', required=True)
    compile_ = commands.add_parser('compile-preview', help='검토한 설계와 실제 구역으로 네 페이지 반영안 생성')
    compile_.add_argument('--bundle', required=True)
    compile_.add_argument('--sections', required=True, help='실제 조회한 구역: {pages:{home:{구역:본문},...}}')
    compile_.add_argument('--output-dir', required=True, help='비공개 반영안 저장 폴더')


def _write_private(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(args):
    bundle = validate_bundle(read_json(args.bundle))
    report = {
        'bundle_valid': True, 'bundle_digest': bundle['bundle_digest'],
        'source_digest': bundle['source_digest'], 'applied': False,
        'layout_complete': False, 'live_notion_verified': False,
    }
    if args.command == 'compile-preview':
        rendered = compile_bundle(bundle, read_json(args.sections))
        target = Path(args.output_dir)
        for key, content in rendered.items():
            _write_private(target / (key + '.md'), content)
        _write_private(target / 'plan.json', json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
        instructions = (
            '검토한 교무수첩 배치 반영안\n'
            '설계 지문: ' + bundle['bundle_digest'] + '\n\n'
            '이 파일들은 로컬 변환 결과입니다. Notion 반영·실제 조회·접근 권한·UI 재현 검증은 하지 않았습니다.\n'
            '1. 대상 수첩과 기존 설치 경로를 확인하고 현재 페이지·블록·원본 DB·뷰·동기화 블록 ID를 비공개로 기록하세요.\n'
            '2. sections 입력의 원본과 최신 페이지를 대조하세요. 사용자 편집이나 미배치 구역이 있으면 합치기 전 변경 범위를 확인하세요.\n'
            '3. 네 Markdown을 전체 페이지 덮어쓰기 명령으로 사용하지 마세요. 기존 원본과 ID를 유지하며 요청한 구역만 MCP/UI로 이동·수정하세요.\n'
            '4. 전체 너비 켜기·작은 텍스트 끄기·상대 열 폭·닫힌 접기는 실제 Notion에서 적용·확인하세요. 도구가 미지원하면 확인 대기로 남기세요.\n'
            '5. 시간표 원본과 홈 동기화 참조를 유지하고 수업을 업무·일정에 복사하지 마세요. 주간·월간 캘린더는 같은 원본의 보기입니다.\n'
            '6. 변경 후 실제 네 페이지와 원본·보기·ID를 재조회하고 화면 증거의 reviewed_bundle_digest에 위 설계 지문을 기록하세요. '
            'verify-layout --snapshot 실제증거.json --reviewed-bundle plan.json으로 검사하세요. 로컬 검사는 실제 설치 완료를 뜻하지 않습니다.\n'
            '자세한 절차: START_HERE.md, docs/PREVIEW.md, docs/ITERATIVE_EDITING.md, docs/ACCEPTANCE.md\n'
        )
        _write_private(target / 'apply-instructions.txt', instructions)
        report['compiled_pages'] = list(rendered)
        report['message'] = '반영안 파일을 저장했습니다. 실제 Notion 적용·검증 전입니다.'
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
