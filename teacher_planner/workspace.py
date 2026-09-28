"""Four native Notion pages sharing the original teacher-planner data sources."""
from .blocks import BlockJournal, children
from .dashboard import (dashboard_config, layout_spec, matrix_table, matrix_title,
                        quick_links, section_heading)
from .model import dashboard_views, rich, selected, view_payload


def link(identifier):
    return 'https://www.notion.so/' + identifier.replace('-', '')


def paragraph(text, url=None):
    items = rich(text)
    if url:
        items[0]['text']['link'] = {'url': url}
    return {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': items}}


def callout(text, icon='💡', color='gray_background', url=None):
    items = rich(text)
    if url:
        items[0]['text']['link'] = {'url': url}
    return {'object': 'block', 'type': 'callout', 'callout': {
        'rich_text': items, 'icon': {'type': 'emoji', 'emoji': icon}, 'color': color}}


def cards(items):
    """Only native callouts go in columns; linked database blocks stay on pages."""
    return {'object': 'block', 'type': 'column_list', 'column_list': {'children': [
        {'object': 'block', 'type': 'column', 'column': {
            'width_ratio': 1 / len(items), 'children': [item]}} for item in items]}}


def navigation(pages, current):
    items = []
    for page in layout_spec()['workspace']['pages']:
        if items:
            items += rich('   /   ')
        item = rich(page['icon'] + ' ' + page['title'])[0]
        item['text']['link'] = {'url': link(pages[page['key']])}
        item['annotations'] = {'bold': page['key'] == current}
        items.append(item)
    return {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': items}}


def install_workspace(j, c, root, databases, actual_props):
    spec = layout_spec()
    pages = {'home': root}
    for page in spec['workspace']['pages']:
        if page['key'] == 'home':
            continue
        obj = j.create('workspace:' + page['key'], '/pages', {
            'parent': {'type': 'page_id', 'page_id': root},
            'icon': {'type': 'emoji', 'emoji': page['icon']},
            'properties': {'title': {'title': rich(page['title'])}}})
        pages[page['key']] = obj['id']

    writer = BlockJournal(j)
    views = dashboard_views(c)
    links = {key: link(obj['id']) for key, obj in databases.items()}
    for name, view in (('todo', 'todo'), ('archive', 'archive_agenda')):
        links[name] = links['agenda'] + '?v=' + j.data['objects']['view:' + view]['id'].replace('-', '')
    settings = dashboard_config(c)
    ordered, groups, anchors = {}, [], {}
    matrix = caption = matrix_sync = None
    matrix_references = {}
    # Create the teaching source before home refers to it. Display order within
    # each page still comes exclusively from the public layout manifest.
    page_specs = sorted(spec['workspace']['pages'], key=lambda p: p['key'] != 'teaching')
    for page in page_specs:
        key, parent = page['key'], pages[page['key']]
        ordered[key] = []
        previous = None

        def append(record, block):
            nonlocal previous
            position = ({'type': 'after_block', 'after_block': {'id': previous}}
                        if previous else {'type': 'start'})
            obj = writer.append(record, parent, block, position=position)
            previous = obj['id']
            ordered[key].append(previous)
            return obj

        append(f'layout:{key}:nav', navigation(pages, key))
        profile = (f"{c['academic_year']}학년도 {c.get('semester', 1)}학기 · {c['teacher']} · "
                   + ' / '.join(c['subjects']))
        append(f'layout:{key}:intro', paragraph(profile + '\n' + page['description']))
        for section in page['sections']:
            instance = key + ':' + section['key']
            active = [v for v in views if v['workspace_page'] == key and v['section'] == section['key']]
            kind = section['kind']
            if kind == 'views' and not active:
                continue
            anchor = append('layout:' + instance, section_heading(section['title'], section['color']))
            anchors[instance] = anchor['id']
            if kind == 'views':
                container = None
                for v in active:
                    source = databases[v['source']]
                    if container:
                        payload = view_payload(v, {**source, 'id': container}, actual_props[v['source']], c['academic_year'])
                    else:
                        payload = view_payload(v, source, actual_props[v['source']], c['academic_year'], parent, after=previous)
                    obj = j.create(v['instance_key'], '/views', payload)
                    actual_container = obj['parent']['database_id']
                    if container and container != actual_container:
                        raise ValueError('보기 탭의 연결 DB가 달라 중단합니다. 기존 설치 상태를 확인하세요.')
                    if not container:
                        container = actual_container
                        ordered[key].append(container)
                    previous = container
                groups.append({'page': key, 'section': section['key'], 'container_id': container,
                               'view_keys': [v['instance_key'] for v in active]})
            elif kind == 'briefing':
                append('layout:' + instance + ':note', callout('오늘 전달할 내용, 회의 준비, 확인할 일을 여기에 적으세요.'))
            elif kind == 'quick':
                entries = [('상담 기록 열기', links['counseling'], '✍️'),
                           ('조회·종례 메모', link(pages['classroom']), '📝'),
                           ('출결 확인' if 'attendance' in links else '학생 명부',
                            links.get('attendance', links['students']), '👥'),
                           ('평가·채점' if 'assessments' in links else '수업 진도',
                            links.get('assessments', links['lessons']), '📋')]
                append('layout:' + instance + ':cards', cards([
                    callout(title, icon, url=url) for title, url, icon in entries]))
            elif kind == 'notes':
                append('layout:' + instance + ':cards', cards([
                    callout('조회 메모\n날짜와 전달 내용을 적으세요.', '☀️'),
                    callout('종례 메모\n날짜와 확인할 내용을 적으세요.', '📝'),
                    callout('후속 관찰\n학생별 기록은 상담 DB에 연결하세요.', '🌱', 'yellow_background')]))
            elif kind == 'school':
                for name, title in (('staff', '교직원 연락망'), ('accounts', '학교 계정 관리'), ('meetings', '회의록')):
                    if name in links:
                        append('layout:' + instance + ':' + name, paragraph(title, links[name]))
                for name, title in (('neis', 'NEIS 나이스'), ('edufine', 'K-에듀파인'),
                                    ('shared_page', '공유 페이지'), ('survey', '설문·제출 안내')):
                    url = settings['links'][name]
                    append('layout:' + instance + ':' + name,
                           paragraph(title + ('' if url else ' · 내 링크 설정'), url or None))
            elif kind == 'matrix':
                matrix_sync = append('layout:matrix-sync', {'object': 'block', 'type': 'synced_block',
                    'synced_block': {'synced_from': None}})
                caption = writer.append('layout:matrix-caption', matrix_sync['id'], matrix_title(None))
                matrix = writer.append('layout:matrix', matrix_sync['id'], matrix_table(c))
                writer.append('layout:' + instance + ':sync', matrix_sync['id'], callout(
                    '홈과 교과 페이지가 같은 주간 표를 공유합니다. 시간표 파일·컴시간 동기화 후 함께 갱신됩니다. 확인되지 않은 교시 시각은 비워 둡니다. 수정·변경·휴강 기록은 아래 관리용 표에서 확인하세요.', '🔄', 'blue_background'))
                append('layout:quick-links', {'object': 'block', 'type': 'toggle', 'toggle': {
                    'rich_text': rich('빠른 동작 / 즐겨찾기'), 'children': quick_links(c, links)}})
            elif kind == 'matrix_reference':
                if matrix_sync is None:
                    raise ValueError('공유할 교과 주간 시간표 원본이 먼저 필요합니다.')
                reference = append('layout:' + instance + ':reference', {
                    'object': 'block', 'type': 'synced_block', 'synced_block': {
                        'synced_from': {'type': 'block_id', 'block_id': matrix_sync['id']}}})
                matrix_references[key] = reference['id']
            elif kind == 'para':
                append('layout:' + instance + ':cards', cards([
                    callout(title, icon, color, links[name]) for name, title, icon, color in (
                        ('projects', 'Projects\n기한과 완료 기준이 있는 일', '🚀', 'purple_background'),
                        ('areas', 'Areas\n꾸준히 관리할 담당 영역', '🌱', 'green_background'),
                        ('resources', 'Resources\n다시 꺼내 쓸 수업·업무 자료', '📚', 'blue_background'),
                        ('archive', 'Archives\n보관한 업무와 자료', '🗃️', 'gray_background'))]))
            elif kind == 'archives':
                archive_links = [paragraph(d['title'], links[d['key']] + '?v=' +
                                 j.data['objects']['view:archive_' + d['key']]['id'].replace('-', ''))
                                 for d in selected(c)[0]]
                append('layout:' + instance + ':toggle', {'object': 'block', 'type': 'toggle', 'toggle': {
                    'rich_text': rich('표별 보관함 열기'), 'children': archive_links}})
            else:
                raise ValueError('알 수 없는 워크스페이스 구역: ' + kind)
    previous = j.data.get('dashboard', {})
    j.data['dashboard'] = {**previous, 'version': spec['version'], 'root_id': root,
                           'pages': pages, 'page_order': ordered, 'view_groups': groups, 'anchors': anchors}
    if matrix:
        rows = previous.get('matrix_row_ids') or [r['id'] for r in children(j.client, matrix['id'])]
        j.data['dashboard'].update(matrix_id=matrix['id'], caption_id=caption['id'], matrix_row_ids=rows)
    if matrix_sync:
        j.data['dashboard'].update(matrix_sync_id=matrix_sync['id'], matrix_reference_ids=matrix_references)
    j.save()


def verify_matrix_sharing(client, layout):
    """Check the shared wrapper without treating mirrored children as new blocks."""
    source_id = layout.get('matrix_sync_id')
    if not source_id:
        return []
    issues = []
    source = client.request('GET', '/blocks/' + source_id)
    if (source.get('type') != 'synced_block'
            or source.get('synced_block', {}).get('synced_from') is not None
            or source.get('parent', {}).get('page_id') != layout['pages']['teaching']
            or source.get('in_trash') or source.get('archived')):
        issues.append('주간 시간표: 교과 페이지의 공유 원본을 확인하세요.')
    expected = [layout['caption_id'], layout['matrix_id']]
    found = [row['id'] for row in children(client, source_id) if row['id'] in expected]
    if found != expected:
        issues.append('주간 시간표: 공유 원본 안의 표시 주간·표 위치를 확인하세요.')
    for page, identifier in layout.get('matrix_reference_ids', {}).items():
        reference = client.request('GET', '/blocks/' + identifier)
        if (reference.get('type') != 'synced_block'
                or (reference.get('synced_block', {}).get('synced_from') or {}).get('block_id') != source_id
                or reference.get('parent', {}).get('page_id') != layout['pages'][page]
                or reference.get('in_trash') or reference.get('archived')):
            issues.append(f'{page}: 교과 시간표와 같은 공유 원본을 연결하세요.')
    return issues


def verify_workspace(client, state):
    layout, issues = state['dashboard'], []
    for key, parent in layout['pages'].items():
        actual = children(client, parent)
        expected = layout['page_order'][key]
        found = [item['id'] for item in actual if item['id'] in expected]
        if found != expected:
            issues.append(f'{key}: 구역 또는 연결 뷰의 위치·순서가 기본 템플릿과 다릅니다.')
        nav = state['objects'].get(f'layout:{key}:nav')
        if nav:
            block = client.request('GET', '/blocks/' + nav['id'])
            urls = {(item.get('text', {}).get('link') or {}).get('url')
                    for item in block.get('paragraph', {}).get('rich_text', [])}
            if not {link(page) for page in layout['pages'].values()} <= urls:
                issues.append(f'{key}: 네 화면의 탐색 링크를 확인하세요.')
        if key != 'home':
            page = client.request('GET', '/pages/' + parent)
            if page.get('parent', {}).get('page_id') != layout['root_id'] or page.get('in_trash') or page.get('archived'):
                issues.append(f'{key}: 하위 페이지의 위치·보관 상태를 확인하세요.')
    for group in layout['view_groups']:
        if any(state['objects'].get(key, {}).get('parent', {}).get('database_id') != group['container_id']
               for key in group['view_keys']):
            issues.append(f"{group['page']}/{group['section']}: 같은 연결 데이터베이스의 보기 탭인지 확인하세요.")
    if layout.get('matrix_id'):
        # Keep checking older, owned column layouts without moving their blocks.
        if state['objects'].get('layout:top'):
            top = children(client, state['objects']['layout:top']['id'])
            if len(top) != 2 or any(row.get('type') != 'column' or
                    abs(row.get('column', {}).get('width_ratio', 0) - spec['width_ratio']) > .001
                    for row, spec in zip(top, layout_spec()['top'])):
                issues.append('수업 시간표: 열 개수·비율을 확인하세요.')
        table = client.request('GET', '/blocks/' + layout['matrix_id'])
        if table.get('type') != 'table' or table.get('table', {}).get('table_width') != 7:
            issues.append('수업 시간표: 교시·수업시간·월~금 7개 열을 확인하세요.')
        issues.extend(verify_matrix_sharing(client, layout))
    return issues
