"""Pure Notion block builders for the shared teacher-notebook layout.

No network calls or state changes live here. The manifest is also used by
installation previews, so displayed structure and installed blocks stay aligned.
"""
import copy
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo


COLORS = {'default', 'gray', 'brown', 'orange', 'yellow', 'green', 'blue',
          'purple', 'pink', 'red'}
COLORS |= {name + '_background' for name in COLORS if name != 'default'}


def layout_spec():
    """Return a fresh copy of the versioned, public layout manifest."""
    return json.loads(Path(__file__).with_name('dashboard.json').read_text(encoding='utf-8'))


def _label(value, label='이름', maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label}: 1~{maximum}자 문자열을 입력하세요.')
    return value.strip()


def _url(value, label='링크', blank=False):
    if not isinstance(value, str):
        raise ValueError(f'{label}: URL 문자열을 입력하세요.')
    if blank and not value.strip():
        return ''
    if not value or value != value.strip() or len(value) > 2000:
        raise ValueError(f'{label}: 올바른 HTTP(S) URL을 입력하세요.')
    if any(char.isspace() for char in value) or any(ord(char) < 32 or ord(char) == 127 for char in unquote(value)):
        raise ValueError(f'{label}: URL에 공백이나 제어 문자를 넣을 수 없습니다.')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                 and parsed.username is None and parsed.password is None)
        parsed.port  # Validate malformed or out-of-range port strings as well.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f'{label}: 계정 정보가 없는 HTTP(S) URL만 사용할 수 있습니다.')
    return value


def dashboard_config(c):
    """Overlay and validate optional dashboard settings without mutating config."""
    if not isinstance(c, dict):
        raise ValueError('설정은 JSON 객체여야 합니다.')
    supplied = c.get('dashboard', {})
    if not isinstance(supplied, dict) or set(supplied) - {'periods', 'bookmarks', 'links'}:
        raise ValueError('dashboard에는 periods, bookmarks, links만 사용할 수 있습니다.')
    result = copy.deepcopy(layout_spec()['defaults'])
    result.update(copy.deepcopy(supplied))
    if type(result['periods']) is not int or not 1 <= result['periods'] <= 20:
        raise ValueError('dashboard.periods는 1~20 정수여야 합니다.')
    bookmarks = result['bookmarks']
    if not isinstance(bookmarks, list) or len(bookmarks) > 20:
        raise ValueError('dashboard.bookmarks는 20개 이하의 목록이어야 합니다.')
    normalized = []
    for bookmark in bookmarks:
        if not isinstance(bookmark, dict) or set(bookmark) != {'label', 'url'}:
            raise ValueError('각 즐겨찾기에는 label과 url을 지정하세요.')
        normalized.append({'label': _label(bookmark['label'], '즐겨찾기 이름'),
                           'url': _url(bookmark['url'], '즐겨찾기', blank=True)})
    result['bookmarks'] = normalized
    links = result['links']
    if not isinstance(links, dict) or set(links) - {'shared_page', 'survey', 'neis', 'edufine'}:
        raise ValueError('dashboard.links에는 shared_page, survey, neis, edufine만 사용할 수 있습니다.')
    result['links'] = {key: _url(links.get(key, ''), key, blank=True)
                       for key in ('shared_page', 'survey', 'neis', 'edufine')}
    return result


def _rich(text, color='default', bold=False, url=None, cancelled=False):
    if color not in COLORS:
        raise ValueError('지원하지 않는 Notion 색상입니다.')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('블록 텍스트는 2000자 이하 문자열이어야 합니다.')
    if not text:
        return []
    item = {'type': 'text', 'text': {'content': text},
            'annotations': {'color': color, 'bold': bold, 'strikethrough': cancelled}}
    if url:
        item['text']['link'] = {'url': _url(url)}
    return [item]


def _text_block(kind, text, color='default', bold=False, url=None):
    return {'object': 'block', 'type': kind,
            kind: {'rich_text': _rich(text, bold=bold, url=url), 'color': color}}


def section_heading(name, color):
    if color not in COLORS:
        raise ValueError('지원하지 않는 Notion 색상입니다.')
    return _text_block('heading_2', _label(name, maximum=200), color)


def _subheading(key):
    section = layout_spec()['sections'][key]
    return _text_block('heading_3', section['title'], section['color'])


def _divider():
    return {'object': 'block', 'type': 'divider', 'divider': {}}


def _link_url(value):
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get('url'):
            value = value['url']
        else:
            identifier = value.get('id')
            if not isinstance(identifier, str) or not re.fullmatch(
                    r'[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier):
                raise ValueError('Notion 링크 객체에는 URL 또는 UUID가 필요합니다.')
            value = 'https://www.notion.so/' + identifier.replace('-', '')
    return _url(value)


def _links(blocks, links, entries):
    for key, label in entries:
        if links.get(key) is not None:
            blocks.append(_text_block('paragraph', label, url=_link_url(links[key])))


def _columns(items, callout_last=False):
    children = []
    for index, item in enumerate(items):
        heading = section_heading(item['title'], item['color'])
        if callout_last and index == len(items) - 1:
            heading = _text_block('callout', item['title'], item['color'], bold=True)
            heading['callout']['icon'] = {'type': 'emoji', 'emoji': '⚡'}
        children.append({'object': 'block', 'type': 'column',
                         'column': {'width_ratio': item['width_ratio'], 'children': [heading]}})
    return {'object': 'block', 'type': 'column_list', 'column_list': {'children': children}}


def top_columns(c, links):
    """Shallow column scaffolds; append the matrix and links after IDs are known."""
    dashboard_config(c)
    return _columns(layout_spec()['top'], callout_last=True)


def middle_columns(c, links=None):
    dashboard_config(c)
    return _columns(layout_spec()['middle'])


def quick_links(c, links):
    settings = dashboard_config(c)
    blocks = [_subheading('quick_actions')]
    _links(blocks, links, [('timetable', '시간표'), ('lessons', '수업 진도'),
                         ('todo', '할 일'), ('archive', '보관함'), ('resources', '수업 자료')])
    blocks.extend([_divider(), _subheading('bookmarks')])
    for bookmark in settings['bookmarks']:
        text = bookmark['label'] + ('' if bookmark['url'] else ' · 링크 추가')
        blocks.append(_text_block('paragraph', text, url=bookmark['url'] or None))
    return blocks


def middle_content(c, links):
    """Return left/center/right block lists, using only supplied active DB links."""
    settings = dashboard_config(c)
    left, center, right = [], [], []
    _links(left, links, [('resources', '교과 자료'), ('semester_1', '1학기 수업 진도'),
                        ('semester_2', '2학기 수업 진도'), ('resource_archive', '수업 자료 보관함')])
    left.extend([_divider(), _subheading('share')])
    for key, label in (('shared_page', '공유 페이지'), ('survey', '설문 · 제출 안내')):
        url = settings['links'][key]
        left.append(_text_block('paragraph', label + ('' if url else ' · 링크 추가'), url=url or None))
    left.extend([_divider(), _subheading('advice')])
    _links(left, links, [('students', '학생 정보'), ('counseling', '상담 기록'),
                        ('contacts', '학부모 연락 · 후속 조치')])
    center.append(_text_block('paragraph', '학생 정보를 확인하고 상담·출결·제출 기록으로 이어갑니다.'))
    _links(center, links, [('students', '학생 명렬표 열기')])
    if links.get('students') is not None:
        center.append(_text_block('paragraph', '학생 명렬표는 이 영역 바로 아래에서 이어집니다.', 'gray'))
    _links(right, links, [('staff', '교직원 연락처'), ('accounts', '학교 업무 계정 안내'),
                         ('meetings', '회의록 · 결정 사항'), ('counseling', '상담 기록')])
    right.extend([_divider(), _subheading('etc')])
    _links(right, links, [('agenda', 'To Do List'), ('areas', '담당 업무 · Areas'),
                         ('projects', '프로젝트 · Projects')])
    return [left, center, right]


def _day(value, label):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f'{label}: YYYY-MM-DD 날짜를 입력하세요.')


def _week(value):
    if value is None:
        return None
    start = _day(value, '주 시작일')
    if start.weekday() != 0:
        raise ValueError('주 시작일은 월요일이어야 합니다.')
    return start


def matrix_title(week_start):
    start = _week(week_start)
    text = '주간 시간표 · 월요일부터 금요일까지' if start is None else (
        f"{start.isoformat()} ~ {(start + timedelta(days=4)).isoformat()} · 주간 시간표")
    return _text_block('paragraph', text, 'gray')


def _time_label(row, zone):
    span = row.get('date_range')
    if span is None:
        return ''
    if not isinstance(span, dict) or not isinstance(span.get('start'), str):
        raise ValueError('수업 시각의 date_range.start를 확인하세요.')
    start = span['start']
    if len(start) == 10:
        if _day(start, '수업 시각') != _day(row['date'], '수업일'):
            raise ValueError('수업일과 일정 날짜가 다릅니다.')
        return ''
    try:
        a = datetime.fromisoformat(start.replace('Z', '+00:00'))
        a = a.replace(tzinfo=zone) if a.tzinfo is None else a.astimezone(zone)
        if a.date() != _day(row['date'], '수업일'):
            raise ValueError
        end = span.get('end')
        if end is None:
            return a.strftime('%H:%M')
        b = datetime.fromisoformat(end.replace('Z', '+00:00'))
        b = b.replace(tzinfo=zone) if b.tzinfo is None else b.astimezone(zone)
        if b <= a:
            raise ValueError
        return a.strftime('%H:%M') + '–' + b.strftime('%H:%M')
    except (TypeError, ValueError, AttributeError):
        raise ValueError('수업 시작·종료 시각을 확인하세요.') from None


def matrix_table(c, rows=(), week_start=None):
    """Build a Monday–Friday matrix without inventing lessons or period times.

    All input dates must belong to one week. Weekend records remain in the source
    calendar and are omitted from this five-day display. Multiple records in one
    slot are shown together so migrations or combined lessons are never hidden.
    """
    settings = dashboard_config(c)
    start = _week(week_start)
    if not isinstance(rows, (list, tuple)):
        raise ValueError('시간표 행은 목록이어야 합니다.')
    zone = ZoneInfo(c.get('timezone', 'Asia/Seoul'))
    indexed = defaultdict(list)
    period_times = defaultdict(list)
    maximum = settings['periods']
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('시간표의 각 행은 객체여야 합니다.')
        day = _day(row.get('date'), '수업일')
        monday = day - timedelta(days=day.weekday())
        if start is None:
            start = monday
        if monday != start:
            raise ValueError('한 시간표 행렬에는 같은 주의 수업만 넣을 수 있습니다.')
        period = row.get('period')
        if type(period) is not int or not 1 <= period <= 20:
            raise ValueError('시간표 교시는 1~20 정수여야 합니다.')
        subject = _label(row.get('subject'), '교과', 200)
        cls = _label(row.get('class_name'), '학급', 200)
        status = row.get('status', '예정')
        if status not in ('예정', '변경', '휴강', '완료'):
            raise ValueError('시간표 상태를 확인하세요.')
        room = row.get('room', '')
        if not isinstance(room, str) or len(room) > 200:
            raise ValueError('교실은 200자 이하 문자열이어야 합니다.')
        if day.weekday() > 4:
            continue
        maximum = max(maximum, period)
        label = _time_label(row, zone)
        item = {'subject': subject, 'class_name': cls, 'status': status,
                'room': room.strip(), 'time': label}
        indexed[(period, day.weekday())].append(item)
        period_times[period].append(label)

    headers = ['교시', '수업시간']
    for day, label in enumerate(('월', '화', '수', '목', '금')):
        headers.append(label if start is None else label + '\n' + (start + timedelta(days=day)).strftime('%m/%d'))
    table_rows = [_table_row([_rich(x, bold=True, color='gray_background') for x in headers])]
    palette = layout_spec()['class_colors']
    for period in range(1, maximum + 1):
        labels = set(period_times[period])
        differing_times = len(labels) > 1
        timing = '수업별 상이' if differing_times else next(iter(labels), '')
        cells = [_rich(f'{period}교시', bold=True), _rich(timing, color='gray')]
        for day in range(5):
            text = []
            for lesson in indexed[(period, day)]:
                if text:
                    text.extend(_rich('\n\n'))
                cancelled = lesson['status'] == '휴강'
                prefix = '취소 · ' if cancelled else ('변경 · ' if lesson['status'] == '변경' else '')
                content = prefix + lesson['subject'] + '\n' + lesson['class_name']
                if lesson['room']:
                    content += '\n' + lesson['room']
                if differing_times and lesson['time']:
                    content += '\n' + lesson['time']
                digest = hashlib.sha256(lesson['class_name'].encode('utf-8')).digest()
                color = palette[int.from_bytes(digest[:4], 'big') % len(palette)]
                text.extend(_rich(content, color=color, cancelled=cancelled))
            if len(text) > 100:
                raise ValueError('한 시간표 칸에 기록이 너무 많습니다. 중복 수업을 확인하세요.')
            cells.append(text)
        table_rows.append(_table_row(cells))
    table_rows.append(_table_row([_rich('공동', bold=True)] + [[] for _ in range(6)]))
    return {'object': 'block', 'type': 'table',
            'table': {'table_width': 7, 'has_column_header': True,
                      'has_row_header': True, 'children': table_rows}}


def _table_row(cells):
    return {'object': 'block', 'type': 'table_row', 'table_row': {'cells': cells}}
