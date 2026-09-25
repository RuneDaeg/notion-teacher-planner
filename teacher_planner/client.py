"""Small standard-library Notion client. Ambiguous writes are never retried."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NotionError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, token, version='2026-03-11', opener=None, sleep=time.sleep):
        if not token:
            raise ValueError('NOTION_TOKEN 환경 변수가 필요합니다. 채팅에 토큰을 붙여 넣지 마세요.')
        self.token, self.version = token, version
        self.opener = opener or build_opener(NoRedirect())
        self.sleep = sleep

    def request(self, method, path, payload=None):
        if not path.startswith('/') or '://' in path:
            raise ValueError('Notion API 상대 경로만 허용합니다.')
        for attempt in range(4):
            self.sleep(0.35)
            req = Request('https://api.notion.com/v1' + path,
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Notion-Version': self.version, 'Content-Type': 'application/json'}, method=method)
            try:
                with self.opener.open(req, timeout=30) as response:
                    return json.load(response)
            except HTTPError as e:
                if e.code == 429 and attempt < 3:
                    try:
                        delay = float(e.headers.get('Retry-After', '2'))
                    except ValueError:
                        delay = 2
                    if delay > 60:
                        raise NotionError('요청 제한: 60초 이후 다시 실행하세요.', 429) from None
                    self.sleep(max(1, delay))
                    continue
                if method == 'GET' and e.code >= 500 and attempt < 3:
                    self.sleep(2 ** attempt)
                    continue
                # Do not echo API body: it can contain record text or credentials.
                hint = {401: '토큰을 확인하세요.', 403: '연결의 읽기·쓰기 권한을 확인하세요.',
                        404: '페이지 공유와 ID를 확인하세요.', 400: '속성·뷰 설정을 공식 문서와 대조하세요.'}.get(e.code, '네트워크 또는 서비스 상태를 확인하세요.')
                raise NotionError(f'Notion HTTP {e.code}. {hint}', e.code) from None
            except (URLError, TimeoutError, OSError, ValueError):
                raise NotionError('Notion 응답을 확정할 수 없습니다. 생성 요청은 자동 재시도하지 않습니다.') from None

    def pages(self, ds, filter_=None):
        cursor = None
        while True:
            body = {'page_size': 100}
            if filter_:
                body['filter'] = filter_
            if cursor:
                body['start_cursor'] = cursor
            r = self.request('POST', f'/data_sources/{ds}/query', body)
            yield from r['results']
            if not r.get('has_more'):
                return
            cursor = r['next_cursor']
