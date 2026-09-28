"""Local setup and public meal lookup commands; no background scheduler."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from .client import Client
from .install import Journal, locked
from .model import config


def add_commands(commands):
    setup = commands.add_parser('setup-extras', help='기존 수첩에 급식 칸·양식 모음 추가')
    fetch = commands.add_parser('neis-meals', help='NEIS 급식 조회 및 비공개 JSON 저장')
    sync = commands.add_parser('meals-sync', help='NEIS 급식을 홈에 표시')
    for parser in (fetch, sync):
        parser.add_argument('--office-code', default=os.getenv('NEIS_OFFICE_CODE', ''))
        parser.add_argument('--school-code', default=os.getenv('NEIS_SCHOOL_CODE', ''))
        parser.add_argument('--date', help='YYYY-MM-DD; 기본 서울 기준 오늘')
    fetch.add_argument('--output', default='.local/neis-meals.json')
    for parser in (setup, sync):
        parser.add_argument('--config', default='.local/config.json')
        parser.add_argument('--state', default='.local/state.json')
        parser.add_argument('--apply', action='store_true')
    sync.add_argument('--watch', action='store_true')
    sync.add_argument('--interval', type=int, default=21600, help='기본 6시간, 최소 3600초; 자정에도 갱신')


def _installation(client, path, c):
    j = Journal(path, client)
    j.ready()
    if not j.data.get('complete') or j.data.get('config') != c:
        raise ValueError('완료한 수첩의 동일한 config와 state를 사용하세요.')
    if client.request('GET', '/users/me')['id'] != j.data.get('identity'):
        raise ValueError('설치 당시의 Notion 연결이 아닙니다.')
    return j


def watch_delay(interval, shown_date=None):
    now = datetime.now(ZoneInfo('Asia/Seoul'))
    if shown_date is not None and shown_date != now.date().isoformat():
        # A request or Notion write can cross midnight after the provider chose
        # its date. Fetch the new day promptly instead of sleeping six hours.
        return 1
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
    return min(interval, max(1, (midnight - now).total_seconds()))


def run(args):
    if args.command == 'setup-extras':
        from .extras import install_extras
        from .forms import selected_forms
        c = config(args.config)
        if not args.apply:
            print(json.dumps({'forms': [f['title'] for f in selected_forms(c)], 'meal_block': True, 'applied': False}, ensure_ascii=False, indent=2))
            return 0
        with locked(args.state):
            client = Client(os.getenv('NOTION_TOKEN'))
            j = _installation(client, args.state, c)
            install_extras(j, c, j.data['objects']['root']['id'], j.data['databases'])
        print('홈 급식 칸과 선택한 양식 모음을 준비했습니다. 실제 급식 조회는 meals-sync로 실행하세요.')
        return 0
    from .cli import private_json
    from .neis_meals import fetch_meals
    from .meals import update_meals
    if not args.office_code or not args.school_code:
        raise ValueError('NEIS 교육청 코드·표준학교코드가 필요합니다.')
    key = os.getenv('NEIS_API_KEY', '')
    if not key.strip() or key.strip().lower() in ('sample', 'sample key', 'sample_key'):
        raise ValueError('발급받은 NEIS_API_KEY 환경 변수가 필요합니다.')
    if args.command == 'neis-meals':
        output = Path(args.output).resolve()
        if not any(part in ('.local', 'private') for part in output.parts[2:-1]):
            raise ValueError('급식 출력은 .local/ 또는 private/ 아래에 저장하세요.')
        c = None
    else:
        if args.watch and (not args.apply or args.date):
            raise ValueError('--watch에는 --apply가 필요하고 --date는 함께 사용할 수 없습니다.')
        if args.interval < 3600:
            raise ValueError('급식 조회 간격은 최소 3600초입니다.')
        c = config(args.config)
    while True:
        snapshot = fetch_meals(args.office_code, args.school_code, args.date, api_key=key)
        summary = {'date': snapshot['date'], 'meal_count': len(snapshot['rows']), 'applied': False}
        if args.command == 'neis-meals':
            private_json(args.output, snapshot)
            print(json.dumps({**summary, 'output': args.output}, ensure_ascii=False, indent=2))
            return 0
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        with locked(args.state):
            client = Client(os.getenv('NOTION_TOKEN'))
            _installation(client, args.state, c)
            count = update_meals(client, args.state, snapshot)
        print(f"{snapshot['date']}: 급식 {len(snapshot['rows'])}건 확인, 홈 {count}개 블록 반영.", flush=True)
        if not args.watch:
            return 0
        sleep(watch_delay(args.interval, snapshot['date']))
