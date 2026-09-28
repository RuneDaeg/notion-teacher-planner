"""Reusable blank page forms; these are library pages, not registered DB templates."""
import copy

from .blocks import BlockJournal, _same_id, children
from .client import NotionError
from .install import fingerprint
from .model import blueprint, rich, selected

VERSION = 1
_CATALOG = [
    {'key': 'counseling', 'title': '학생 상담 기록', 'target': 'counseling', 'icon': '💬',
     'summary': '상담 목적, 관찰과 대화, 합의 사항, 후속 확인을 남깁니다.'},
    {'key': 'guardian', 'title': '학부모 연락 기록', 'target': 'contacts', 'icon': '📞',
     'summary': '연락 요지, 보호자 의견, 합의 사항, 다음 연락을 정리합니다.'},
    {'key': 'meeting', 'title': '회의록 · 결정 · 후속 조치', 'target': 'meetings', 'icon': '🗒️',
     'summary': '안건별 논의와 결정, 담당자, 기한을 연결합니다.'},
    {'key': 'lesson', 'title': '수업 계획 · 성찰', 'target': 'lessons', 'icon': '📚',
     'summary': '학습 목표, 수업 흐름, 이해 확인, 다음 차시 수정을 기록합니다.'},
    {'key': 'assessment', 'title': '평가 계획 · 채점 기록', 'target': 'assessments', 'icon': '📋',
     'summary': '평가 목표와 기준, 운영 준비, 채점과 피드백을 정리합니다.'},
    {'key': 'homeroom', 'title': '조회 · 종례 기록', 'target': 'resources', 'icon': '☀️',
     'summary': '조회 전달, 하루 관찰, 종례 확인, 다음 날 준비를 남깁니다.'},
]

USAGE = [
    '이 페이지는 빈 양식 원본입니다. 사용할 때 페이지 메뉴에서 복제한 뒤 복사본을 연결된 기록 DB로 이동하고 제목과 속성을 입력하세요.',
    '반복해서 쓰려면 대상 DB의 새로 만들기 옆 메뉴에서 새 템플릿을 열고, 아래 작성 양식 본문을 복사해 넣으세요. 이 양식 모음의 페이지는 DB 템플릿 메뉴에 자동 등록되지 않습니다.',
    '날짜와 학생·학급은 기록할 때 입력하고, 해당 관계 속성이 있으면 그때 선택하세요. 원본 양식이나 DB 템플릿에 특정 학생·학급·날짜를 고정하지 않습니다.',
    '본문의 표와 체크박스는 작성용입니다. 체크해도 업무가 자동 생성되지 않습니다. 필요한 후속 업무는 업무·일정 DB에 직접 만드세요. 업무 연결 관계가 있는 DB는 그 속성으로 연결하고, 없는 DB는 업무 페이지 링크를 본문에 남기세요.',
]


def catalog():
    """Return fresh metadata for the six approved form definitions."""
    return copy.deepcopy(_CATALOG)


def selected_forms(c):
    """Apply explicit form choices, enabled target modules, and homeroom role."""
    all_forms = catalog()
    requested = c.get('forms', [form['key'] for form in all_forms])
    keys = {form['key'] for form in all_forms}
    if (not isinstance(requested, list) or any(not isinstance(key, str) for key in requested)
            or len(requested) != len(set(requested)) or set(requested) - keys):
        raise ValueError('forms에는 중복 없는 승인 양식 키 배열을 입력하세요: ' + ', '.join(form['key'] for form in all_forms))
    sources = {db['key'] for db in selected(c)[0]}
    homeroom = any(row.get('homeroom') is True for row in c.get('classes', []))
    return [form for form in all_forms if form['key'] in requested and form['target'] in sources
            and (form['key'] != 'homeroom' or homeroom)]


def _text(kind, text):
    return {'object': 'block', 'type': kind, kind: {'rich_text': rich(text)}}


def _heading(text):
    return _text('heading_2', text)


def _paragraph(text):
    return _text('paragraph', text)


def _todo(text):
    block = _text('to_do', text)
    block['to_do']['checked'] = False
    return block


def _table(headers, rows):
    return {'object': 'block', 'type': 'table', 'table': {
        'table_width': len(headers), 'has_column_header': True, 'has_row_header': False,
        'children': [{'object': 'block', 'type': 'table_row', 'table_row': {
            'cells': [rich(cell) if cell else [] for cell in row]}}
            for row in [headers, *rows]]}}


def _fields(names):
    return _table(['항목', '작성'], [[name, ''] for name in names])


def form_blocks(key):
    """Return a fresh, blank body without dates, people, links or DB relation values."""
    if key == 'counseling':
        return [
            _heading('기본 정보'),
            _fields(['상담 일시', '학생 · 학급', '상담 대상 · 방식', '장소', '기록자']),
            _paragraph('기록 DB의 학생·상담일·대상·주제 속성을 함께 입력하세요.'),
            _heading('상담 목적'),
            _paragraph('상담을 시작한 이유와 학생이 바라는 점을 적으세요.'),
            _heading('관찰과 대화'),
            _fields(['직접 관찰한 사실', '학생이 표현한 생각 · 감정', '교사의 해석 · 확인할 가설', '학생의 강점 · 필요한 지원']),
            _paragraph('관찰한 사실과 해석을 나누고, 필요한 대화만 요약하세요.'),
            _heading('합의 사항과 지원 계획'),
            _table(['할 일 · 지원', '담당', '확인할 시점', '확인 방법'], [['', '', '', ''], ['', '', '', '']]),
            _heading('후속 확인'),
            _fields(['다음 확인에서 물어볼 내용', '연결할 후속 업무', '확인 결과 · 다음 판단']),
            _todo('학생 관계와 상담일 속성을 입력했다.'),
            _todo('합의 사항과 후속 확인일을 기록했다.'),
        ]
    if key == 'guardian':
        return [
            _heading('연락 정보'),
            _fields(['연락 일시', '학생 · 학급', '연락 대상 · 학생과의 관계', '연락 수단', '기록자']),
            _paragraph('기록 DB의 학생·연락일·수단·상태 속성을 함께 입력하세요.'),
            _heading('연락 목적과 공유 내용'),
            _fields(['이번 연락의 목적', '학교에서 확인한 사실', '보호자에게 전달한 요지']),
            _heading('보호자의 의견'),
            _fields(['보호자가 전한 상황', '질문 · 요청', '추가로 확인할 내용']),
            _heading('합의와 다음 연락'),
            _table(['합의한 행동', '담당', '기한 · 후속일', '확인 방법'], [['', '', '', ''], ['', '', '', '']]),
            _fields(['다음 연락의 목적', '연결할 후속 업무', '연락 결과']),
            _todo('사실과 전달 요지를 구분해 기록했다.'),
            _todo('후속일과 담당자를 확인했다.'),
        ]
    if key == 'meeting':
        return [
            _heading('회의 정보'),
            _fields(['회의 일시 · 장소', '참석자', '진행자 · 기록자', '회의 목적', '관련 자료']),
            _paragraph('기록 DB의 일시·업무 분류·안건 속성을 함께 입력하세요.'),
            _heading('안건별 논의'),
            _table(['안건', '핵심 의견 · 근거', '결정 또는 보류', '추가 확인'], [['', '', '', ''], ['', '', '', '']]),
            _heading('결정 사항'),
            _table(['결정 내용', '적용 범위', '시행 시점', '공유 대상'], [['', '', '', ''], ['', '', '', '']]),
            _heading('후속 조치'),
            _table(['다음 행동', '담당자', '기한', '완료 확인 방법'], [['', '', '', ''], ['', '', '', '']]),
            _paragraph('실행할 항목은 업무·일정 DB에 직접 등록하고 후속 업무 관계로 연결하세요.'),
            _heading('다음 회의'),
            _fields(['보류한 안건', '다음에 확인할 결과', '다음 회의 준비']),
            _todo('결정 사항과 담당자·기한을 확인했다.'),
            _todo('필요한 사람에게 공유할 내용을 정리했다.'),
        ]
    if key == 'lesson':
        return [
            _heading('수업 정보'),
            _fields(['교과 · 학급 · 학기', '단원 · 차시', '계획일 · 실제 수업일', '성취기준', '준비물 · 자료']),
            _paragraph('기록 DB의 학급·교과·단원·계획일·차시 속성을 함께 입력하세요.'),
            _heading('학습 목표와 이해 확인'),
            _fields(['학생이 할 수 있어야 할 것', '선수 학습 · 예상 어려움', '목표 도달을 확인할 질문 · 산출물']),
            _heading('수업 흐름'),
            _table(['단계', '교사의 활동 · 발문', '학생의 활동', '자료 · 예상 시간'],
                   [['도입', '', '', ''], ['전개', '', '', ''], ['정리', '', '', '']]),
            _heading('지원과 조정'),
            _fields(['추가 설명 · 보조 자료', '심화 과제', '수업 중 조정할 기준']),
            _heading('수업 후 성찰'),
            _fields(['관찰한 학생의 이해 · 근거', '잘 작동한 활동', '예상과 달랐던 점', '다음 차시에서 바꿀 것']),
            _todo('실제 수업일과 완료 차시를 갱신했다.'),
            _todo('다음 수업과 보강 필요 여부를 기록했다.'),
        ]
    if key == 'assessment':
        return [
            _heading('평가 정보'),
            _fields(['교과 · 학급', '평가 유형 · 범위', '평가일 · 채점 마감', '성취기준', '평가 목표']),
            _paragraph('기록 DB의 학급·교과·유형·평가일·채점 마감 속성을 함께 입력하세요.'),
            _heading('평가 설계와 기준'),
            _table(['평가 요소', '과제 · 문항', '판단 기준', '배점 · 수준'], [['', '', '', ''], ['', '', '', '']]),
            _fields(['기준표 · 자료 위치', '학생에게 사전 안내할 내용', '응시 지원 · 대체 절차']),
            _heading('평가 전 확인'),
            _todo('평가 목표와 기준의 일치를 확인했다.'),
            _todo('운영 일정과 준비물을 확인했다.'),
            _heading('채점과 검토'),
            _table(['검토 항목', '확인 내용', '조치 · 담당'],
                   [['채점 기준 일관성', '', ''], ['누락 · 재확인', '', ''], ['피드백 준비', '', '']]),
            _paragraph('학생별 상세 채점 내용은 학교에서 정한 기록 위치에 두고, 이 페이지에는 진행 상황과 확인 사항을 요약하세요.'),
            _heading('피드백과 다음 수업'),
            _fields(['공통 강점 · 어려움', '학생에게 제공할 피드백', '후속 지도 · 재확인 계획']),
            _todo('대상 인원·채점 완료 수와 상태를 갱신했다.'),
        ]
    if key == 'homeroom':
        return [
            _heading('기본 정보'),
            _fields(['날짜 · 학급', '기록자', '오늘 우선 확인할 일']),
            _paragraph('자료 DB의 이름·학년도·쓰임을 입력하세요. 날짜와 학급은 이 본문에 적습니다.'),
            _heading('조회'),
            _fields(['오늘 전달할 내용', '준비물 · 제출물', '출결 관련 확인', '안전 · 생활 안내']),
            _heading('하루 관찰'),
            _table(['관찰한 사실', '도움이 필요한 부분', '확인 · 지원 행동'], [['', '', ''], ['', '', '']]),
            _paragraph('학생별로 이어서 확인할 내용은 학생 상담 기록에 별도로 남기세요.'),
            _heading('종례'),
            _fields(['마무리 전달 사항', '미확인 · 미완료 사항', '내일 준비', '후속 확인 시점']),
            _todo('조회와 종례에서 전달한 내용을 구분했다.'),
            _todo('추가 기록이나 후속 업무가 필요한 항목을 확인했다.'),
        ]
    raise ValueError('알 수 없는 양식 키입니다: ' + str(key))


def _target_link(title, identifier):
    block = _paragraph('기록할 곳: ' + title)
    block['paragraph']['rich_text'][0]['text']['link'] = {
        'url': 'https://www.notion.so/' + identifier.replace('-', '')}
    return block


def _checked_page(client, saved, parent, label):
    try:
        actual = client.request('GET', '/pages/' + saved['id'])
    except NotionError as error:
        if error.status == 404:
            raise ValueError(label + ': 저장된 페이지를 찾을 수 없어 재생성하지 않습니다.') from None
        raise
    if (not _same_id(actual.get('id'), saved['id'])
            or not _same_id(actual.get('parent', {}).get('page_id'), parent)
            or actual.get('archived') or actual.get('in_trash')):
        raise ValueError(label + ': 기존 페이지의 위치·휴지통 상태가 달라 추가 작성을 중단합니다.')


def install_forms(j, c, root, databases):
    """Create a journaled page library outside every data source and metric."""
    j.ready()
    forms = [form for form in selected_forms(c) if form['target'] in databases]
    if not forms:
        return None
    saved_library = j.data['objects'].get('forms:library')
    if saved_library:
        _checked_page(j.client, saved_library, root, '양식 모음')
    for form in forms:
        saved = j.data['objects'].get('forms:' + form['key'])
        if saved:
            if not saved_library:
                raise ValueError('양식 모음의 상위 페이지 설치 기록이 없습니다. 기존 상태를 확인하세요.')
            _checked_page(j.client, saved, saved_library['id'], form['title'])
    titles = {db['key']: db['title'] for db in selected(c)[0]}
    definition = fingerprint({'version': VERSION, 'root': root, 'forms': forms,
                              'bodies': {form['key']: form_blocks(form['key']) for form in forms},
                              'usage': USAGE,
                              'targets': {form['target']: databases[form['target']]['id'] for form in forms}})
    if j.data.get('forms_definition') and j.data['forms_definition'] != definition:
        raise ValueError('기존 양식 모음과 선택·본문·대상이 다릅니다. 기존 상태를 확인하고 새 수첩에는 별도 상태를 사용하세요.')
    j.data['forms_definition'] = definition
    j.save()
    library = j.create('forms:library', '/pages', {
        'parent': {'type': 'page_id', 'page_id': root}, 'icon': {'type': 'emoji', 'emoji': '📝'},
        'properties': {'title': {'title': rich('양식 모음')}}})
    writer = BlockJournal(j)
    intro = writer.append('forms:library:intro', library['id'], _paragraph(
        '필요한 양식을 열어 복제한 뒤 기록 DB로 이동하세요. 원본은 비워 두고 다음 기록에 다시 사용합니다.'))
    guide = writer.append('forms:library:guide', library['id'], _paragraph(
        '반복 사용은 대상 DB의 새 템플릿에 작성 양식 본문을 복사해 등록할 수 있습니다. 양식 원본은 실제 상담·수업·업무 기록에 포함되지 않습니다.'))
    entries = {}
    for form in forms:
        key, target = form['key'], form['target']
        page = j.create('forms:' + key, '/pages', {
            'parent': {'type': 'page_id', 'page_id': library['id']},
            'icon': {'type': 'emoji', 'emoji': form['icon']},
            'properties': {'title': {'title': rich(form['title'])}}})
        usage = [_heading('사용 방법'), _target_link(titles[target], databases[target]['id']),
                 *[_paragraph(text) for text in USAGE], _heading('작성 양식')]
        block_ids = []
        for part, blocks in (('usage', usage), ('body', form_blocks(key))):
            for index, block in enumerate(blocks):
                obj = writer.append(f'forms:{key}:{part}:{index}', page['id'], block)
                block_ids.append(obj['id'])
        entries[key] = {'page_id': page['id'], 'target': target,
                        'target_database_id': databases[target]['id'], 'block_ids': block_ids}
    j.data['forms'] = {'version': VERSION, 'library_id': library['id'], 'root_id': root,
                       'entries': entries, 'library_block_ids': [intro['id'], guide['id']]}
    j.save()
    return library['id']


def verify_forms(client, state):
    """Check library placement and owned blocks without requiring blank contents."""
    layout = state.get('forms')
    if not layout:
        return []
    issues = []

    def check_page(identifier, parent, label, block_ids):
        try:
            page = client.request('GET', '/pages/' + identifier)
        except NotionError as error:
            if error.status == 404:
                issues.append(label + ': 페이지를 찾을 수 없습니다.')
                return False
            raise
        if (not _same_id(page.get('parent', {}).get('page_id'), parent)
                or page.get('archived') or page.get('in_trash')):
            issues.append(label + ': 페이지 위치·휴지통 상태를 확인하세요.')
        actual = [block['id'] for block in children(client, identifier)]
        if [identifier for identifier in actual if identifier in block_ids] != block_ids:
            issues.append(label + ': 양식 안내 또는 본문 구역이 없거나 순서가 달라졌습니다.')
            return False
        return True

    check_page(layout['library_id'], layout['root_id'], '양식 모음', layout['library_block_ids'])
    names = {form['key']: form['title'] for form in catalog()}
    for key, entry in layout['entries'].items():
        label = names.get(key, key)
        if check_page(entry['page_id'], layout['library_id'], label, entry['block_ids']):
            target = client.request('GET', '/blocks/' + entry['block_ids'][1])
            links = {(item.get('text', {}).get('link') or {}).get('url')
                     for item in target.get('paragraph', {}).get('rich_text', [])}
            expected = 'https://www.notion.so/' + entry['target_database_id'].replace('-', '')
            if expected not in links:
                issues.append(label + ': 기록 DB 링크를 확인하세요.')
    return issues


def form_markdown(key):
    """Render the same blank definition as a portable Markdown counterpart."""
    entry = next((form for form in catalog() if form['key'] == key), None)
    if entry is None:
        raise ValueError('알 수 없는 양식 키입니다: ' + str(key))
    titles = {db['key']: db['title'] for db in blueprint()['databases']}
    lines = ['# ' + entry['title'], '', '기록할 곳: ' + titles[entry['target']], '', '## 사용 방법', '']
    for text in USAGE:
        lines.extend([text, ''])
    lines.extend(['## 작성 양식', ''])
    for block in form_blocks(key):
        kind, body = block['type'], block[block['type']]
        if kind == 'table':
            rows = [[(''.join(item['text']['content'] for item in cell)).replace('|', '\\|')
                     for cell in row['table_row']['cells']] for row in body['children']]
            lines.append('| ' + ' | '.join(rows[0]) + ' |')
            lines.append('| ' + ' | '.join(['---'] * body['table_width']) + ' |')
            lines.extend('| ' + ' | '.join(row) + ' |' for row in rows[1:])
        else:
            text = ''.join(item['text']['content'] for item in body['rich_text'])
            lines.append(('## ' if kind == 'heading_2' else '- [ ] ' if kind == 'to_do' else '') + text)
        lines.append('')
    return '\n'.join(lines)
