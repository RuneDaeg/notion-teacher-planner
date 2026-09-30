"""Bounded daily dispatch, shared public snapshots and lease-protected workers."""
import hashlib
import json
import re
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

from teacher_planner.client import Client, NotionError
from teacher_planner.neis import fetch_schedule, NeisError
from teacher_planner.neis_meals import fetch_meals, NeisMealsError
from teacher_planner.cloud_registration import enrollment_url
from teacher_planner.cloud_sync import sync_once, validate_targets
from .security import TokenBox, ConnectionError, RetryableConnectionError, exchange
from .store import BusyError

REGION = 'asia-northeast3'


def today():
    return datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()


def task_payload(identifier, day):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier):
        raise ValueError('갱신 대상이 유효하지 않습니다.')
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('갱신 날짜가 유효하지 않습니다.')
    return {'installation_id': identifier, 'day': day}


def enqueue(identifier, day=None, checkpoint='start', delay=0):
    from firebase_admin import functions, exceptions
    data = task_payload(identifier, day or today())
    task_id = hashlib.sha256((identifier + data['day'] + checkpoint).encode()).hexdigest()
    queue = functions.task_queue(f'locations/{REGION}/functions/planner_sync_task')
    try:
        return queue.enqueue({'data': data}, functions.TaskOptions(
            task_id=task_id, schedule_delay_seconds=delay, dispatch_deadline_seconds=540))
    except exceptions.AlreadyExistsError:
        return task_id


def dispatch_daily(store, enqueue_fn=enqueue):
    count = 0
    day = today()
    for identifier, doc in store.active():
        if doc.get('last_success_date') != day:
            # Deterministic spread across an hour; one school is fetched once and
            # each installation is idempotent even if Scheduler redelivers.
            enqueue_fn(identifier, day=day, delay=int(identifier[:8], 16) % 3600)
            count += 1
    return count


def snapshots(store, manifest, day, api_key):
    identity = ':'.join(str(manifest[k]) for k in ('office_code', 'school_code', 'academic_year'))
    key = hashlib.sha256((identity + ':' + day).encode()).hexdigest()
    cached = store.cache(key)
    if cached:
        return cached
    calendar = fetch_schedule(manifest['office_code'], manifest['school_code'],
                              manifest['academic_year'], api_key=api_key)
    meals = fetch_meals(manifest['office_code'], manifest['school_code'], day, api_key=api_key)
    data = {'calendar': calendar, 'meals': meals}
    store.cache_put(key, data)
    return data


def status_block(client, manifest, message, public_url):
    body = '자동 갱신 · 매일 오전 7시부터 순차 실행 (한국 시간)\n' + message
    client.request('PATCH', '/blocks/' + manifest['status_block_id'], {'callout': {'rich_text': [
        {'type': 'text', 'text': {'content': body}},
        {'type': 'text', 'text': {'content': '\n자동 갱신 연결·관리',
                                'link': {'url': enrollment_url(public_url, manifest)}}}]}})


def run_task(store, data, *, api_key, encryption_key, client_id, client_secret, public_url,
             enqueue_fn=enqueue, snapshot_fn=snapshots, client_factory=Client,
             exchange_fn=exchange, sync_fn=sync_once, validator=validate_targets):
    if not isinstance(data, dict) or set(data) != {'installation_id', 'day'}:
        raise ValueError('갱신 요청을 확인하세요.')
    task_payload(data['installation_id'], data['day'])
    identifier, day = data['installation_id'], data['day']
    if day != today():
        return {'skipped': 'expired_date'}  # Never label yesterday's meals as today.
    lease = store.claim(identifier, day)
    if not lease:
        return {'skipped': 'disabled_or_done'}
    doc = store.get(identifier)
    manifest = doc['manifest']
    client = None
    verified = False
    try:
        if not date(manifest['academic_year'], 3, 1) <= date.fromisoformat(day) < date(manifest['academic_year'] + 1, 3, 1):
            store.fenced(identifier, lease, {'status': 'academic_year_ended', 'enabled': False}, release=True)
            return {'skipped': 'academic_year_ended'}
        box = TokenBox(encryption_key)
        credentials = box.open('notion', doc['credentials'])
        # Rotation is done under the installation lease; persist both new tokens
        # immediately. A lost rotation response requires re-authorization.
        rotated = exchange_fn(client_id, client_secret, {'grant_type': 'refresh_token',
                              'refresh_token': credentials['refresh_token']})
        store.fenced(identifier, lease, {'credentials': box.seal('notion', {
            key: rotated[key] for key in ('access_token', 'refresh_token')})})
        client = client_factory(rotated['access_token'])
        validator(client, manifest)
        verified = True
        source = snapshot_fn(store, manifest, day, api_key)
        state = store.state(identifier)
        def save(value):
            store.fenced(identifier, lease, state=value)
        result = sync_fn(client, manifest, source['calendar'], source['meals'], state, save, max_events=8)
        if result['complete']:
            stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
            status_block(client, manifest, '마지막 갱신 완료: ' + stamp + '\n급식·학사일정 반영 완료', public_url)
            store.fenced(identifier, lease, {'status': 'active', 'last_success_date': day,
                                             'last_success_at': time.time(), 'last_error': None}, release=True)
        else:
            checkpoint = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
            enqueue_fn(identifier, day=day, checkpoint=checkpoint, delay=10)
            store.fenced(identifier, lease, {'status': 'waiting'}, release=True)
        return result
    except (NeisError, NeisMealsError, NotionError, RetryableConnectionError) as exc:
        # Provider downtime is retried by Cloud Tasks. Keep the previous visible
        # content and timestamp; do not replace it with a fabricated empty result.
        permanent = isinstance(exc, NotionError) and exc.status in (401, 403, 404)
        store.fenced(identifier, lease, {'status': 'reconnect' if permanent else 'retrying',
                                         'enabled': not permanent, 'last_error': 'provider_error'}, release=True)
        if permanent:
            return {'complete': False, 'attention': 'reconnect'}
        raise RuntimeError('외부 서비스 조회/반영에 실패했습니다. 재시도합니다.') from None
    except (ConnectionError, ValueError):
        if verified:
            try:
                status_block(client, manifest, '갱신을 중단했습니다. 연결·원본 자료 확인이 필요합니다.\n'
                             '마지막으로 반영된 내용을 유지합니다.', public_url)
            except Exception:
                pass
        store.fenced(identifier, lease, {'status': 'attention', 'enabled': False,
                                         'last_error': 'validation_or_auth'}, release=True)
        return {'complete': False, 'attention': 'validation_or_auth'}
    except Exception:
        try:
            store.fenced(identifier, lease, {'status': 'retrying', 'last_error': 'temporary_error'}, release=True)
        except BusyError:
            pass
        raise RuntimeError('갱신을 완료하지 못했습니다. 다시 시도합니다.') from None
