"""One reviewed structural design for browser previews and native handoffs.

No credentials, personal settings, Notion calls, or writes belong in this module.
Hashes detect accidental changes; they are not signatures or proof of consent.
"""
import hashlib
import json
from pathlib import Path

from .dashboard import dashboard_config, layout_spec
from .forms import selected_forms
from .layout_contract import active_sections_for_config, load_contract, render_page, resolve_page_layout
from .model import blueprint


MODULES = ('accounts', 'assessment', 'attendance', 'contact', 'meeting', 'staff')
FORMS = ('assessment', 'counseling', 'guardian', 'homeroom', 'lesson', 'meeting')
PAGE_KEYS = ('home', 'classroom', 'teaching', 'planning')
MAX_INPUT_BYTES = 2 * 1024 * 1024
SELECTION_KEYS = {'modules', 'forms', 'homeroom', 'school_links', 'periods'}


def _json_values(value):
    # JSON.stringify emits an integral number as 100, not Python's 100.0.
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_values(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_json(value):
    return json.dumps(_json_values(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def default_selection():
    return {'modules': [], 'forms': [], 'homeroom': False, 'school_links': False, 'periods': 7}


def validate_selection(selection):
    if not isinstance(selection, dict) or set(selection) != SELECTION_KEYS:
        raise ValueError('미리보기 선택에는 modules, forms, homeroom, school_links, periods만 필요합니다.')
    result = dict(selection)
    for key, allowed in (('modules', MODULES), ('forms', FORMS)):
        values = selection[key]
        if (not isinstance(values, list) or any(not isinstance(item, str) for item in values)
                or len(values) != len(set(values)) or set(values) - set(allowed)):
            raise ValueError(f'{key}: 중복 없는 지원 항목 목록이 필요합니다.')
        result[key] = sorted(values)
    if any(type(selection[key]) is not bool for key in ('homeroom', 'school_links')):
        raise ValueError('담임과 학교 링크 선택은 true/false여야 합니다.')
    if type(selection['periods']) is not int or not 1 <= selection['periods'] <= 20:
        raise ValueError('교시 수는 1~20 정수여야 합니다.')
    for form, module in (('guardian', 'contact'), ('meeting', 'meeting'), ('assessment', 'assessment')):
        if form in result['forms'] and module not in result['modules']:
            raise ValueError(f'{form} 양식에 필요한 {module} 기능을 먼저 선택하세요.')
    if 'homeroom' in result['forms'] and not result['homeroom']:
        raise ValueError('조회·종례 양식에는 담임 선택이 필요합니다.')
    return result


def _config(selection):
    """Only the minimum nonpersonal selectors used by the existing resolver."""
    return {
        'modules': {key: key in selection['modules'] for key in MODULES},
        'forms': selection['forms'], 'classes': [{'homeroom': selection['homeroom']}],
        'dashboard': {'periods': selection['periods'], 'links': {
            'neis': 'https://example.invalid/' if selection['school_links'] else ''}},
    }


def selection_from_config(config):
    settings = dashboard_config(config)
    return validate_selection({
        'modules': sorted(key for key in MODULES if config.get('modules', {}).get(key) is True),
        'forms': sorted(item['key'] for item in selected_forms(config)),
        'homeroom': any(item.get('homeroom') is True for item in config.get('classes', [])),
        'school_links': any(settings['links'].values()), 'periods': settings['periods'],
    })


def build_catalog():
    contract, data, dashboard = load_contract(), blueprint(), layout_spec()
    return _json_values({
        'format': 'teacher-planner-preview-catalog', 'version': 1,
        'source_digest': digest({'contract': contract, 'blueprint': data, 'dashboard': dashboard}),
        'contract': contract, 'databases': data['databases'], 'views': data['views'],
        'workspace': dashboard['workspace'],
    })


def create_bundle(selection=None):
    selection = validate_selection(default_selection() if selection is None else selection)
    catalog = build_catalog()
    config = _config(selection)
    pages = {key: resolve_page_layout(key, active_sections_for_config(config, key)) for key in PAGE_KEYS}
    referenced = {view_key for page in pages.values() for section in page['sections'].values()
                  for view_key in section.get('view_keys', [])}
    views = {view['key']: view for view in catalog['views'] if view['key'] in referenced}
    if referenced != set(views):
        raise ValueError('배치 계약의 보기와 원본 명세가 일치하지 않습니다.')
    bundle = _json_values({
        'format': 'teacher-planner-reviewed-layout', 'version': 1,
        'source_digest': catalog['source_digest'], 'selection': selection,
        'page_settings': catalog['contract']['page_settings'], 'pages': pages,
        'views': views, 'applied': False,
    })
    bundle['bundle_digest'] = digest(bundle)
    return bundle


make_bundle = create_bundle


def validate_bundle(bundle):
    if not isinstance(bundle, dict):
        raise ValueError('미리보기 설계는 JSON 객체여야 합니다.')
    expected = create_bundle(bundle.get('selection'))
    if bundle.get('source_digest') != expected['source_digest']:
        raise ValueError('공통 설계 버전이 달라졌습니다. 현재 웹에서 다시 미리보기를 확인하세요.')
    # Reconstruct all values. A caller recomputing a hash cannot legitimize a
    # changed width, omitted module, unexpected view, or made-up API setting.
    if canonical_json(bundle) != canonical_json(expected):
        raise ValueError('검토한 선택·배치·보기 또는 설계 지문이 일치하지 않습니다. 다시 미리보기를 확인하세요.')
    return expected


def read_json(path):
    path = Path(path)
    with path.open('rb') as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('입력 파일은 2 MiB 이하여야 합니다.')
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('JSON에 중복된 필드 이름이 있습니다.')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('유효한 JSON 숫자가 필요합니다.')))


def compile_bundle(bundle, sections):
    """Resolve native fragments into all four reviewed page layouts, offline.

    The caller must fetch these fragments from the target notebook. Their origin,
    ownership, latest state, and IDs cannot be attested by this pure compiler.
    """
    reviewed = validate_bundle(bundle)
    if (not isinstance(sections, dict) or set(sections) != {'pages'}
            or not isinstance(sections['pages'], dict) or set(sections['pages']) != set(PAGE_KEYS)):
        raise ValueError('sections에는 네 페이지를 모두 담은 pages 객체만 필요합니다.')
    rendered = {}
    for key in PAGE_KEYS:
        supplied = sections['pages'][key]
        expected = set(reviewed['pages'][key]['sections'])
        if not isinstance(supplied, dict) or set(supplied) != expected:
            raise ValueError(f'{key}: 검토한 구역과 실제 구역 키가 일치해야 합니다. 사용자 구역을 누락하지 마세요.')
        for value in supplied.values():
            if value == '' or value == [] or (isinstance(value, str) and not value.strip()):
                raise ValueError(f'{key}: 검토한 구역의 빈 내용은 생략할 수 없습니다.')
        rendered[key] = render_page(key, supplied)
    return rendered
