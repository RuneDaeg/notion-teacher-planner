import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teacher_planner.cli import verify_remote
from teacher_planner.client import NotionError
from teacher_planner.install import install
from teacher_planner.model import blueprint, config, schema, values
from test_planner import FakeNotion, ROOT


class StudentHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'state.json'
        self.api = FakeNotion()
        self.config = config(ROOT / 'config.example.json')
        self.config['modules'] = dict.fromkeys(self.config['modules'], False)
        self.definitions = {d['key']: d['properties'] for d in blueprint()['databases']}

    def install(self):
        return install(self.api, self.config, self.api.parent, self.path)

    def state(self):
        return json.loads(self.path.read_text())

    def sources(self):
        return {key: self.api.objects['/data_sources/' + value['data_source_id']]
                for key, value in self.state()['databases'].items()}

    def dual_creations(self):
        return [payload for method, path, payload in self.api.calls
                if method == 'PATCH' and path.startswith('/data_sources/')
                and any(p.get('relation', {}).get('type') == 'dual_property'
                        for p in payload['properties'].values())]

    def test_blueprint_creates_one_native_pair_without_independent_reverse(self):
        props = self.definitions['counseling']
        self.assertEqual('상담 기록', props['학생']['reciprocal'])
        self.assertNotIn('상담 기록', self.definitions['students'])
        self.assertNotIn('학생', schema(props))
        self.assertEqual({'data_source_id': 'students-source', 'type': 'dual_property', 'dual_property': {}},
                         schema(props, {'students': 'students-source', 'agenda': 'agenda-source'})['학생']['relation'])

    def test_new_student_pages_show_all_related_originals_across_year_and_archive(self):
        self.install()
        sources = self.sources()
        forward = sources['counseling']['properties']['학생']
        reverse = sources['students']['properties']['상담 기록']
        self.assertEqual(reverse['id'], forward['relation']['dual_property']['synced_property_id'])
        self.assertEqual(forward['id'], reverse['relation']['dual_property']['synced_property_id'])
        self.assertEqual(sources['counseling']['id'], reverse['relation']['data_source_id'])
        def add(source, props):
            return self.api.request('POST', '/pages', {
                'parent': {'data_source_id': sources[source]['id']},
                'properties': values(props, self.definitions[source])})['id']
        student = add('students', {'이름': '[가상] 학생 A'})
        other = add('students', {'이름': '[가상] 학생 B'})
        old = add('counseling', {'이름': '[가상] 이전 상담', '학생': [student], '학년도': 2025, '보관': True})
        recent = add('counseling', {'이름': '[가상] 최근 상담', '학생': [student], '학년도': 2026, '보관': False})
        add('counseling', {'이름': '[가상] 다른 학생 상담', '학생': [other]})
        page = self.api.objects['/pages/' + student]
        self.assertEqual({old, recent}, {v['id'] for v in page['properties']['상담 기록']['relation']})
        self.assertEqual([], self.api.block_children[student])
        self.assertEqual(3, len(self.api.pages(sources['counseling']['id'])))
        self.assertEqual([], verify_remote(self.api, self.state()))

    def test_lost_dual_creation_requires_name_confirmation_without_another_pair(self):
        request = self.api.request
        def lose_response(method, path, payload=None):
            result = request(method, path, payload)
            if (method == 'PATCH' and path.startswith('/data_sources/')
                    and any(p.get('relation', {}).get('type') == 'dual_property'
                            for p in payload['properties'].values())):
                raise NotionError('lost response')
            return result
        with patch.object(self.api, 'request', side_effect=lose_response):
            with self.assertRaises(NotionError):
                self.install()
        self.assertTrue(self.state()['reciprocal_relations']['counseling:학생']['creation_pending'])
        self.assertEqual(1, len(self.dual_creations()))
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '생성 응답이 불확실'):
            self.install()
        self.assertFalse(any(method != 'GET' for method, path, payload in self.api.calls))
        # Explicitly confirmed native pair/name can be adopted, without a new pair.
        sources = self.sources()
        paired = sources['counseling']['properties']['학생']['relation']['dual_property']
        self.api.request('PATCH', '/data_sources/' + sources['students']['id'],
                         {'properties': {paired['synced_property_id']: {'name': '상담 기록'}}})
        self.install()
        self.assertEqual([], self.dual_creations())
        self.assertTrue(self.state()['reciprocal_relations']['counseling:학생']['complete'])
        self.assertEqual([], verify_remote(self.api, self.state()))

    def test_definitive_rejection_does_not_claim_later_manual_pair(self):
        request = self.api.request
        attempted = []
        def reject_creation(method, path, payload=None):
            if (method == 'PATCH' and path.startswith('/data_sources/')
                    and any(p.get('relation', {}).get('type') == 'dual_property'
                            for p in payload['properties'].values())):
                attempted.append((path, copy.deepcopy(payload)))
                raise NotionError('definitive rejection', 400)
            return request(method, path, payload)
        with patch.object(self.api, 'request', side_effect=reject_creation):
            with self.assertRaises(NotionError):
                self.install()
        self.assertNotIn('counseling:학생', self.state()['reciprocal_relations'])
        self.api.request('PATCH', *attempted[0])  # Teacher creates a matching target manually.
        sources = self.sources()
        reverse_id = sources['counseling']['properties']['학생']['relation']['dual_property']['synced_property_id']
        self.api.request('PATCH', '/data_sources/' + sources['students']['id'],
                         {'properties': {reverse_id: {'name': '교사가 만든 상담 모음'}}})
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '기존 역방향 관계 이름'):
            self.install()
        self.assertFalse(any(method != 'GET' for method, path, payload in self.api.calls))
        self.assertIn('교사가 만든 상담 모음', sources['students']['properties'])

    def test_verify_rejects_replaced_pair_even_when_both_pointers_match(self):
        self.install()
        sources = self.sources()
        source = sources['counseling']['properties']['학생']
        target = sources['students']['properties']['상담 기록']
        source['id'], target['id'] = 'new-source-property', 'new-target-property'
        source['relation']['dual_property']['synced_property_id'] = target['id']
        target['relation']['dual_property']['synced_property_id'] = source['id']
        self.api.calls.clear()
        self.assertTrue(any('양방향 관계 불일치' in x for x in verify_remote(self.api, self.state())))
        with self.assertRaisesRegex(ValueError, '속성 ID가 다릅니다'):
            self.install()
        self.assertFalse(any(method != 'GET' for method, path, payload in self.api.calls))

    def test_lost_rename_response_preserves_pair_ids_and_resumes(self):
        request = self.api.request
        def lose_response(method, path, payload=None):
            result = request(method, path, payload)
            if (method == 'PATCH' and path.startswith('/data_sources/')
                    and any(p == {'name': '상담 기록'} for p in payload['properties'].values())):
                raise NotionError('lost rename response')
            return result
        with patch.object(self.api, 'request', side_effect=lose_response):
            with self.assertRaises(NotionError):
                self.install()
        pair = copy.deepcopy(self.state()['reciprocal_relations']['counseling:학생'])
        self.api.calls.clear()
        self.install()
        final = self.state()['reciprocal_relations']['counseling:학생']
        for field in ('source_property_id', 'target_property_id'):
            self.assertEqual(pair[field], final[field])
        self.assertFalse(any(method == 'PATCH' and path.startswith('/data_sources/')
                             for method, path, payload in self.api.calls))

    def test_preexisting_reverse_name_conflict_stops_before_dual_creation(self):
        request = self.api.request
        def collide(method, path, payload=None):
            result = request(method, path, payload)
            if method == 'POST' and path == '/databases' and '학생 ID' in payload['initial_data_source']['properties']:
                ds = result['data_sources'][0]['id']
                self.api.objects['/data_sources/' + ds]['properties']['상담 기록'] = {
                    'id': 'manual-notes', 'type': 'rich_text', 'rich_text': {}}
            return result
        with patch.object(self.api, 'request', side_effect=collide):
            with self.assertRaisesRegex(ValueError, '충돌'):
                self.install()
        self.assertEqual([], self.dual_creations())
        self.assertEqual('manual-notes', self.sources()['students']['properties']['상담 기록']['id'])

    def test_completed_pair_manual_rename_is_not_overwritten(self):
        self.install()
        students = self.sources()['students']
        prop_id = students['properties']['상담 기록']['id']
        self.api.request('PATCH', '/data_sources/' + students['id'], {'properties': {prop_id: {'name': '교사의 상담 모음'}}})
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '기존 역방향 관계 이름'):
            self.install()
        self.assertFalse(any(method != 'GET' for method, path, payload in self.api.calls))
        self.assertTrue(any('양방향 관계 불일치' in x for x in verify_remote(self.api, self.state())))

    def test_single_or_misdirected_reverse_is_not_silently_repaired(self):
        self.install()
        sources = self.sources()
        relation = sources['counseling']['properties']['학생']['relation']
        original = copy.deepcopy(relation)
        relation.clear()
        relation.update(data_source_id=sources['students']['id'], type='single_property', single_property={})
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '관계가 달라'):
            self.install()
        self.assertFalse(any(method != 'GET' for method, path, payload in self.api.calls))
        self.assertTrue(any('양방향 관계 불일치' in x for x in verify_remote(self.api, self.state())))
        relation.clear()
        relation.update(original)
        sources['students']['properties']['상담 기록']['relation']['dual_property']['synced_property_id'] = 'unrelated-property'
        with self.assertRaisesRegex(ValueError, '역방향 연결'):
            self.install()

    def test_changed_blueprint_cannot_migrate_an_existing_install(self):
        before = blueprint()
        next(d for d in before['databases'] if d['key'] == 'counseling')['properties']['학생'].pop('reciprocal')
        with patch('teacher_planner.install.blueprint', return_value=before), patch('teacher_planner.model.blueprint', return_value=before):
            self.install()
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '기존 수첩은 자동 변경하지 않습니다'):
            self.install()
        self.assertEqual(['GET'], [method for method, path, payload in self.api.calls])


if __name__ == '__main__':
    unittest.main()
