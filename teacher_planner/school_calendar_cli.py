"""Opt-in public NEIS calendar fetching and foreground synchronization."""
import json
import os
from datetime import datetime
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from .client import Client
from .install import Journal, fingerprint, locked
from .model import config


def add_commands(commands):
    fetch = commands.add_parser('neis-calendar', help='나이스 공개 학사일정 조회·비공개 파일 저장')
    sync = commands.add_parser('neis-sync', help='나이스 공개 학사일정 검토 및 기존 Notion 캘린더 반영')
    for command in (fetch, sync):
        command.add_argument('--office-code', default=os.getenv('NEIS_OFFICE_CODE', ''), help='시도교육청 코드; NEIS_OFFICE_CODE')
        command.add_argument('--school-code', default=os.getenv('NEIS_SCHOOL_CODE', ''), help='나이스 표준학교 코드; NEIS_SCHOOL_CODE (컴시간 코드와 다름)')
        command.add_argument('--from', dest='start', help='시작일 YYYY-MM-DD; 기본 학년도 3월 1일')
        command.add_argument('--to', dest='end', help='마지막 포함일 YYYY-MM-DD; 기본 다음 해 2월 말')
    fetch.add_argument('--year', type=int, help='학년도; 기본 서울 기준 현재 학년도')
    fetch.add_argument('--output', default='.local/neis-calendar.json', help='.local/ 또는 private/ 아래 JSON 경로')
    sync.add_argument('--config', default='.local/config.json')
    sync.add_argument('--state', default='.local/state.json')
    sync.add_argument('--apply', action='store_true')
    sync.add_argument('--merge-existing', action='store_true', help='기존 일별 행사 페이지를 기간으로 통합하고 중복 페이지는 보관; 전체 학년도 조회 필요')
    sync.add_argument('--watch', action='store_true', help='터미널 프로세스에서 반복 갱신; --apply 필요')
    sync.add_argument('--interval', type=int, default=21600, help='반복 간격 초; 최소 3600, 기본 21600 (6시간)')


def current_academic_year():
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    return today.year if today.month >= 3 else today.year - 1


def _state(j, c):
    j.ready()
    if not j.data.get('complete') or j.data.get('config') != c:
        raise ValueError('설치를 완료하고 동일한 config와 state를 사용하세요.')


def run(args):
    from .cli import private_json
    from .neis import fetch_schedule
    from .school_calendar import changes, apply_changes, group_events

    if not args.office_code or not args.school_code:
        raise ValueError('나이스 교육청 코드와 표준학교 코드를 입력하세요. 컴시간 학교 코드와 다릅니다.')
    api_key = os.getenv('NEIS_API_KEY', '')
    if not api_key.strip() or api_key.strip().lower() in ('sample', 'sample key', 'sample_key'):
        raise ValueError('전체 학사일정 조회에는 NEIS_API_KEY 환경 변수가 필요합니다. 샘플 응답은 전체 일정으로 사용하지 않습니다.')
    if args.command == 'neis-calendar':
        output = Path(args.output).resolve()
        if not any(part in ('.local', 'private') for part in output.parts[2:-1]):
            raise ValueError('학사일정 출력은 .local/ 또는 private/ 아래에 저장하세요.')
        year = args.year if args.year is not None else current_academic_year()
        c = None
    else:
        if args.watch and not args.apply:
            raise ValueError('--watch에는 --apply가 필요합니다.')
        if args.interval < 3600:
            raise ValueError('학사일정 조회 간격은 최소 3600초여야 합니다.')
        c = config(args.config)
        year = c['academic_year']
    while True:
        snapshot = fetch_schedule(args.office_code, args.school_code, year,
                                  api_key=api_key, start=args.start, end=args.end)
        event_count = len(group_events(snapshot['rows']))
        summary = {'academic_year': year, 'start': snapshot['start'], 'end': snapshot['end'],
                   'event_count': event_count, 'daily_record_count': len(snapshot['rows']), 'applied': False}
        if args.command == 'neis-calendar':
            private_json(args.output, snapshot)
            print(json.dumps({**summary, 'output': args.output}, ensure_ascii=False, indent=2))
            return 0
        if not args.apply:
            print(json.dumps({**summary, 'behavior': '조회만 완료했습니다. 같은 행사명·학교 과정·주야 과정의 연속된 날짜를 한 기간으로 표시합니다. --apply로 반영하며 기존 일별 페이지 통합에는 --merge-existing이 필요합니다. 누락·이름 변경은 자동 삭제하지 않으며 기존 기간의 축소·분리는 확인 후 처리합니다.'}, ensure_ascii=False, indent=2))
            return 0
        with locked(args.state):
            client = Client(os.getenv('NOTION_TOKEN'))
            j = Journal(args.state, client)
            _state(j, c)
            if client.request('GET', '/users/me')['id'] != j.data.get('identity'):
                raise ValueError('설치 당시의 Notion 연결이 아닙니다.')
            source = {'office_code': snapshot['office_code'], 'school_code': snapshot['school_code'],
                      'academic_year': snapshot['academic_year']}
            binding = fingerprint(source)
            if any(j.data.get(field) and j.data[field] != binding for field in ('neis_source', 'neis_meals_source')):
                raise ValueError('기존 학사일정 연결과 학교·교육청·학년도가 다릅니다. 학교와 설치 상태를 확인하세요.')
            ops = changes(client, c, j.data, snapshot, merge_existing=args.merge_existing)
            if ops and not j.data.get('neis_source'):
                j.data['neis_source'] = binding
                j.save()
            count = apply_changes(client, args.state, ops)
            # Reload after journaled creations so their IDs and block ownership survive.
            latest = Journal(args.state, client)
            latest.data['neis_last_checked_at'] = datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
            latest.data['neis_last_range'] = {'start': snapshot['start'], 'end': snapshot['end'],
                                             'event_count': event_count, 'daily_record_count': len(snapshot['rows'])}
            latest.save()
        print(f"{snapshot['start']} ~ {snapshot['end']}: 일별 {len(snapshot['rows'])}건 → 기간 행사 {event_count}건, {count}건 반영. 기존 주간·월간 캘린더에서 확인하세요.", flush=True)
        if not args.watch:
            return 0
        sleep(args.interval)
