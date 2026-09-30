"""Server-only Firestore persistence. Every worker write is fenced by its lease."""
import hashlib
import json
import secrets
import time
from firebase_admin import firestore


class BusyError(RuntimeError):
    pass


def installation_id(workspace, root):
    return hashlib.sha256((workspace + ':' + root).encode()).hexdigest()


def bounded(value):
    # Firestore documents are at most 1 MiB. Reserve space for field encoding.
    if len(json.dumps(value, ensure_ascii=False).encode()) > 800_000:
        raise ValueError('동기화 자료가 저장 한도를 넘었습니다. 운영자가 확인해야 합니다.')
    return value


class Store:
    def __init__(self, db):
        self.db = db

    def ref(self, identifier):
        return self.db.collection('planner_installations').document(identifier)

    def get(self, identifier):
        return self.ref(identifier).get().to_dict()

    def register(self, manifest, identity, encrypted):
        identifier = installation_id(identity['workspace_id'], manifest['root_page_id'])
        ref = self.ref(identifier)

        @firestore.transactional
        def register_tx(tx):
            previous = ref.get(transaction=tx).to_dict()
            if previous:
                if (previous['owner_id'] != identity['owner_id'] or previous['manifest'] != manifest):
                    raise ValueError('이미 연결된 수첩의 소유자나 설정이 다릅니다. 기존 연결을 확인하세요.')
                if previous.get('lock_until', 0) > time.time():
                    raise BusyError('갱신 중입니다. 잠시 후 다시 연결해 주세요.')
            data = {'manifest': manifest, **identity, 'credentials': encrypted,
                    'enabled': True, 'status': 'waiting', 'updated_at': time.time()}
            if not previous:
                data.update(created_at=time.time(), last_success_date=None, last_success_at=None)
            tx.set(ref, data, merge=True)
        register_tx(self.db.transaction())
        return identifier

    def set_enabled(self, identifier, owner, enabled):
        ref = self.ref(identifier)

        @firestore.transactional
        def change(tx):
            doc = ref.get(transaction=tx).to_dict()
            if not doc or doc['owner_id'] != owner:
                raise ValueError('연결을 확인하세요.')
            if doc.get('lock_until', 0) > time.time():
                raise BusyError('현재 갱신 중입니다. 잠시 후 다시 시도해 주세요.')
            tx.update(ref, {'enabled': enabled, 'status': 'waiting' if enabled else 'paused'})
        change(self.db.transaction())

    def active(self):
        from google.cloud.firestore_v1.base_query import FieldFilter
        for doc in self.db.collection('planner_installations').where(
                filter=FieldFilter('enabled', '==', True)).stream():
            yield doc.id, doc.to_dict()

    def claim(self, identifier, day):
        ref = self.ref(identifier)

        @firestore.transactional
        def claim_tx(tx):
            doc = ref.get(transaction=tx).to_dict()
            if not doc or not doc.get('enabled') or doc.get('last_success_date') == day:
                return None
            if doc.get('lock_until', 0) > time.time():
                raise BusyError('이미 갱신 중입니다.')
            lease = secrets.token_hex(24)
            tx.update(ref, {'lease': lease, 'lock_until': time.time() + 900,
                            'status': 'syncing', 'last_attempt_at': time.time()})
            return lease
        return claim_tx(self.db.transaction())

    def fenced(self, identifier, lease, changes=None, state=None, release=False):
        ref = self.ref(identifier)

        @firestore.transactional
        def write(tx):
            doc = ref.get(transaction=tx).to_dict()
            if (not doc or doc.get('lease') != lease or doc.get('lock_until', 0) <= time.time()
                    or not doc.get('enabled')):
                raise BusyError('갱신 실행 권한이 만료되었습니다.')
            if state is not None:
                tx.set(ref.collection('private').document('sync'), {'state': bounded(state)})
            values = dict(changes or {})
            if release:
                values.update(lease=None, lock_until=0)
            if values:
                tx.update(ref, values)
        write(self.db.transaction())

    def state(self, identifier):
        doc = self.ref(identifier).collection('private').document('sync').get().to_dict()
        return (doc or {}).get('state', {})

    def cache(self, key):
        doc = self.db.collection('planner_school_cache').document(key).get().to_dict()
        return (doc or {}).get('data')

    def cache_put(self, key, data):
        from datetime import datetime, timedelta, timezone
        self.db.collection('planner_school_cache').document(key).set({
            'data': bounded(data), 'expires_at': datetime.now(timezone.utc) + timedelta(days=3)})
