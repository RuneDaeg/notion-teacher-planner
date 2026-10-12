"""Opt-in workflow schema and record-page plans; never migrate existing data.

Relations use the documented data-source dual_property schema. Record-page
linked views and counts are handed to MCP/UI and remain pending until read back.
https://developers.notion.com/reference/property-object
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID


def workflow_catalog():
    return json.loads(Path(__file__).with_name('workflow_catalog.json').read_text(encoding='utf-8'))


def validate_workflow_selection(value, modules=None):
    catalog = workflow_catalog()
    if not isinstance(value, dict) or set(value) != {'preset', 'features'}:
        raise ValueError('workflows에는 preset과 features만 필요합니다.')
    preset, features = value['preset'], value['features']
    if not isinstance(preset, str) or preset not in catalog['presets']:
        raise ValueError('workflows.preset은 basic, recommended, custom 중 하나여야 합니다.')
    allowed = [item['key'] for item in catalog['features']]
    if (not isinstance(features, list) or any(not isinstance(key, str) for key in features)
            or len(features) != len(set(features)) or set(features) - set(allowed)):
        raise ValueError('workflows.features에는 중복 없는 지원 기능을 선택하세요.')
    if preset != 'custom' and set(features) != set(catalog['presets'][preset]['features']):
        raise ValueError('프리셋의 기능 목록이 다릅니다. 기능을 바꾸려면 custom을 선택하세요.')
    if modules is not None:
        enabled = {key for key, enabled in modules.items() if enabled is True} if isinstance(modules, dict) else set(modules)
        required = set(catalog['presets'][preset]['modules'])
        required.update(module for item in catalog['features'] if item['key'] in features
                        for module in item['requires_modules'])
        missing = required - enabled
        if missing:
            raise ValueError('선택한 연결 기능에 필요한 모듈을 먼저 선택하세요: ' + ', '.join(sorted(missing)))
    return {'preset': preset, 'features': sorted(features)}


def workflow_selection(config):
    return validate_workflow_selection(config['workflows'], config.get('modules', {})) if 'workflows' in config else None


def workflow_summary(selection):
    catalog = workflow_catalog()
    selected = validate_workflow_selection(selection)
    features = [item for item in catalog['features'] if item['key'] in selected['features']]
    modules = set(catalog['presets'][selected['preset']]['modules'])
    modules.update(module for item in features for module in item['requires_modules'])
    return {'preset': selected['preset'], 'title': catalog['presets'][selected['preset']]['title'],
            'features': features, 'modules': sorted(modules), 'featureCount': len(features)}


def _relation(target, reciprocal):
    return {'type': 'relation', 'target': target, 'reciprocal': reciprocal}


def _database(key, title, module, properties):
    return {'key': key, 'title': title, 'module': module, 'section': '학생 기록',
            'properties': {'이름': {'type': 'title'}, '학년도': {'type': 'number'},
                           '보관': {'type': 'checkbox'}, **properties}}


def workflow_blueprint(config, base):
    """Return a fresh overlay; absence of workflows exactly preserves old schemas."""
    selection = workflow_selection(config)
    result = deepcopy(base)
    if selection is None or not selection['features']:
        return result
    features = set(selection['features'])
    enabled = {'core'} | {key for key, value in config.get('modules', {}).items() if value is True}
    dbs = {item['key']: item for item in result['databases'] if item['module'] in enabled}

    def relation(source, name, target, reverse):
        if source in dbs and target in dbs:
            dbs[source]['properties'][name] = _relation(target, reverse)

    if 'student_history' in features:
        for source, reverse in (('counseling', '상담 기록'), ('attendance', '출결 기록'),
                                ('submissions', '제출 기록'), ('contacts', '학부모 연락')):
            relation(source, '학생', 'students', reverse)
    if 'class_hub' in features:
        for source, reverse in (('students', '학생 명단'), ('timetable', '수업 시간표'),
                                ('lessons', '수업 진도'), ('agenda', '업무·일정'),
                                ('attendance', '출결 기록'), ('submissions', '제출 기록'),
                                ('assessments', '평가 목록')):
            relation(source, '학급', 'classes', reverse)
    if 'lesson_links' in features:
        relation('lessons', '시간표', 'timetable', '수업 진도')
        relation('lessons', '자료', 'resources', '수업 진도')
    if 'followups' in features:
        for source, reverse in (('counseling', '상담 기록'), ('contacts', '학부모 연락'), ('meetings', '회의록')):
            relation(source, '후속 업무', 'agenda', reverse)
        for source, date_name in (('counseling', '후속 확인일'), ('contacts', '후속일')):
            if source in dbs:
                dbs[source]['properties']['후속 확인 필요'] = {'type': 'formula', 'expression':
                    f'if(or(empty(prop("{date_name}")), prop("상태") == "종결"), false, dateStart(prop("{date_name}")) <= today())'}
                result['views'].append({'key': 'workflow_' + source + '_followup', 'source': source,
                    'name': '후속 확인할 기록', 'type': 'table', 'filter': {'and': [
                        {'property': '보관', 'checkbox': {'equals': False}},
                        {'property': '후속 확인 필요', 'formula': {'checkbox': {'equals': True}}}]},
                    'sorts': [{'property': date_name, 'direction': 'ascending'}],
                    'show': ['이름', '학생', date_name, '상태', '후속 업무']})
    if 'para_hub' in features:
        relation('projects', '영역', 'areas', '프로젝트')
        for source, reverse in (('agenda', '업무·일정'), ('resources', '자료')):
            relation(source, '영역', 'areas', reverse)
            relation(source, '프로젝트', 'projects', reverse)
        relation('meetings', '프로젝트', 'projects', '회의록')
    if 'submissions' in features:
        item = _database('submission_items', '제출 항목', 'attendance', {
            '학급': _relation('classes', '제출 항목'), '대상 학생': _relation('students', '제출 대상 항목'),
            '대상 확정일': {'type': 'date'}, '마감': {'type': 'date'},
            '상태': {'type': 'select', 'options': ['준비', '수집 중', '마감', '취소']},
            '업무·일정': _relation('agenda', '제출 항목'), '안내': {'type': 'rich_text'}})
        result['databases'].append(item)
        dbs[item['key']] = item
        relation('submissions', '제출 항목', 'submission_items', '제출 기록')
        dbs['submissions']['properties']['기록 키'] = {'type': 'rich_text'}
    if 'assessment_scope' in features:
        relation('assessments', '수업 진도', 'lessons', '평가 범위')
        relation('assessments', '기준표', 'resources', '관련 평가')
    if 'assessment_results' in features:
        item = _database('assessment_results', '학생별 평가 결과', 'assessment', {
            '평가': _relation('assessments', '학생별 결과'), '학생': _relation('students', '평가 결과'),
            '기록 키': {'type': 'rich_text'}, '채점 완료': {'type': 'checkbox'},
            '결시': {'type': 'checkbox'}, '점수': {'type': 'number'},
            '피드백': {'type': 'rich_text'}, '확인일': {'type': 'date'}})
        result['databases'].append(item)
        dbs[item['key']] = item
        relation('assessments', '대상 학생', 'students', '평가 대상')
        dbs['assessments']['properties']['대상 확정일'] = {'type': 'date'}
        dbs['assessments']['properties']['만점'] = {'type': 'number'}
    if 'staff_links' in features:
        for source, reverse in (('agenda', '연결 업무'), ('meetings', '담당 회의'), ('accounts', '관리 계정')):
            # Keep free-text 담당/관리 담당; adding this relation never migrates it.
            relation(source, '담당 교직원', 'staff', reverse)
    for item in result['databases']:
        if item['key'] in ('submission_items', 'assessment_results'):
            for archived, prefix, name in ((False, 'active_', '전체 · 현재 학년도'), (True, 'archive_', '보관함')):
                result['views'].append({'key': prefix + item['key'], 'source': item['key'],
                    'name': name, 'type': 'table', 'filter': {'property': '보관', 'checkbox': {'equals': archived}}})
    if 'submissions' in features:
        result['views'].append({'key': 'workflow_submissions_pending', 'source': 'submissions',
            'name': '미제출·보완 요청', 'type': 'table', 'filter': {'and': [
                {'property': '보관', 'checkbox': {'equals': False}}, {'or': [
                    {'property': '상태', 'select': {'equals': '미제출'}},
                    {'property': '상태', 'select': {'equals': '보완 요청'}}]}]},
            'sorts': [{'property': '마감', 'direction': 'ascending'}],
            'show': ['이름', '제출 항목', '학생', '학급', '상태', '마감']})
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def workflow_plan(config):
    """Machine-readable native-view handoff, bound to selected schemas and rules."""
    from .model import blueprint
    selection = workflow_selection(config)
    features = set(selection['features']) if selection else set()
    schema = workflow_blueprint(config, blueprint())
    enabled = {'core'} | {key for key, value in config.get('modules', {}).items() if value is True}
    dbs = {item['key']: item for item in schema['databases'] if item['module'] in enabled}
    result = {'version': 1, 'selection': selection, 'applied': False,
              'record_views_installed': False, 'record_views': [], 'record_rules': [], 'counters': [],
              'handoff': ['기존 기록과 관계 ID를 보존하고 이름으로 자동 연결하지 않습니다.',
                          '새 Python 설치는 선택 스키마와 원본 보기를 만듭니다. 상세 페이지 보기는 MCP/UI에서 별도로 구성합니다.',
                          '관계만 연결해도 날짜·업무·출결·제출 행이 자동 생성되지 않습니다.',
                          '기존 수첩에 적용할 때 설정·설치 상태를 초기화하거나 재설치하지 않습니다.'],
              'verification': {'command': 'verify-workflows --config CONFIG --snapshot SNAPSHOT',
                  'required': ['current_page_relation_filter', 'data_source_id', 'parent_page_id',
                               'sorts', 'visible_properties', 'ui_checked', 'new_record_checked',
                               'host_record_counts', 'record_rule_checks', 'counter_checks'],
                  'host_examples': 'two_distinct_records_when_available; one_requires_host_record_count_1; zero_is_pending',
                  'record_rule_checks': 'exact_rule_and_source_id; positive_checked_record_count; empty_violations; readback_checked; new_record_checked; legacy_rows_preserved',
                  'counter_checks': 'exact_counter_and_source_ids; roster_completed_missing_ids; displayed_counts_and_percentage; empty_duplicate_record_keys; readback_checked; ui_checked; includes_unrecorded_students',
                  'status': 'pending' if features else 'not_requested'}}

    def view(host, source, prop, *, all_time=False, variant=None, filters=None, name=None, sort_property=None):
        if host not in dbs or source not in dbs:
            return
        key = host + ':' + source + ':' + prop + (':' + variant if variant else '')
        if any(item['key'] == key for item in result['record_views']):
            return
        properties = dbs[source]['properties']
        date = sort_property or next((name for name in ('상담일', '연락일', '날짜', '수업일', '평가일', '확인일', '제출일', '일시', '마감', '계획일', '기간') if name in properties), '이름')
        clauses = [{'property': prop, 'relation': {'contains': '$current_page'}}]
        if not all_time:
            clauses.append({'property': '보관', 'checkbox': {'equals': False}})
        clauses.extend(filters or [])
        result['record_views'].append({'key': key, 'host': host, 'source': source,
            'name': name or dbs[source]['title'], 'type': 'table', 'filter': {'and': clauses},
            'sorts': [{'property': date, 'direction': 'descending' if all_time else 'ascending'}],
            'show': list(dict.fromkeys(['이름', prop] + [name for name in ('학생', '학급', date, '상태', '채점 완료', '점수', '피드백', '다음 행동') if name in properties])),
            'scope': 'all_linked_history' if all_time else 'active_linked_records',
            'apply_with': 'MCP_or_Notion_UI', 'status': 'pending'})

    if 'student_history' in features:
        for source in ('counseling', 'attendance', 'submissions', 'contacts'):
            view('students', source, '학생', all_time=True)
        view('students', 'submissions', '학생', variant='pending', name='미제출·보완 요청', sort_property='마감', filters=[
            {'or': [{'property': '상태', 'select': {'equals': '미제출'}},
                    {'property': '상태', 'select': {'equals': '보완 요청'}}]}])
    if 'class_hub' in features:
        for source in ('students', 'timetable', 'lessons', 'agenda', 'attendance', 'submissions', 'assessments'):
            view('classes', source, '학급')
        view('classes', 'assessments', '학급', variant='upcoming', name='30일 안에 예정 평가',
             filters=[{'property': '평가 예정', 'formula': {'checkbox': {'equals': True}}}])
    if 'lesson_links' in features:
        for target, prop in (('timetable', '시간표'), ('resources', '자료')):
            view(target, 'lessons', prop)
            view('lessons', target, '수업 진도')
    if 'followups' in features:
        for source, reverse in (('counseling', '상담 기록'), ('contacts', '학부모 연락'), ('meetings', '회의록')):
            if source in dbs:
                view('agenda', source, '후속 업무')
                view(source, 'agenda', reverse, name='미완료 후속 업무', filters=[
                    {'property': '상태', 'select': {'does_not_equal': '완료'}},
                    {'property': '상태', 'select': {'does_not_equal': '취소'}}])
    if 'para_hub' in features:
        view('areas', 'projects', '영역')
        for source in ('agenda', 'resources'):
            view('areas', source, '영역')
            view('projects', source, '프로젝트')
        view('projects', 'meetings', '프로젝트')
    if 'submissions' in features:
        view('submission_items', 'submissions', '제출 항목')
        view('submission_items', 'submissions', '제출 항목', variant='pending', name='미제출·보완 요청', sort_property='마감', filters=[
            {'or': [{'property': '상태', 'select': {'equals': '미제출'}},
                    {'property': '상태', 'select': {'equals': '보완 요청'}}]}])
        result['record_rules'].append({'source': 'submissions', 'identity': ['제출 항목', '학생'],
            'exactly_one_relation': ['제출 항목', '학생'], 'key_property': '기록 키',
            'key_format': 'submission_item_page_id:student_page_id', 'legacy_rows': 'keep_unlinked_until_reviewed'})
        result['counters'].append({'host': 'submission_items', 'source': 'submissions',
            'relation': '제출 항목', 'roster_property': '대상 학생', 'roster_date': '대상 확정일',
            'denominator': 'distinct_confirmed_target_student_ids',
            'numerator': 'distinct_target_student_ids_with_제출_or_확인_완료',
            'missing': 'target_students_without_accepted_submission_including_no_row',
            'zero_denominator': '미설정', 'duplicate_rows': 'block_until_resolved',
            'percentage': 'round(100 * numerator / denominator, 1); no calculation until roster confirmed',
            'method': 'verified_filtered_views_and_roster_comparison', 'automatic_rollup': False})
    if 'assessment_scope' in features:
        view('assessments', 'lessons', '평가 범위')
        view('lessons', 'assessments', '수업 진도')
        view('resources', 'assessments', '기준표')
        view('assessments', 'resources', '관련 평가')
    if 'assessment_results' in features:
        view('assessments', 'assessment_results', '평가')
        view('students', 'assessment_results', '학생', all_time=True)
        result['record_rules'].append({'source': 'assessment_results', 'identity': ['평가', '학생'],
            'exactly_one_relation': ['평가', '학생'], 'key_property': '기록 키',
            'key_format': 'assessment_page_id:student_page_id', 'legacy_rows': 'no_inferred_student_scores',
            'score_rule': 'ungraded_or_absent_is_blank_not_zero; graded_score_between_zero_and_assessment_max'})
        result['counters'].append({'host': 'assessments', 'source': 'assessment_results',
            'relation': '평가', 'roster_property': '대상 학생', 'roster_date': '대상 확정일',
            'denominator': 'distinct_confirmed_target_student_ids',
            'numerator': 'distinct_target_student_ids_with_채점_완료_and_not_결시',
            'missing': 'target_students_without_completed_result_including_no_row',
            'zero_denominator': '미설정', 'duplicate_rows': 'block_until_resolved',
            'percentage': 'round(100 * numerator / denominator, 1); no calculation until roster confirmed',
            'method': 'verified_filtered_views_and_roster_comparison', 'automatic_rollup': False,
            'existing_counts': '대상 인원·채점 완료·채점률 유지; 자동 덮어쓰기 없음'})
    if 'staff_links' in features:
        for source in ('agenda', 'meetings', 'accounts'):
            view('staff', source, '담당 교직원')
    result['schema'] = {'databases': list(dbs.values()), 'views': [view_ for view_ in schema['views'] if view_['source'] in dbs]}
    result['plan_digest'] = _digest(result)
    return result


def verify_workflow_evidence(config, snapshot):
    """Check supplied API/UI evidence, without querying or changing Notion."""
    plan = workflow_plan(config)
    if not isinstance(snapshot, dict):
        raise ValueError('상세보기 증거는 JSON 객체여야 합니다.')
    issues, pending, checked = [], [], []
    checked_rules, checked_counters = 0, 0
    if snapshot.get('plan_digest') != plan['plan_digest']:
        issues.append('검토한 연결 기능 계획과 증거의 plan_digest가 다릅니다.')
    bindings = snapshot.get('data_sources', {})
    if not isinstance(bindings, dict):
        raise ValueError('data_sources에는 DB 키와 실제 원본 ID가 필요합니다.')
    def identity(value):
        try:
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError):
            return None
    views = snapshot.get('views', [])
    if not isinstance(views, list) or any(not isinstance(item, dict) for item in views):
        raise ValueError('views에는 실제 페이지에서 다시 읽은 보기 목록이 필요합니다.')
    host_counts = snapshot.get('host_record_counts', {})
    if not isinstance(host_counts, dict):
        raise ValueError('host_record_counts에는 실제 상세 페이지의 원본별 전체 기록 수가 필요합니다.')

    def required_examples(host):
        count = host_counts.get(host)
        if type(count) is not int or count < 0:
            pending.append(host + ': 실제 원본의 전체 기록 수 확인 필요')
            return 2
        if count == 0:
            pending.append(host + ': 실제 상세 페이지가 없어 보기·집계를 아직 확인할 수 없습니다.')
        return max(1, min(2, count))

    known = {view['key'] for view in plan['record_views']}
    if any(item.get('plan_key') not in known for item in views):
        issues.append('선택하지 않은 상세보기 증거가 있습니다.')
    for expected in plan['record_views']:
        matches = [item for item in views if item.get('plan_key') == expected['key']]
        if not matches:
            pending.append(expected['key'] + ': 실제 상세보기 조회·화면 증거가 필요합니다.')
            continue
        required = required_examples(expected['host'])
        seen = set()
        for actual in matches:
            host = identity(actual.get('current_page_id'))
            if not host or host in seen:
                issues.append(expected['key'] + ': 현재 페이지 ID 누락·중복')
                continue
            seen.add(host)
            expected_source = identity(bindings.get(expected['source']))
            source = identity(actual.get('data_source_id'))
            host_source = identity(bindings.get(expected['host']))
            if (not expected_source or source != expected_source or identity(actual.get('parent_page_id')) != host
                    or not host_source or identity(actual.get('host_data_source_id')) != host_source):
                issues.append(expected['key'] + ': 원본 또는 현재 상세 페이지 불일치')
            wanted = json.loads(json.dumps(expected['filter']).replace('$current_page', actual['current_page_id']))
            if actual.get('type') != expected['type'] or actual.get('filter') != wanted:
                issues.append(expected['key'] + ': 현재 페이지 관계 필터·보기 유형 불일치')
            if actual.get('sorts') != expected['sorts'] or actual.get('visible_properties') != expected['show']:
                issues.append(expected['key'] + ': 정렬·표시 속성 불일치')
            if actual.get('ui_checked') is not True or actual.get('new_record_checked') is not True:
                pending.append(expected['key'] + ': 실제 화면·신규 기록 연결 확인 필요')
            checked.append(expected['key'])
        if len(seen) < required:
            pending.append(expected['key'] + ': 서로 다른 상세 페이지 ' + str(required) + '개 확인 필요')

    # Rule/counter checks are deliberately separate from view evidence. A valid
    # relation-filter screenshot alone cannot establish uniqueness or a denominator.
    def checks(name):
        value = snapshot.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(name + '에는 실제 확인한 결과 목록이 필요합니다.')
        return value

    rule_checks = checks('record_rule_checks')
    known_sources = {item['source'] for item in plan['record_rules']}
    if any(item.get('source') not in known_sources for item in rule_checks):
        issues.append('선택하지 않은 기록 규칙 증거가 있습니다.')
    for rule in plan['record_rules']:
        matches = [item for item in rule_checks if item.get('source') == rule['source']]
        if not matches:
            pending.append(rule['source'] + ': 기록 키·관계 개수·중복·기존 기록 보존 검사 필요')
            continue
        if len(matches) != 1:
            issues.append(rule['source'] + ': 기록 규칙 검사 증거 중복')
            continue
        actual = matches[0]
        source_id = identity(bindings.get(rule['source']))
        if not source_id or identity(actual.get('data_source_id')) != source_id or actual.get('rule') != rule:
            issues.append(rule['source'] + ': 검사한 원본·기록 규칙 불일치')
        if actual.get('violations') != []:
            issues.append(rule['source'] + ': 기록 규칙 위반 목록이 비어 있지 않거나 누락됨')
        count = actual.get('checked_record_count')
        if (type(count) is not int or count < 1 or any(actual.get(flag) is not True for flag in
                ('readback_checked', 'new_record_checked', 'legacy_rows_preserved'))):
            pending.append(rule['source'] + ': 실제 기록·신규 기록·기존 자료 보존 확인 필요')
        checked_rules += 1

    counter_checks = checks('counter_checks')
    known_hosts = {item['host'] for item in plan['counters']}
    if any(item.get('host') not in known_hosts for item in counter_checks):
        issues.append('선택하지 않은 집계 증거가 있습니다.')

    def student_ids(actual, field, label):
        values = actual.get(field)
        if not isinstance(values, list):
            issues.append(label + ': ' + field + ' 목록 누락')
            return set()
        ids = [identity(value) for value in values]
        if None in ids or len(ids) != len(set(ids)):
            issues.append(label + ': ' + field + ' ID 오류·중복')
        return set(ids) - {None}

    for counter in plan['counters']:
        label = counter['host']
        matches = [item for item in counter_checks if item.get('host') == label]
        if not matches:
            pending.append(label + ': 대상 학생 명단·미기록 학생·분모·비율의 실제 집계 검사 필요')
            continue
        required = required_examples(label)
        seen = set()
        for actual in matches:
            page = identity(actual.get('current_page_id'))
            if not page or page in seen:
                issues.append(label + ': 집계 현재 페이지 ID 누락·중복')
                continue
            seen.add(page)
            host_id, source_id = identity(bindings.get(label)), identity(bindings.get(counter['source']))
            if (not host_id or not source_id or identity(actual.get('host_data_source_id')) != host_id
                    or identity(actual.get('source_data_source_id')) != source_id or actual.get('counter') != counter):
                issues.append(label + ': 집계 원본·규칙 불일치')
            targets = student_ids(actual, 'target_student_ids', label)
            completed = student_ids(actual, 'completed_student_ids', label)
            missing = student_ids(actual, 'missing_student_ids', label)
            if not completed <= targets or missing != targets - completed:
                issues.append(label + ': 대상 외 학생 포함 또는 미기록 학생을 포함한 미완료 명단 불일치')
            if actual.get('duplicate_record_keys') != []:
                issues.append(label + ': 중복 기록 키가 있거나 검사 결과 누락')
            if type(actual.get('roster_confirmed')) is not bool:
                pending.append(label + ': 대상 확정 여부 확인 필요')
            percentage = (round(100 * len(completed) / len(targets), 1)
                          if targets and actual.get('roster_confirmed') is True else '미설정')
            shown = actual.get('displayed', {})
            ratio = shown.get('percentage') if isinstance(shown, dict) else None
            ratio_valid = (ratio == percentage if isinstance(percentage, str) else
                           type(ratio) in (int, float) and math.isfinite(ratio) and abs(ratio - percentage) < 1e-9)
            if (not isinstance(shown, dict) or type(shown.get('denominator')) is not int
                    or type(shown.get('numerator')) is not int or shown.get('denominator') != len(targets)
                    or shown.get('numerator') != len(completed) or not ratio_valid):
                issues.append(label + ': 화면의 분자·분모·비율이 확인한 학생 명단과 다름')
            if any(actual.get(flag) is not True for flag in ('readback_checked', 'ui_checked', 'includes_unrecorded_students')):
                pending.append(label + ': 집계 원본·화면·기록 없는 대상 학생 확인 필요')
            checked_counters += 1
        if len(seen) < required:
            pending.append(label + ': 서로 다른 집계 상세 페이지 ' + str(required) + '개 확인 필요')
    return {'plan_digest': plan['plan_digest'], 'evidence_valid': not issues and not pending,
            'checked_views': len(checked), 'checked_record_rules': checked_rules, 'checked_counters': checked_counters,
            'issues': issues, 'pending': list(dict.fromkeys(pending)),
            'applied': False, 'live_notion_verified': False,
            'method': 'supplied_readback_and_ui_evidence_only'}
