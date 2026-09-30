"""Offline integration tests: browser enrollment through the daily worker."""
import copy
import json
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError

from cryptography.fernet import Fernet
from cloud.app import create_app, COOKIE
from cloud.security import TokenBox, ConnectionError, RetryableConnectionError, origin, exchange
from cloud.store import Store, BusyError, bounded, installation_id
from cloud.runtime import run_task, dispatch_daily, snapshots, enqueue
from teacher_planner.client import NotionError
from teacher_planner.neis import NeisError

BASE = 'https://planner.example.org'
MANIFEST = {'version': 1, 'office_code': 'N10', 'school_code': '1234567',
            'school_name': '가상고등학교', 'academic_year': 2026,
            'root_page_id': '11111111-1111-4111-8111-111111111111',
            'agenda_data_source_id': '22222222-2222-4222-8222-222222222222',
            'meals_block_id': '33333333-3333-4333-8333-333333333333',
            'status_block_id': '44444444-4444-4444-8444-444444444444'}
TOKENS = {'access_token': 'fake-access', 'refresh_token': 'fake-refresh',
          'workspace_id': 'workspace', 'bot_id': 'bot', 'owner': {'user': {'id': 'owner'}}}
DAY = '2026-09-30'


class MemoryStore:
    def __init__(self):
        self.docs, self.states, self.caches = {}, {}, {}

    def register(self, manifest, identity, encrypted):
        identifier = installation_id(identity['workspace_id'], manifest['root_page_id'])
        existing = self.docs.get(identifier)
        if existing and (existing['owner_id'] != identity['owner_id'] or existing['manifest'] != manifest):
            raise ValueError()
        self.docs[identifier] = {**(existing or {}), 'manifest': manifest, **identity,
                                 'credentials': encrypted, 'enabled': True, 'status': 'waiting'}
        return identifier

    def get(self, identifier):
        return copy.deepcopy(self.docs.get(identifier))

    def set_enabled(self, identifier, owner, enabled):
        assert self.docs[identifier]['owner_id'] == owner
        self.docs[identifier].update(enabled=enabled, status='waiting' if enabled else 'paused')

    def active(self):
        return ((key, value) for key, value in self.docs.items() if value['enabled'])

    def claim(self, identifier, day):
        doc = self.docs.get(identifier)
        if not doc or not doc['enabled'] or doc.get('last_success_date') == day:
            return None
        if doc.get('lease'):
            raise BusyError()
        doc.update(lease='lease', status='syncing')
        return 'lease'

    def fenced(self, identifier, lease, changes=None, state=None, release=False):
        assert self.docs[identifier]['lease'] == lease
        if state is not None:
            self.states[identifier] = copy.deepcopy(state)
        self.docs[identifier].update(changes or {})
        if release:
            self.docs[identifier]['lease'] = None

    def state(self, identifier):
        return copy.deepcopy(self.states.get(identifier, {}))

    def cache(self, key):
        return self.caches.get(key)

    def cache_put(self, key, data):
        self.caches[key] = bounded(data)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key()
        self.box = TokenBox(self.key)
        self.store = MemoryStore()
        self.queue = Mock()
        self.exchange = Mock(return_value=copy.deepcopy(TOKENS))
        self.validate = Mock()
        self.app = create_app(store=self.store, enqueue=self.queue, public_url=BASE,
                              client_id='fake-client', client_secret='fake-secret', encryption_key=self.key,
                              exchange_fn=self.exchange, client_factory=Mock(), target_validator=self.validate)
        self.browser = self.app.test_client()

    def start(self, browser=None):
        response = (browser or self.browser).post('/api/start', json=MANIFEST,
            headers={'Origin': BASE}, base_url=BASE)
        self.assertEqual(response.status_code, 200)
        self.assertIn('__session=', response.headers['Set-Cookie'])
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('Secure', response.headers['Set-Cookie'])
        self.assertEqual(self.store.docs, {})  # Anonymous start cannot allocate Firestore records.
        params = parse_qs(urlsplit(response.json['authorize_url']).query)
        self.assertEqual(params['redirect_uri'], [BASE + '/api/callback'])
        return params['state'][0]

    def connect(self):
        state = self.start()
        response = self.browser.get('/api/callback', query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/connect?result=connected')
        return next(iter(self.store.docs))

    def test_complete_flow_no_duplicate_school_input(self):
        identifier = self.connect()
        self.validate.assert_called_once()
        self.queue.assert_called_once()
        self.assertEqual(self.queue.call_args.args, (identifier,))
        self.assertTrue(self.queue.call_args.kwargs['checkpoint'].startswith('connect:'))
        doc = self.store.docs[identifier]
        self.assertEqual(doc['manifest'], MANIFEST)
        self.assertNotIn('fake-access', json.dumps(doc))
        self.assertEqual(self.box.open('notion',doc['credentials'])['refresh_token'],'fake-refresh')
        status = self.browser.get('/api/status',base_url=BASE)
        self.assertEqual(status.json['school_name'],'가상고등학교')
        self.assertNotIn('credentials',status.json)
        self.assertEqual(status.headers['Cache-Control'],'private, no-store')

    def test_origin_csrf_and_foreign_browser_rejected(self):
        self.assertEqual(self.browser.post('/api/start',json=MANIFEST,base_url=BASE).status_code,400)
        state = self.start()
        other = self.app.test_client()
        response = other.get('/api/callback',query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.status_code,400)
        self.exchange.assert_not_called()

    def test_nonce_mixup_and_tampered_state_rejected(self):
        state = self.start()
        self.start()  # A second flow replaces the browser nonce.
        response = self.browser.get('/api/callback',query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.status_code,400)
        self.exchange.assert_not_called()

    def test_external_targets_not_registered(self):
        state = self.start()
        self.validate.side_effect = ValueError('outside root')
        response = self.browser.get('/api/callback',query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.store.docs,{})
        self.queue.assert_not_called()

    def test_duplicate_template_rejected(self):
        state=self.start()
        self.exchange.return_value['duplicated_template_id']='new-page'
        response=self.browser.get('/api/callback',query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.store.docs)

    def test_queue_failure_keeps_registration_for_daily_sweep(self):
        state=self.start();self.queue.side_effect=RuntimeError('queue temporarily down')
        response=self.browser.get('/api/callback',query_string={'state':state,'code':'code'},base_url=BASE)
        self.assertEqual(response.location,'/connect?result=waiting')
        self.assertEqual(len(self.store.docs),1)

    def test_pause_requires_own_session_csrf_and_origin(self):
        identifier=self.connect()
        self.assertEqual(self.browser.post('/api/enabled',json={'enabled':False},
            headers={'Origin':BASE},base_url=BASE).status_code,400)
        status=self.browser.get('/api/status',base_url=BASE).json
        response=self.browser.post('/api/enabled',json={'enabled':False},headers={
            'Origin':BASE,'X-CSRF-Token':status['csrf']},base_url=BASE)
        self.assertEqual(response.status_code,200)
        self.assertFalse(self.store.docs[identifier]['enabled'])

    def test_reconnect_and_resume_get_new_dispatch_after_same_day_failure(self):
        identifier = self.connect()
        response = self.browser.post('/api/start', json=MANIFEST,
                                     headers={'Origin': BASE}, base_url=BASE)
        state = parse_qs(urlsplit(response.json['authorize_url']).query)['state'][0]
        response = self.browser.get('/api/callback', query_string={'state':state,'code':'new-code'}, base_url=BASE)
        self.assertEqual(response.status_code, 302)
        csrf = self.browser.get('/api/status', base_url=BASE).json['csrf']
        for enabled in (False, True):
            response = self.browser.post('/api/enabled', json={'enabled':enabled},
                headers={'Origin':BASE,'X-CSRF-Token':csrf}, base_url=BASE)
            self.assertEqual(response.status_code, 200)
        calls = self.queue.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.args == (identifier,) for call in calls))
        self.assertEqual(len({call.kwargs['checkpoint'] for call in calls}), 3)

    def test_transient_oauth_failure_reports_retry_without_registering(self):
        state = self.start()
        self.exchange.side_effect = RetryableConnectionError('provider response must not leak')
        response = self.browser.get('/api/callback', query_string={'state':state,'code':'code'}, base_url=BASE)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('provider response', response.text)
        self.assertEqual(self.store.docs, {})

    def test_token_endpoint_transient_errors_are_distinct_from_uncertain_rotation(self):
        opener = Mock()
        for status in (429, 500, 503):
            opener.open.side_effect = HTTPError('https://api.notion.com/v1/oauth/token', status,
                                                'secret response', {}, None)
            with self.assertRaises(RetryableConnectionError) as result:
                exchange('client', 'secret', {'grant_type':'refresh_token'}, opener=opener)
            self.assertNotIn('secret', str(result.exception))
        for error in (HTTPError('https://api.notion.com/v1/oauth/token', 400, 'secret', {}, None),
                      URLError('response lost')):
            opener.open.side_effect = error
            with self.assertRaises(ConnectionError):
                exchange('client', 'secret', {'grant_type':'refresh_token'}, opener=opener)

    def test_secrets_not_accepted_in_manifest(self):
        response=self.browser.post('/api/start',json={**MANIFEST,'access_token':'SECRET'},
            headers={'Origin':BASE},base_url=BASE)
        self.assertEqual(response.status_code,400)
        self.assertNotIn('SECRET',response.text)

    def test_expired_or_wrong_purpose_tokens_fail(self):
        token=self.box.cipher.encrypt_at_time(json.dumps({'purpose':'oauth','payload':{}}).encode(),int(time.time())-601).decode()
        with self.assertRaises(ConnectionError):self.box.open('oauth',token,ttl=600)
        with self.assertRaises(ConnectionError):self.box.open('browser',self.box.seal('notion',{}))

    def test_default_https_port_matches_browser_origin(self):
        self.assertEqual(origin(BASE + ':443'), BASE)
        self.assertEqual(origin(BASE + '/'), BASE)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.key=Fernet.generate_key();self.store=MemoryStore()
        self.identifier=self.store.register(MANIFEST,{'owner_id':'owner','workspace_id':'workspace','bot_id':'bot'},
            TokenBox(self.key).seal('notion',{'access_token':'old','refresh_token':'old-refresh'}))
        self.client=Mock();self.sync=Mock(return_value={'complete':True});self.queue=Mock()
        self.rotation=Mock(return_value={'access_token':'new','refresh_token':'new-refresh'})
        self.kwargs=dict(api_key='fake-key',encryption_key=self.key,client_id='client',client_secret='secret',
            public_url=BASE,enqueue_fn=self.queue, snapshot_fn=Mock(return_value={'calendar':{},'meals':{}}),
            client_factory=Mock(return_value=self.client), exchange_fn=self.rotation,
            sync_fn=self.sync,validator=Mock())
        self.clock=patch('cloud.runtime.today',return_value=DAY);self.clock.start();self.addCleanup(self.clock.stop)

    def run_task(self):return run_task(self.store,{'installation_id':self.identifier,'day':DAY},**self.kwargs)

    def test_success_skips_second_delivery_same_day(self):
        self.assertTrue(self.run_task()['complete'])
        self.assertEqual(self.store.docs[self.identifier]['last_success_date'],DAY)
        self.assertEqual(self.run_task()['skipped'],'disabled_or_done')
        self.sync.assert_called_once();self.rotation.assert_called_once()

    def test_rotated_tokens_saved_before_sync(self):
        def check(*args,**kwargs):
            self.assertEqual(TokenBox(self.key).open('notion',self.store.docs[self.identifier]['credentials'])['refresh_token'],'new-refresh')
            return {'complete':True}
        self.sync.side_effect=check;self.run_task()

    def test_continuation_no_success_before_all_chunks(self):
        self.sync.return_value={'complete':False}
        self.run_task()
        self.assertNotIn('last_success_date',self.store.docs[self.identifier])
        self.queue.assert_called_once()
        self.assertEqual(self.queue.call_args.kwargs['day'],DAY)
        self.assertEqual(self.queue.call_args.kwargs['delay'],10)

    def test_stale_or_paused_task_does_not_fetch(self):
        self.assertEqual(run_task(self.store,{'installation_id':self.identifier,'day':'2026-09-29'},**self.kwargs),{'skipped':'expired_date'})
        self.store.docs[self.identifier]['enabled']=False
        self.run_task();self.kwargs['snapshot_fn'].assert_not_called()

    def test_provider_failure_keeps_last_success_and_retries(self):
        self.store.docs[self.identifier]['last_success_date']='2026-09-29'
        self.kwargs['snapshot_fn'].side_effect=NeisError('down')
        with self.assertRaises(RuntimeError):self.run_task()
        self.assertEqual(self.store.docs[self.identifier]['last_success_date'],'2026-09-29')
        self.assertEqual(self.store.docs[self.identifier]['status'],'retrying')
        self.client.request.assert_not_called()

    def test_transient_token_endpoint_failure_retries_then_completes(self):
        before = self.store.docs[self.identifier]['credentials']
        self.rotation.side_effect = RetryableConnectionError('temporary')
        with self.assertRaises(RuntimeError):
            self.run_task()
        doc = self.store.docs[self.identifier]
        self.assertTrue(doc['enabled'])
        self.assertEqual(doc['status'], 'retrying')
        self.assertEqual(doc['credentials'], before)
        self.sync.assert_not_called()
        self.rotation.side_effect = None
        self.assertTrue(self.run_task()['complete'])

    def test_lost_rotation_response_requires_reconnect_and_preserves_data(self):
        self.rotation.side_effect = ConnectionError('response lost')
        self.run_task()
        self.assertFalse(self.store.docs[self.identifier]['enabled'])
        self.assertNotIn('last_success_date', self.store.docs[self.identifier])
        self.sync.assert_not_called()

    def test_uncertain_or_invalid_sync_pauses_instead_of_repeated_creation(self):
        self.sync.side_effect=ValueError('ambiguous pending create')
        self.run_task()
        self.assertFalse(self.store.docs[self.identifier]['enabled'])
        self.assertEqual(self.store.docs[self.identifier]['status'],'attention')

    def test_revoke_disables_until_reconnect(self):
        self.kwargs['validator'].side_effect=NotionError('missing',403)
        self.run_task();self.assertFalse(self.store.docs[self.identifier]['enabled'])
        self.assertEqual(self.store.docs[self.identifier]['status'],'reconnect')

    def test_school_cache_shared_across_teachers(self):
        with patch('cloud.runtime.fetch_schedule',return_value={'source':'neis'}) as calendar, \
             patch('cloud.runtime.fetch_meals',return_value={'source':'neis-meals'}) as meals:
            snapshots(self.store,MANIFEST,DAY,'fake-key')
            other={**MANIFEST,'root_page_id':'55555555-5555-4555-8555-555555555555'}
            snapshots(self.store,other,DAY,'fake-key')
            calendar.assert_called_once();meals.assert_called_once()

    def test_schedule_skips_paused_and_done_spreads_tasks(self):
        dispatch_daily(self.store,self.queue)
        self.assertEqual(self.queue.call_args.kwargs['day'],DAY)
        self.assertLess(self.queue.call_args.kwargs['delay'],3600)
        self.queue.reset_mock();self.store.docs[self.identifier]['last_success_date']=DAY
        dispatch_daily(self.store,self.queue);self.queue.assert_not_called()

    def test_named_tasks_are_stable_and_payload_has_exact_sdk_envelope(self):
        with patch('firebase_admin.functions.task_queue') as task_queue:
            enqueue(self.identifier,DAY);first=task_queue.return_value.enqueue.call_args
            enqueue(self.identifier,DAY);second=task_queue.return_value.enqueue.call_args
            self.assertEqual(first.args[1].task_id,second.args[1].task_id)
            self.assertEqual(first.args[0],{'data':{'installation_id':self.identifier,'day':DAY}})
            self.assertEqual(first.args[1].dispatch_deadline_seconds,540)

    def test_schedule_manifest_and_task_limits(self):
        import main
        trigger=main.planner_daily.__firebase_endpoint__.scheduleTrigger
        self.assertEqual(trigger['schedule'],'0 7 * * *')
        self.assertEqual(trigger['timeZone'],'Asia/Seoul')
        queue=main.planner_sync_task.__firebase_endpoint__.taskQueueTrigger
        self.assertEqual(queue['rateLimits']['maxConcurrentDispatches'],1)
        self.assertEqual(main.planner_sync_task.__firebase_endpoint__.maxInstances,1)


if __name__=='__main__':unittest.main()
