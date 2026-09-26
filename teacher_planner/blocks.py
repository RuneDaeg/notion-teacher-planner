"""Journaled creation and strict recovery for new page layout blocks."""
import copy
from urllib.parse import quote

from .client import NotionError
from .install import compact


def children(client, parent):
    """Read all direct children, rejecting a broken pagination response."""
    cursor, seen, result = None, set(), []
    while True:
        endpoint = f'/blocks/{parent}/children?page_size=100'
        if cursor is not None:
            endpoint += '&start_cursor=' + quote(cursor, safe='')
        response = client.request('GET', endpoint)
        rows = response.get('results')
        if not isinstance(rows, list):
            raise ValueError('Notion 블록 목록 응답을 확인할 수 없습니다.')
        result.extend(rows)
        if not response.get('has_more'):
            return result
        cursor = response.get('next_cursor')
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError('Notion 블록 목록의 다음 페이지를 확인할 수 없습니다.')
        seen.add(cursor)


class BlockJournal:
    """Wrap an existing Journal; append PATCH requests are creation operations."""

    def __init__(self, journal):
        self.journal = journal

    def append(self, key, parent, oneblock, position=None):
        j = self.journal
        j.ready()
        if key in j.data['objects']:
            return j.data['objects'][key]
        if (not isinstance(oneblock, dict) or not isinstance(oneblock.get('type'), str)
                or not isinstance(oneblock.get(oneblock.get('type')), dict) or 'id' in oneblock):
            raise ValueError('새 블록 한 개의 형식과 type을 지정하세요.')
        payload = {'children': [copy.deepcopy(oneblock)]}
        if position is not None:
            valid = position in ({'type': 'start'}, {'type': 'end'})
            if isinstance(position, dict) and set(position) == {'type', 'after_block'} and position['type'] == 'after_block':
                after = position['after_block']
                valid = isinstance(after, dict) and set(after) == {'id'} and isinstance(after['id'], str) and bool(after['id'])
            if not valid:
                raise ValueError('블록 위치는 start/end 또는 after_block.id 형식이어야 합니다.')
            payload['position'] = copy.deepcopy(position)
        endpoint = f'/blocks/{parent}/children'
        j.data['pending'] = {'key': key, 'method': 'PATCH', 'endpoint': endpoint,
                             'block_parent': parent, 'payload': payload}
        j.save()
        try:
            result = j.client.request('PATCH', endpoint, payload)
        except NotionError as error:
            if error.status in (400, 401, 403, 404, 429):
                j.data.pop('pending', None)
                j.save()
            raise
        # A malformed success is ambiguous too: leave pending for inspection.
        results = result.get('results') if isinstance(result, dict) else None
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise ValueError('블록 생성 응답을 확정할 수 없습니다. pending 상태를 확인하세요.')
        block = results[0]
        if not isinstance(block.get('id'), str) or not block['id'] or block.get('type') != oneblock['type']:
            raise ValueError('블록 생성 ID 또는 유형을 확인할 수 없습니다. pending 상태를 확인하세요.')
        j.data['objects'][key] = compact(block)
        j.data.pop('pending')
        j.save()
        return j.data['objects'][key]


def _same_id(first, second):
    return (isinstance(first, str) and isinstance(second, str)
            and first.replace('-', '').lower() == second.replace('-', '').lower())


def _matches_value(expected, actual):
    """Match all authored values while ignoring response-only dictionary keys."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches_value(value, actual[key])
            for key, value in expected.items() if key != 'children')
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(expected) == len(actual)
                and all(_matches_value(a, b) for a, b in zip(expected, actual)))
    return expected == actual


def _verify_tree(client, expected, actual, parent):
    kind = expected.get('type')
    actual_parent = actual.get('parent', {})
    parent_kind = actual_parent.get('type')
    if (parent_kind not in ('page_id', 'block_id', 'database_id')
            or not _same_id(parent, actual_parent.get(parent_kind))
            or actual.get('in_trash') or actual.get('archived')
            or not actual.get('id') or actual.get('type') != kind
            or not _matches_value(expected, actual)):
        raise ValueError('복구 블록의 부모·유형·내용이 생성 요청과 일치하지 않습니다.')
    expected_children = expected.get(kind, {}).get('children', [])
    if expected_children or actual.get('has_children'):
        actual_children = list(children(client, actual['id']))
        if len(expected_children) != len(actual_children):
            raise ValueError('복구 블록의 하위 블록 수가 생성 요청과 일치하지 않습니다.')
        for expected_child, actual_child in zip(expected_children, actual_children):
            _verify_tree(client, expected_child, actual_child, actual['id'])


def recover_block(client, pending, obj):
    """Verify a manually selected append result without changing journal state."""
    parent = pending.get('block_parent')
    expected = pending.get('payload', {}).get('children')
    if (pending.get('method') != 'PATCH' or not isinstance(parent, str)
            or pending.get('endpoint') != f'/blocks/{parent}/children'
            or not isinstance(expected, list) or len(expected) != 1
            or not isinstance(expected[0], dict)):
        raise ValueError('복구 대상은 확인 가능한 블록 한 개 생성 요청이어야 합니다.')
    _verify_tree(client, expected[0], obj, parent)
