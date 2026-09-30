"""Teacher-facing OAuth enrollment. School inputs come from the installer."""
import hmac
from flask import Flask, jsonify, redirect, request

from teacher_planner.cloud_registration import validate_manifest
from teacher_planner.cloud_sync import validate_targets
from teacher_planner.client import Client, NotionError
from .security import (TokenBox, ConnectionError, RetryableConnectionError,
                       authorization_url, exchange, nonce, origin)
from .store import BusyError

# Firebase Hosting forwards only __session cookies to rewritten Cloud Functions.
COOKIE = '__session'


def create_app(*, store, enqueue, public_url, client_id, client_secret, encryption_key,
               exchange_fn=exchange, client_factory=Client, target_validator=validate_targets):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 12000
    base = origin(public_url)
    box = TokenBox(encryption_key)
    callback = base + '/api/callback'

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(NotionError)
    def invalid(_error):
        # Never expose API response bodies, credentials, IDs or callback parameters.
        return jsonify(error='연결을 확인하지 못했습니다. 설치 정보와 Notion 접근 허용을 확인하고 다시 시도하세요.'), 400

    @app.errorhandler(BusyError)
    def busy(_error):
        return jsonify(error='갱신 중입니다. 잠시 후 다시 시도해 주세요.'), 409

    @app.errorhandler(RetryableConnectionError)
    def unavailable(_error):
        return jsonify(error='Notion 인증 서버에 일시적인 오류가 있습니다. 수첩의 연결 링크에서 잠시 후 다시 시도해 주세요.'), 503

    def same_origin():
        if request.headers.get('Origin') != base:
            raise ConnectionError('요청 출처가 다릅니다.')

    def session():
        data = box.open('browser', request.cookies.get(COOKIE), ttl=3600)
        doc = store.get(data['installation_id'])
        if not doc or doc['owner_id'] != data['owner_id']:
            raise ConnectionError('연결이 없습니다.')
        return data, doc

    @app.post('/api/start')
    def start():
        same_origin()
        manifest = validate_manifest(request.get_json())
        csrf = nonce()
        state = box.seal('oauth', {'manifest': manifest, 'nonce': csrf})
        response = jsonify(authorize_url=authorization_url(client_id, callback, state))
        response.set_cookie(COOKIE, box.seal('pending', {'nonce': csrf}), max_age=600,
                            secure=True, httponly=True, samesite='Lax', path='/')
        return response

    @app.get('/api/callback')
    def complete():
        if request.args.get('error'):
            response = redirect('/connect?result=cancelled')
            response.delete_cookie(COOKIE, path='/')
            return response
        pending = box.open('pending', request.cookies.get(COOKIE), ttl=600)
        state = box.open('oauth', request.args.get('state'), ttl=600)
        if not hmac.compare_digest(pending['nonce'], state['nonce']):
            raise ConnectionError('연결 요청이 다릅니다.')
        code = request.args.get('code')
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise ConnectionError('인증 코드가 없습니다.')
        manifest = validate_manifest(state['manifest'])
        tokens = exchange_fn(client_id, client_secret, {'grant_type': 'authorization_code',
                             'code': code, 'redirect_uri': callback})
        owner = tokens.get('owner', {}).get('user', {}).get('id')
        if (not owner or not tokens.get('workspace_id') or not tokens.get('bot_id')
                or tokens.get('duplicated_template_id')):
            raise ConnectionError('기존 수첩을 선택해 연결해 주세요.')
        target_validator(client_factory(tokens['access_token']), manifest)
        encrypted = box.seal('notion', {key: tokens[key] for key in ('access_token', 'refresh_token')})
        identifier = store.register(manifest, {'owner_id': owner, 'workspace_id': tokens['workspace_id'],
                                              'bot_id': tokens['bot_id']}, encrypted)
        # Registration is durable even if dispatch fails; the daily sweep retries it.
        queued = True
        try:
            # Cloud Tasks remembers completed task IDs. A deliberate reconnect
            # needs a fresh dispatch even after an earlier failure on this date.
            enqueue(identifier, checkpoint='connect:' + nonce())
        except Exception:
            queued = False
        response = redirect('/connect?result=' + ('connected' if queued else 'waiting'))
        response.set_cookie(COOKIE, box.seal('browser', {'installation_id': identifier,
                            'owner_id': owner, 'csrf': nonce()}), max_age=3600,
                            secure=True, httponly=True, samesite='Lax', path='/')
        return response

    @app.get('/api/status')
    def status():
        current, doc = session()
        manifest = doc['manifest']
        return jsonify(school_name=manifest['school_name'], academic_year=manifest['academic_year'],
                       enabled=doc['enabled'], status=doc['status'],
                       last_success_at=doc.get('last_success_at'), schedule='매일 오전 7시 · 한국 시간',
                       csrf=current['csrf'], notion_url='https://www.notion.so/' + manifest['root_page_id'])

    @app.post('/api/enabled')
    def enabled():
        same_origin()
        current, _ = session()
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), current['csrf']):
            raise ConnectionError('요청이 유효하지 않습니다.')
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise ValueError('설정을 확인하세요.')
        store.set_enabled(current['installation_id'], current['owner_id'], body['enabled'])
        if body['enabled']:
            enqueue(current['installation_id'], checkpoint='resume:' + nonce())
        return jsonify(ok=True)

    return app
