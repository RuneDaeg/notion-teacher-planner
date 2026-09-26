"""Install the reference-based home and refresh its dated timetable mirror."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .blocks import BlockJournal, children
from .dashboard import (dashboard_config, layout_spec, matrix_table, matrix_title,
                        middle_columns, middle_content, quick_links, section_heading,
                        top_columns)
from .install import Journal, compact
from .model import dashboard_views, rich, selected, view_payload
from .timetable import text_property


def notion_link(obj, view=None):
    url = 'https://www.notion.so/' + obj['id'].replace('-', '')
    return url + '?v=' + view['id'].replace('-', '') if view else url


def _column_ids(j, block, key, count):
    columns = children(j.client, block['id'])
    if len(columns) != count or any(c.get('type') != 'column' for c in columns):
        raise ValueError('홈 열 구조가 예상과 다릅니다. 중복 생성하지 않고 중단합니다.')
    for i, column in enumerate(columns):
        record = f'layout:{key}:column:{i}'
        old = j.data['objects'].get(record)
        if old and old['id'] != column['id']:
            raise ValueError('설치 중 홈 열이 변경되었습니다. 기존 설치 상태를 확인하세요.')
        j.data['objects'][record] = compact(column)
    j.save()
    return [column['id'] for column in columns]


def install_dashboard(j, c, root, databases, actual_props):
    writer = BlockJournal(j)
    links = {key: notion_link(obj) for key, obj in databases.items()}
    for key, source in (('semester_1', 'lessons'), ('semester_2', 'lessons'),
                        ('resource_archive', 'resources')):
        view_key = 'archive_resources' if key == 'resource_archive' else key
        links[key] = notion_link(databases[source], j.data['objects']['view:' + view_key])
    links['todo'] = notion_link(databases['agenda'], j.data['objects']['view:todo'])
    links['archive'] = notion_link(databases['agenda'], j.data['objects']['view:archive_agenda'])
    links['timetable'] = notion_link(databases['timetable'])

    # First create shallow column scaffolds; append tables/children separately to
    # remain within Notion's two-level nesting limit for one append request.
    top = writer.append('layout:top', root, top_columns(c, links), position={'type': 'start'})
    left, right = _column_ids(j, top, 'top', 2)
    caption = writer.append('layout:matrix-caption', left, matrix_title(None))
    table = writer.append('layout:matrix', left, matrix_table(c))
    for i, block in enumerate(quick_links(c, links)):
        writer.append(f'layout:quick:{i}', right, block)

    anchors = {}
    previous = top['id']
    headings = layout_spec()['sections']
    for key in ('things', 'meetings', 'middle', 'students', 'schedule', 'archive'):
        if key == 'meetings' and 'meetings' not in databases:
            continue
        if key == 'middle':
            block = middle_columns(c, links)
        else:
            spec = headings[key]
            block = section_heading(spec['title'], spec['color'])
        anchor = writer.append('layout:' + key, root, block,
                               position={'type': 'after_block', 'after_block': {'id': previous}})
        previous = anchor['id']
        anchors[key] = previous
        if key == 'middle':
            columns = _column_ids(j, anchor, 'middle', 3)
            for i, blocks in enumerate(middle_content(c, links)):
                for k, child in enumerate(blocks):
                    writer.append(f'layout:middle:{i}:{k}', columns[i], child)

    slots = {'inbox': 'things', 'todo': 'things', 'active_meetings': 'meetings',
             'active_students': 'students', 'weekly': 'schedule', 'monthly': 'schedule',
             'teacher_week': 'schedule', 'archive_agenda': 'archive'}
    view_anchors = dict(anchors)
    for v in dashboard_views(c):
        section = slots[v['key']]
        obj = j.create('home:' + v['key'], '/views', view_payload(
            v, databases[v['source']], actual_props[v['source']], c['academic_year'],
            root, after=view_anchors[section]))
        view_anchors[section] = obj['parent']['database_id']
    archive_links = []
    for d in selected(c)[0]:
        url = notion_link(databases[d['key']], j.data['objects']['view:archive_' + d['key']])
        block = {'object': 'block', 'type': 'paragraph', 'paragraph': {
            'rich_text': [{'type': 'text', 'text': {'content': d['title'] + ' 보관함', 'link': {'url': url}}}]}}
        archive_links.append(block)
    writer.append('layout:archive-links', root, {'object': 'block', 'type': 'toggle',
                  'toggle': {'rich_text': rich('표별 보관함'), 'children': archive_links}},
                  position={'type': 'after_block', 'after_block': {'id': view_anchors['archive']}})
    previous_layout = j.data.get('dashboard', {})
    base_rows = previous_layout.get('matrix_row_ids') or [r['id'] for r in children(j.client, table['id'])]
    j.data['dashboard'] = {**previous_layout, 'version': layout_spec()['version'], 'root_id': root,
                           'matrix_id': table['id'], 'caption_id': caption['id'],
                           'matrix_row_ids': base_rows,
                           'anchors': anchors}
    j.save()


def _week_for(rows, explicit=None):
    if explicit:
        start = date.fromisoformat(explicit)
        return start - timedelta(days=start.weekday())
    days = [date.fromisoformat(r['date']) for r in rows]
    if not days:
        return None
    weeks = {day - timedelta(days=day.weekday()) for day in days}
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    current = today - timedelta(days=today.weekday())
    return current if current in weeks else max(weeks)


def _plain_rich(items):
    return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in items)


def verify_dashboard(client, state):
    layout = state.get('dashboard')
    if not layout:
        return []
    issues = []
    actual = children(client, layout['root_id'])
    actual_ids = [item['id'] for item in actual]
    objects = state['objects']
    groups = [('top', []), ('things', ['inbox', 'todo']), ('meetings', ['active_meetings']),
              ('middle', []), ('students', ['active_students']),
              ('schedule', ['weekly', 'monthly', 'teacher_week']), ('archive', ['archive_agenda'])]
    expected = []
    for key, views in groups:
        if 'layout:' + key not in objects:
            continue
        expected.append(objects['layout:' + key]['id'])
        for view in views:
            if 'home:' + view in objects:
                expected.append(objects['home:' + view]['parent']['database_id'])
    found = [identifier for identifier in actual_ids if identifier in expected]
    if found != expected:
        issues.append('홈: 구역 또는 연결 뷰의 위치·순서가 기본 템플릿과 다릅니다.')
    for key, specs in (('top', layout_spec()['top']), ('middle', layout_spec()['middle'])):
        actual_columns = children(client, objects['layout:' + key]['id'])
        if len(actual_columns) != len(specs) or any(
                column.get('type') != 'column' or
                abs(column.get('column', {}).get('width_ratio', 0) - spec['width_ratio']) > 0.001
                for column, spec in zip(actual_columns, specs)):
            issues.append(f'홈 {key}: 열 개수·비율을 확인하세요.')
    table = client.request('GET', '/blocks/' + layout['matrix_id'])
    if table.get('type') != 'table' or table.get('table', {}).get('table_width') != 7:
        issues.append('홈 시간표: 교시·수업시간·월~금 7개 열을 확인하세요.')
    return issues


def refresh_dashboard(client, state_path, imported_rows, week_start=None):
    """Refresh only the owned matrix, reading all saved lessons in its week.

    Legacy notebooks have no owned dashboard and retain their previous behavior.
    This reads after synchronization, so a missing input slot never clears a
    previously imported lesson that the sync engine intentionally preserved.
    """
    j = Journal(state_path, client)
    j.ready()
    layout = j.data.get('dashboard')
    week = _week_for(imported_rows, week_start)
    if not layout or week is None:
        return 0
    c, dbs = j.data['config'], j.data['databases']
    class_names = {p['id']: text_property(p, '이름') for p in client.pages(dbs['classes']['data_source_id'])}
    end = week + timedelta(days=7)
    selected_filter = {'and': [
        {'property': '학년도', 'number': {'equals': c['academic_year']}},
        {'property': '보관', 'checkbox': {'equals': False}},
        {'property': '수업일', 'date': {'on_or_after': week.isoformat()}},
        {'property': '수업일', 'date': {'before': end.isoformat()}},
    ]}
    rows = []
    for page in client.pages(dbs['timetable']['data_source_id'], selected_filter):
        p = page['properties']
        span = p.get('수업일', {}).get('date')
        period = p.get('교시', {}).get('number')
        if not span or not period or p.get('보관', {}).get('checkbox'):
            continue
        start = span['start']
        day = (date.fromisoformat(start) if len(start) == 10 else
               datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(ZoneInfo(c['timezone'])).date())
        if not week <= day < end or p.get('학년도', {}).get('number') != c['academic_year']:
            continue
        relations = p.get('학급', {}).get('relation', [])
        cls = ', '.join(class_names.get(r['id'], '학급 미지정') for r in relations) or '학급 미지정'
        if isinstance(period, bool) or not isinstance(period, (int, float)) or int(period) != period:
            raise ValueError('시간표의 교시는 정수여야 합니다.')
        rows.append({'date': day.isoformat(), 'period': int(period), 'date_range': span,
                     'class_name': cls, 'subject': text_property(page, '교과'),
                     'room': text_property(page, '교실'),
                     'status': (p.get('상태', {}).get('select') or {}).get('name', '예정')})
    actual_rows = children(client, layout['matrix_id'])
    if len(actual_rows) < 3 or any(r.get('type') != 'table_row' or len(r['table_row']['cells']) != 7 for r in actual_rows):
        raise ValueError('홈 시간표의 행·열 구조가 변경되었습니다. 자동으로 다시 만들지 않습니다.')
    owned_ids = list(layout['matrix_row_ids'])
    extras = sorted((int(key.rsplit(':', 1)[1]), value['id']) for key, value in j.data['objects'].items()
                    if key.startswith('layout:matrix:extra:'))
    for index, identifier in extras:
        if index != len(owned_ids):
            raise ValueError('홈 시간표 확장 기록이 일치하지 않습니다.')
        owned_ids.append(identifier)
    if [r['id'] for r in actual_rows] != owned_ids:
        raise ValueError('홈 시간표의 행 순서나 개수가 변경되었습니다. 기존 블록을 보존하고 중단합니다.')
    # Keep an already-expanded table's height; never delete user-visible blocks.
    display_c = {**c, 'dashboard': {**dashboard_config(c), 'periods': max(dashboard_config(c)['periods'], len(actual_rows) - 2)}}
    desired = matrix_table(display_c, rows, week.isoformat())['table']['children']
    # The reference's common-course row is a manual note area. Preserve it even
    # when adding numeric periods would move it to the new last row.
    common_rows = [r for r in actual_rows if _plain_rich(r['table_row']['cells'][0]) == '공동']
    if not common_rows:
        raise ValueError('홈 시간표 공동수업 행을 확인하세요.')
    # A failed multi-row extension can leave numeric rows after the old note
    # before the new final note is appended. Resume from the last preserved copy.
    desired[-1]['table_row']['cells'] = [
        [{k: v for k, v in item.items() if k in ('type', 'text', 'mention', 'equation', 'annotations')}
         for item in cell] for cell in common_rows[-1]['table_row']['cells']]
    writer = BlockJournal(j)
    written = 0
    while len(actual_rows) < len(desired):
        index = len(actual_rows)
        obj = writer.append(f'layout:matrix:extra:{index}', layout['matrix_id'], desired[index])
        actual_rows.append({**obj, 'type': 'table_row', 'table_row': desired[index]['table_row']})
        written += 1
    for current, want in zip(actual_rows, desired):
        current_cells = [_plain_rich(cell) for cell in current['table_row']['cells']]
        desired_cells = [_plain_rich(cell) for cell in want['table_row']['cells']]
        if current_cells != desired_cells:
            client.request('PATCH', '/blocks/' + current['id'], {'table_row': want['table_row']})
            written += 1
    caption = client.request('GET', '/blocks/' + layout['caption_id'])
    desired_caption = matrix_title(week.isoformat())
    if _plain_rich(caption.get('paragraph', {}).get('rich_text', [])) != _plain_rich(desired_caption['paragraph']['rich_text']):
        client.request('PATCH', '/blocks/' + layout['caption_id'], {'paragraph': desired_caption['paragraph']})
        written += 1
    j.data['dashboard']['week_start'] = week.isoformat()
    j.save()
    return written
