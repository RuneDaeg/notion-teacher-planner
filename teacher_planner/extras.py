"""Add meals and a reusable form library without replacing existing records."""
from .blocks import BlockJournal, _same_id
from .client import NotionError
from .forms import install_forms, verify_forms
from .meals import MEAL_KEY, checked_block
from .workspace import callout, paragraph, link


def _place(j, key, block, after=None):
    root = j.data['objects']['root']['id']
    position = {'type': 'after_block', 'after_block': {'id': after}} if after else {'type': 'start'}
    obj = BlockJournal(j).append(key, root, block, position=position)
    order = j.data.get('dashboard', {}).get('page_order', {}).get('home')
    if order is not None:
        if obj['id'] in order:
            order.remove(obj['id'])
        index = order.index(after) + 1 if after in order else 0
        order.insert(index, obj['id'])
    j.save()
    return obj['id']


def _checked_forms_link(client, identifier, root, library):
    """Reuse the canonical top-level link without changing its position/text."""
    message = '홈 양식 모음 링크의 위치·연결을 확인하세요.'
    try:
        block = client.request('GET', '/blocks/' + identifier)
    except NotionError as error:
        if error.status == 404:
            raise ValueError(message) from None
        raise
    parent = block.get('parent', {})
    urls = {(item.get('text', {}).get('link') or {}).get('url')
            for item in block.get('paragraph', {}).get('rich_text', [])}
    if (not _same_id(block.get('id'), identifier) or block.get('type') != 'paragraph'
            or block.get('archived') or block.get('in_trash')
            or parent.get('type') not in ('page_id', 'block_id')
            or not _same_id(parent.get(parent.get('type')), root) or link(library) not in urls):
        raise ValueError(message)
    return block


def install_extras(j, c, root, databases):
    j.ready()
    if not _same_id(root, j.data['objects']['root']['id']):
        raise ValueError('추가 기능의 대상 홈이 설치 기록과 다릅니다.')
    actual_root = j.client.request('GET', '/pages/' + root)
    if actual_root.get('archived') or actual_root.get('in_trash'):
        raise ValueError('휴지통의 수첩에는 추가 기능을 설치할 수 없습니다.')
    existing = j.data['objects'].get(MEAL_KEY)
    if existing:
        checked_block(j.client, existing['id'], root)
    existing_link = j.data['objects'].get('extras:forms:link')
    if existing_link:
        saved_library = j.data['objects'].get('forms:library')
        if not saved_library:
            raise ValueError('양식 모음 링크의 대상 설치 기록이 없습니다. 기존 상태를 확인하세요.')
        _checked_forms_link(j.client, existing_link['id'], root, saved_library['id'])
    library = install_forms(j, c, root, databases)
    if existing:
        meal = existing['id']
    else:
        today = j.data['objects'].get('home:teacher_today', {}).get('parent', {}).get('database_id')
        meal = _place(j, MEAL_KEY, callout(
            '오늘의 중식\n아직 조회하지 않았습니다. NEIS 인증키·교육청 코드·표준학교코드를 설정하고 meals-sync를 실행하세요.\n조회 후에는 날짜와 중식 메뉴·열량을 표시합니다.',
            '🍱', 'yellow_background'), after=today)
    if library:
        if existing_link:
            _checked_forms_link(j.client, existing_link['id'], root, library)
            forms_link = existing_link['id']
        else:
            anchor = j.data['objects'].get('layout:home:quick:cards', {}).get('id')
            forms_link = _place(j, 'extras:forms:link', paragraph('📝 자주 쓰는 양식 모음', link(library)), after=anchor)
    else:
        if existing_link:
            raise ValueError('기존 양식 모음 링크가 있으므로 선택 양식을 바꾸어 추가 설치할 수 없습니다.')
        forms_link = None
    j.data['extras'] = {'root_id': root, 'meal_block_id': meal, 'forms_link_id': forms_link}
    j.save()
    return j.data['extras']


def verify_extras(client, state):
    extras = state.get('extras')
    if not extras:
        return []
    issues = verify_forms(client, state)
    try:
        checked_block(client, extras['meal_block_id'], state['objects']['root']['id'])
    except ValueError as error:
        issues.append(str(error))
    if extras.get('forms_link_id'):
        try:
            _checked_forms_link(client, extras['forms_link_id'], extras['root_id'], state['forms']['library_id'])
        except ValueError as error:
            issues.append(str(error))
    return issues
