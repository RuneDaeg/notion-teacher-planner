import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.cli import main, verify_remote
from teacher_planner.install import Journal, fingerprint, install
from teacher_planner.model import blueprint, config, schema, selected
from teacher_planner.workflows import (validate_workflow_selection, verify_workflow_evidence,
    workflow_blueprint, workflow_catalog, workflow_plan, workflow_selection, workflow_summary)
from test_planner import FakeNotion


ROOT = Path(__file__).resolve().parents[1]


def settings(features=None, preset='custom', all_modules=False):
    result = config(ROOT / 'config.example.json')
    result['demo'] = False
    result['modules'] = {key: all_modules for key in ('attendance', 'assessment', 'contact', 'meeting', 'staff', 'accounts')}
    if features is not None:
        result['workflows'] = {'preset': preset, 'features': features}
    return result


def all_settings():
    return settings([item['key'] for item in workflow_catalog()['features']], all_modules=True)


def view_evidence(c, host_count=2):
    plan = workflow_plan(c)
    refs = {db['key']: str(uuid4()) for db in plan['schema']['databases']}
    pages = {db['key']: [str(uuid4()) for _ in range(host_count)] for db in plan['schema']['databases']}
    views, counters = [], []
    for view in plan['record_views']:
        for page in pages[view['host']]:
            views.append({'plan_key': view['key'], 'current_page_id': page, 'host_data_source_id': refs[view['host']],
                          'parent_page_id': page, 'data_source_id': refs[view['source']], 'type': view['type'],
                          'filter': json.loads(json.dumps(view['filter']).replace('$current_page', page)),
                          'sorts': view['sorts'], 'visible_properties': view['show'],
                          'ui_checked': True, 'new_record_checked': True})
    for counter in plan['counters']:
        for page in pages[counter['host']]:
            students = [str(uuid4()) for _ in range(3)]
            counters.append({'host': counter['host'], 'current_page_id': page,
                'host_data_source_id': refs[counter['host']], 'source_data_source_id': refs[counter['source']],
                'counter': counter, 'target_student_ids': students, 'completed_student_ids': students[:2],
                'missing_student_ids': students[2:], 'duplicate_record_keys': [], 'roster_confirmed': True,
                'displayed': {'denominator': 3, 'numerator': 2, 'percentage': 66.7},
                'readback_checked': True, 'ui_checked': True, 'includes_unrecorded_students': True})
    rules = [{'source': rule['source'], 'data_source_id': refs[rule['source']], 'rule': rule,
              'checked_record_count': 3, 'violations': [], 'readback_checked': True,
              'new_record_checked': True, 'legacy_rows_preserved': True} for rule in plan['record_rules']]
    return {'plan_digest': plan['plan_digest'], 'data_sources': refs, 'views': views,
            'host_record_counts': {key: host_count for key in refs}, 'record_rule_checks': rules, 'counter_checks': counters}


class WorkflowChoiceTests(unittest.TestCase):
    def test_catalog_has_nine_exact_features_and_requested_presets(self):
        catalog = workflow_catalog()
        self.assertEqual(1, catalog['version'])
        self.assertEqual(['student_history', 'class_hub', 'lesson_links', 'followups', 'para_hub',
                          'submissions', 'assessment_scope', 'assessment_results', 'staff_links'],
                         [item['key'] for item in catalog['features']])
        self.assertEqual(['기본 설정', '추천 설정', '커스텀'], [item['title'] for item in catalog['presets'].values()])

    def test_omitted_workflows_and_explicit_empty_custom_keep_legacy_databases(self):
        legacy, empty = settings(), settings([])
        self.assertIsNone(workflow_selection(legacy))
        self.assertEqual(selected(legacy), selected(empty))
        self.assertEqual(blueprint(), workflow_blueprint(legacy, blueprint()))
        self.assertEqual(blueprint(), workflow_blueprint(empty, blueprint()))
        self.assertEqual([], workflow_plan(empty)['record_views'])

    def test_basic_needs_no_optional_module(self):
        features = workflow_catalog()['presets']['basic']['features']
        c = settings(features, 'basic')
        self.assertEqual(9, len(selected(c)[0]))
        self.assertEqual(sorted(features), workflow_selection(c)['features'])
        self.assertEqual([], workflow_summary(c['workflows'])['modules'])

    def test_recommended_requires_all_four_modules_without_enabling_them(self):
        features = workflow_catalog()['presets']['recommended']['features']
        for missing in ('attendance', 'assessment', 'contact', 'meeting'):
            c = settings(features, 'recommended', all_modules=True)
            c['modules'][missing] = False
            before = copy.deepcopy(c)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                selected(c)
            self.assertEqual(before, c)

    def test_hard_dependencies_and_optional_modules(self):
        for feature, module in (('submissions', 'attendance'), ('assessment_scope', 'assessment'),
                                ('assessment_results', 'assessment'), ('staff_links', 'staff')):
            c = settings([feature])
            with self.subTest(feature=feature), self.assertRaises(ValueError):
                selected(c)
            c['modules'][module] = True
            self.assertIn(feature, workflow_selection(c)['features'])
        for feature in ('student_history', 'class_hub', 'lesson_links', 'followups', 'para_hub'):
            self.assertEqual(9, len(selected(settings([feature]))[0]))

    def test_invalid_or_inconsistent_choices_are_rejected(self):
        for value in (None, [], {}, {'preset': 'other', 'features': []},
                      {'preset': 'basic', 'features': []}, {'preset': 'custom', 'features': ['unknown']},
                      {'preset': 'custom', 'features': ['class_hub', 'class_hub']},
                      {'preset': 'custom', 'features': [True]}, {'preset': 'custom', 'features': [], 'extra': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_workflow_selection(value)

    def test_config_file_rejects_dependency_before_api_work(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(settings(['assessment_results'])))
            with patch('teacher_planner.cli.Client', side_effect=AssertionError('No API calls')), redirect_stderr(io.StringIO()):
                self.assertEqual(1, main(['install', '--config', str(path), '--apply']))


class WorkflowSchemaTests(unittest.TestCase):
    def test_all_features_add_exact_two_databases_and_preserve_base_blueprint(self):
        before = blueprint()
        c = all_settings()
        dbs, views = selected(c)
        self.assertEqual(18, len(dbs))
        self.assertEqual({'submission_items', 'assessment_results'}, {d['key'] for d in dbs} - {d['key'] for d in before['databases']})
        self.assertEqual(before, blueprint())
        self.assertEqual(66, len(views))
        self.assertFalse(any(p['type'] == 'rollup' for d in dbs for p in d['properties'].values()))

    def test_every_enabled_relation_has_a_target_and_reverse_name_is_collision_free(self):
        for feature in [None] + [item['key'] for item in workflow_catalog()['features']]:
            c = all_settings() if feature is None else settings([feature], all_modules=True)
            dbs = {db['key']: db for db in selected(c)[0]}
            seen = set()
            for db in dbs.values():
                for prop in db['properties'].values():
                    if prop['type'] == 'relation':
                        self.assertIn(prop['target'], dbs)
                        if prop.get('reciprocal'):
                            pair = prop['target'], prop['reciprocal']
                            self.assertNotIn(pair, seen)
                            self.assertNotIn(prop['reciprocal'], dbs[prop['target']]['properties'])
                            seen.add(pair)

    def test_disabled_modules_never_appear_as_relationship_targets(self):
        c = settings(['student_history', 'class_hub', 'followups', 'para_hub', 'lesson_links'])
        dbs = selected(c)[0]
        self.assertEqual(9, len(dbs))
        self.assertNotIn('contacts', json.dumps(workflow_plan(c)['record_views']))
        self.assertNotIn('meetings', json.dumps(workflow_plan(c)['record_views']))

    def test_schemas_emit_documented_dual_properties_and_keep_text_fields(self):
        dbs = {d['key']: d for d in selected(all_settings())[0]}
        ids = {key: str(uuid4()) for key in dbs}
        self.assertEqual({'relation': {'data_source_id': ids['students'], 'type': 'dual_property', 'dual_property': {}}},
                         schema(dbs['attendance']['properties'], ids)['학생'])
        for db, field in (('submissions', '항목'), ('meetings', '담당'), ('accounts', '관리 담당'), ('staff', '담당 업무')):
            self.assertEqual('rich_text', dbs[db]['properties'][field]['type'])
        self.assertNotIn('reciprocal', dbs['timetable']['properties']['업무·일정'])

    def test_record_views_have_real_or_generated_relation_properties(self):
        plan = workflow_plan(all_settings())
        props = {db['key']: copy.deepcopy(db['properties']) for db in plan['schema']['databases']}
        for db in plan['schema']['databases']:
            for name, prop in db['properties'].items():
                if prop.get('reciprocal'):
                    props[prop['target']][prop['reciprocal']] = {'type': 'relation', 'target': db['key']}
        for view in plan['record_views']:
            with self.subTest(view=view['key']):
                relation = view['filter']['and'][0]
                self.assertEqual(view['host'], props[view['source']][relation['property']]['target'])
                self.assertEqual({'contains': '$current_page'}, relation['relation'])
                self.assertTrue(set(view['show']) <= set(props[view['source']]))

    def test_followups_exclude_finished_tasks_and_submission_pending_view_exists(self):
        plan = workflow_plan(all_settings())
        followups = [view for view in plan['record_views'] if view['host'] in ('counseling', 'contacts', 'meetings') and view['source'] == 'agenda']
        self.assertEqual(3, len(followups))
        for view in followups:
            self.assertIn({'property': '상태', 'select': {'does_not_equal': '완료'}}, view['filter']['and'])
            self.assertIn({'property': '상태', 'select': {'does_not_equal': '취소'}}, view['filter']['and'])
        pending = next(view for view in plan['record_views'] if view['key'].endswith(':pending'))
        self.assertEqual(['미제출', '보완 요청'], [part['select']['equals'] for part in pending['filter']['and'][-1]['or']])

    def test_student_history_does_not_hide_old_or_archived_linked_records(self):
        for view in workflow_plan(all_settings())['record_views']:
            if view['host'] == 'students' and not view['key'].endswith(':pending'):
                self.assertEqual(1, len(view['filter']['and']))
                self.assertEqual('all_linked_history', view['scope'])
                self.assertEqual('descending', view['sorts'][0]['direction'])

    def test_class_upcoming_assessments_and_assessment_criteria_are_explicit(self):
        views = {view['key']: view for view in workflow_plan(all_settings())['record_views']}
        self.assertIn({'property': '평가 예정', 'formula': {'checkbox': {'equals': True}}},
                      views['classes:assessments:학급:upcoming']['filter']['and'])
        self.assertIn('assessments:resources:관련 평가', views)

    def test_record_identity_and_roster_denominator_are_explicit_not_row_counts(self):
        plan = workflow_plan(all_settings())
        self.assertEqual([['제출 항목', '학생'], ['평가', '학생']], [rule['identity'] for rule in plan['record_rules']])
        for counter in plan['counters']:
            self.assertEqual('distinct_confirmed_target_student_ids', counter['denominator'])
            self.assertIn('including_no_row', counter['missing'])
            self.assertEqual('block_until_resolved', counter['duplicate_rows'])
            self.assertEqual('미설정', counter['zero_denominator'])
            self.assertFalse(counter['automatic_rollup'])


class WorkflowInstallationTests(unittest.TestCase):
    def test_all_nine_install_as_new_schemas_without_claiming_record_views_installed(self):
        c, api = all_settings(), FakeNotion()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            install(api, c, api.parent, path)
            state = Journal(path, api).data
            self.assertEqual(18, len(state['databases']))
            self.assertTrue(state['complete'])
            self.assertFalse(state['workflow_acceptance']['record_views_installed'])
            self.assertEqual('pending', state['workflow_acceptance']['status'])
            self.assertEqual([], verify_remote(api, state))
            first_calls = len([call for call in api.calls if call[0] == 'POST'])
            install(api, c, api.parent, path)
            self.assertEqual(first_calls, len([call for call in api.calls if call[0] == 'POST']))

    def test_legacy_signature_and_checkpoint_are_unchanged(self):
        from teacher_planner.dashboard import layout_spec
        c, api = settings(), FakeNotion()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            install(api, c, api.parent, path)
            state = Journal(path, api).data
            self.assertEqual(fingerprint({'config': c, 'blueprint': blueprint(), 'dashboard': layout_spec()}), state['signature'])
            self.assertNotIn('workflow_plan', state)
            self.assertNotIn('workflow_acceptance', state)

    def test_adding_workflows_to_existing_state_stops_before_any_mutation(self):
        c, api = settings(), FakeNotion()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            install(api, c, api.parent, path)
            before, call_count = path.read_bytes(), len(api.calls)
            c['workflows'] = {'preset': 'basic', 'features': workflow_catalog()['presets']['basic']['features']}
            with self.assertRaises(ValueError):
                install(api, c, api.parent, path)
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(any(method in ('POST', 'PATCH', 'DELETE') for method, _, _ in api.calls[call_count:]))

    def test_workflow_cli_is_offline_and_reports_pending_handoff(self):
        c = all_settings()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(c))
            with patch('teacher_planner.cli.Client', side_effect=AssertionError('No API calls')), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, main(['workflow-plan', '--config', str(path)]))
            result = json.loads(out.getvalue())
            self.assertFalse(result['applied'])
            self.assertFalse(result['record_views_installed'])
            self.assertEqual('pending', result['verification']['status'])

    def test_final_installation_cannot_complete_from_layout_and_views_without_rule_evidence(self):
        c, api = all_settings(), FakeNotion()
        with tempfile.TemporaryDirectory() as folder:
            state_path, snapshot_path = Path(folder) / 'state.json', Path(folder) / 'workflow.json'
            install(api, c, api.parent, state_path)
            state = Journal(state_path, api).data
            evidence = view_evidence(c)
            refs = {key: value['data_source_id'] for key, value in state['databases'].items()}
            evidence['data_sources'] = refs
            plans = {view['key']: view for view in workflow_plan(c)['record_views']}
            for view in evidence['views']:
                planned = plans[view['plan_key']]
                view.update(host_data_source_id=refs[planned['host']], data_source_id=refs[planned['source']])
            for rule in evidence['record_rule_checks']:
                rule['data_source_id'] = refs[rule['source']]
            for counter in evidence['counter_checks']:
                counter.update(host_data_source_id=refs[counter['host']],
                               source_data_source_id=refs[counter['counter']['source']])
            for missing in (True, False):
                supplied = copy.deepcopy(evidence)
                if missing:
                    supplied['record_rule_checks'] = []
                snapshot_path.write_text(json.dumps(supplied))
                with (patch('teacher_planner.cli.Client', return_value=api),
                      patch('teacher_planner.layout_cli.load_evidence', return_value={'complete': True}),
                      redirect_stdout(io.StringIO()) as out):
                    code = main(['verify', '--state', str(state_path), '--layout-snapshot', 'already-checked.json',
                                 '--workflow-snapshot', str(snapshot_path)])
                report = json.loads(out.getvalue())
                self.assertEqual(1 if missing else 0, code)
                self.assertEqual(not missing, report['complete'])
                self.assertEqual(not missing, report['installation_complete'])
                self.assertTrue(report['layout_core_complete'])


class WorkflowEvidenceTests(unittest.TestCase):
    def test_valid_supplied_evidence_remains_distinct_from_live_queries(self):
        c = all_settings()
        result = verify_workflow_evidence(c, view_evidence(c))
        self.assertTrue(result['evidence_valid'])
        self.assertEqual(80, result['checked_views'])
        self.assertEqual(2, result['checked_record_rules'])
        self.assertEqual(4, result['checked_counters'])
        self.assertFalse(result['applied'])
        self.assertFalse(result['live_notion_verified'])

    def test_two_records_are_required_when_available_but_one_record_workspace_is_allowed(self):
        c = all_settings()
        evidence = view_evidence(c, host_count=1)
        self.assertTrue(verify_workflow_evidence(c, evidence)['evidence_valid'])
        evidence['host_record_counts'] = {key: 2 for key in evidence['data_sources']}
        self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])
        evidence['host_record_counts'] = {key: 0 for key in evidence['data_sources']}
        self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])

    def test_view_evidence_alone_does_not_verify_identity_or_counts(self):
        c = all_settings()
        for key in ('host_record_counts', 'record_rule_checks', 'counter_checks'):
            evidence = view_evidence(c)
            del evidence[key]
            with self.subTest(key=key):
                result = verify_workflow_evidence(c, evidence)
                self.assertFalse(result['evidence_valid'])
                self.assertTrue(result['pending'])

    def test_rule_violations_wrong_sources_and_unverified_rows_are_rejected(self):
        c = all_settings()
        for change in ({'data_source_id': str(uuid4())}, {'rule': {}}, {'violations': ['duplicate']},
                       {'checked_record_count': 0}, {'new_record_checked': False}, {'legacy_rows_preserved': False}):
            evidence = view_evidence(c)
            evidence['record_rule_checks'][0].update(change)
            with self.subTest(change=change):
                self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])

    def test_counts_use_distinct_target_students_and_include_missing_rows(self):
        c = all_settings()
        for change in ({'target_student_ids': [str(uuid4())]}, {'missing_student_ids': []},
                       {'completed_student_ids': [str(uuid4())]}, {'duplicate_record_keys': ['item:student']},
                       {'displayed': {'denominator': 2, 'numerator': 2, 'percentage': 100}},
                       {'displayed': {'denominator': 3, 'numerator': 2, 'percentage': float('nan')}},
                       {'includes_unrecorded_students': False}, {'ui_checked': False}, {'counter': {}}):
            evidence = view_evidence(c)
            evidence['counter_checks'][0].update(change)
            with self.subTest(change=change):
                self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])

    def test_unconfirmed_or_empty_roster_cannot_show_a_percentage(self):
        c = all_settings()
        for targets in (None, []):
            evidence = view_evidence(c)
            actual = evidence['counter_checks'][0]
            if targets is not None:
                actual.update(target_student_ids=[], completed_student_ids=[], missing_student_ids=[])
                actual['displayed'] = {'denominator': 0, 'numerator': 0, 'percentage': 0}
            else:
                actual['roster_confirmed'] = False
            self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])
            actual['displayed']['percentage'] = '미설정'
            self.assertTrue(verify_workflow_evidence(c, evidence)['evidence_valid'])

    def test_wrong_current_page_parent_source_filter_or_sort_cannot_pass(self):
        c = all_settings()
        for field, replacement in (('current_page_id', str(uuid4())), ('parent_page_id', str(uuid4())),
                                   ('host_data_source_id', str(uuid4())), ('data_source_id', str(uuid4())),
                                   ('filter', {}), ('sorts', []), ('visible_properties', ['이름']), ('type', 'gallery')):
            evidence = view_evidence(c)
            evidence['views'][0][field] = replacement
            with self.subTest(field=field):
                self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])

    def test_plan_or_ui_assertions_without_readback_never_pass(self):
        c = all_settings()
        for mutate in (lambda e: e.update(views=[]), lambda e: e.update(plan_digest='different'),
                       lambda e: e['views'][0].update(ui_checked=False),
                       lambda e: e['views'][0].update(new_record_checked=False),
                       lambda e: e['views'].append(copy.deepcopy(e['views'][0]))):
            evidence = view_evidence(c)
            mutate(evidence)
            self.assertFalse(verify_workflow_evidence(c, evidence)['evidence_valid'])


if __name__ == '__main__':
    unittest.main()
