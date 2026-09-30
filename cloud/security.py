"""Scoped encrypted OAuth state, browser sessions and Notion credentials."""
import base64
import json
import secrets
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from cryptography.fernet import Fernet, InvalidToken
from teacher_planner.client import NoRedirect


class ConnectionError(ValueError):
    pass


class RetryableConnectionError(RuntimeError):
    pass


class TokenBox:
    def __init__(self, key):
        self.cipher = Fernet(key.encode() if isinstance(key, str) else key)

    def seal(self, purpose, payload):
        return self.cipher.encrypt(json.dumps({'purpose': purpose, 'payload': payload},
                                             separators=(',', ':')).encode()).decode()

    def open(self, purpose, token, ttl=None):
        try:
            if not isinstance(token, str) or len(token) > 16000:
                raise ValueError()
            obj = json.loads(self.cipher.decrypt(token.encode(), ttl=ttl))
            if obj['purpose'] != purpose or not isinstance(obj['payload'], dict):
                raise ValueError()
            return obj['payload']
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ConnectionError('연결이 만료되었거나 유효하지 않습니다. 수첩의 연결 링크를 다시 여세요.') from None


def origin(value):
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.path not in ('', '/') or parts.query or parts.fragment
            or parts.port not in (None, 443)):
        raise ValueError('PUBLIC_BASE_URL은 HTTPS 서비스 원점 주소여야 합니다.')
    host = parts.hostname.lower()
    return 'https://' + ('[' + host + ']' if ':' in host else host)


def authorization_url(client_id, redirect_uri, state):
    return 'https://api.notion.com/v1/oauth/authorize?' + urlencode({
        'owner': 'user', 'client_id': client_id, 'response_type': 'code',
        'redirect_uri': redirect_uri, 'state': state})


def exchange(client_id, client_secret, payload, opener=None):
    """No redirects, URL/body logging or automatic retries of token rotation."""
    basic = base64.b64encode((client_id + ':' + client_secret).encode()).decode()
    req = Request('https://api.notion.com/v1/oauth/token',
                  data=json.dumps(payload).encode(), method='POST',
                  headers={'Authorization': 'Basic ' + basic, 'Content-Type': 'application/json'})
    try:
        with (opener or build_opener(NoRedirect())).open(req, timeout=25) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError()
            result = json.loads(body)
        for field in ('access_token', 'refresh_token'):
            if not isinstance(result.get(field), str) or not 1 <= len(result[field]) <= 4096:
                raise ValueError()
        return result
    except HTTPError as error:
        if error.code == 429 or 500 <= error.code < 600:
            raise RetryableConnectionError('Notion 인증 서버에 일시적인 오류가 있습니다.') from None
        raise ConnectionError('Notion 인증을 완료하지 못했습니다. 다시 연결해 주세요.') from None
    except Exception:
        raise ConnectionError('Notion 인증을 완료하지 못했습니다. 다시 연결해 주세요.') from None


def nonce():
    return secrets.token_urlsafe(32)
