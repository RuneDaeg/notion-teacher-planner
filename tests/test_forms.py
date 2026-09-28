import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.blocks import children, recover_block
from teacher_planner.client import NotionError
from teacher_planner.forms import (catalog, form_blocks, form_markdown, install_forms,
                                   selected_forms, verify_forms)
from teacher_planner.install import Journal, compact
from teacher_planner.model import config, rich, selected
from test_planner import FakeNotion

ROOT = Path(__file__).resolve().parents[1]
KEYS = {'counseling', 'guardian', 'meeting', 'lesson', 'assessment', 'homeroom'}


class FormDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.c = config(ROOT / 'config.example.json')

    def test_catalog_and_bodies_are_fresh_and_cover_the_six_approved_forms(self):
        self.assertEqual(KEYS, {form['key'] for form in catalog()})
        first = catalog()
        first[0]['title'] = 'changed'
        self.assertNotEqual(first, catalog())
        body = form_blocks('counseling')
        body[0]['heading_2']['rich_text'] = rich('changed')
        self.assertNotEqual(body, form_blocks('counseling'))
        with self.assertRaisesRegex(ValueError, '알 수 없는'):
            form_blocks('unknown')

    def test_form_selection_respects_modules_role_and_explicit_choices(self):
        self.assertEqual(KEYS, {form['key'] for form in selected_forms(self.c)})
        self.c['modules'] = dict.fromkeys(self.c['modules'], False)
        self.assertEqual({'counseling', 'lesson', 'homeroom'}, {form['key'] for form in selected_forms(self.c)})
        self.c['classes'][0]['homeroom'] = False
        self.assertEqual({'counseling', 'lesson'}, {form['key'] for form in selected_forms(self.c)})
        self.c['forms'] = ['guardian', 'homeroom', 'lesson']
        self.assertEqual(['lesson'], [form['key'] for form in selected_forms(self.c)])
        self.c['forms'] = []
        self.assertEqual([], selected_forms(self.c))
        self.c['classes'].append({'homeroom': True})
        self.c['forms'] = ['homeroom']
        self.assertEqual(['homeroom'], [form['key'] for form in selected_forms(self.c)])

    def test_invalid_selection_is_rejected_without_mutating_configuration(self):
        for choice in (None, 'lesson', {}, ['unknown'], ['lesson', 'lesson'], [1], [['lesson']]):
            self.c['forms'] = choice
            before = copy.deepcopy(self.c)
            with self.subTest(choice=choice), self.assertRaisesRegex(ValueError, 'forms'):
                selected_forms(self.c)
            self.assertEqual(before, self.c)

    def test_blank_content_uses_valid_native_blocks_and_no_fixed_records(self):
        for form in catalog():
            body = form_blocks(form['key'])
            self.assertTrue({'heading_2', 'paragraph', 'table', 'to_do'} <= {block['type'] for block in body})
            serialized = json.dumps(body, ensure_ascii=False)
            self.assertIsNone(re.search(r'\b\d{4}-\d{2}-\d{2}\b|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', serialized))
            self.assertNotIn('relation', serialized)
            self.assertNotIn('http', serialized)
            for block in body:
                with self.subTest(form=form['key'], kind=block['type']):
                    if block['type'] == 'table':
                        table = block['table']
                        self.assertTrue(table['has_column_header'])
                        self.assertGreater(len(table['children']), 1)
                        for row in table['children']:
                            self.assertEqual(table['table_width'], len(row['table_row']['cells']))
                        # Each data row contains blank writing cells, not sample people/dates.
                        for row in table['children'][1:]:
                            self.assertIn([], row['table_row']['cells'])
                    elif block['type'] == 'to_do':
                        self.assertFalse(block['to_do']['checked'])

    def test_markdown_counterparts_match_the_shared_native_definitions(self):
        for form in catalog():
            text = (ROOT / 'templates/forms' / (form['key'] + '.md')).read_text(encoding='utf-8')
            self.assertEqual(form_markdown(form['key']), text)
            self.assertIn('새 템플릿', text)
            self.assertIn('복제', text)
            self.assertIn('자동 생성되지 않습니다', text)
            self.assertIn('- [ ] ', text)


class FormInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.api = FakeNotion()
        self.c = config(ROOT / 'config.example.json')
        self.databases = {db['key']: {'id': str(uuid4()), 'data_source_id': str(uuid4())}
                          for db in selected(self.c)[0]}
        j = Journal(self.path, self.api)
        j.data.update(config=self.c, databases=self.databases)
        j.save()

    def state(self):
        return json.loads(self.path.read_text())

    def install(self):
        return install_forms(Journal(self.path, self.api), self.c, self.api.parent, self.databases)

    def writes(self):
        return [call for call in self.api.calls if call[0] in ('POST', 'PATCH')]

    def test_library_is_outside_data_sources_and_each_form_links_to_its_target_database(self):
        library = self.install()
        state = self.state()
        self.assertEqual(library, state['forms']['library_id'])
        self.assertEqual(KEYS, set(state['forms']['entries']))
        self.assertEqual(self.api.parent, self.api.objects['/pages/' + library]['parent']['page_id'])
        page_creates = [payload for method, path, payload in self.writes() if method == 'POST' and path == '/pages']
        self.assertEqual(7, len(page_creates))
        self.assertTrue(all(payload['parent']['type'] == 'page_id' for payload in page_creates))
        self.assertFalse(any(path in ('/databases', '/views') for _, path, _ in self.writes()))
        for key, entry in state['forms']['entries'].items():
            self.assertEqual(library, self.api.objects['/pages/' + entry['page_id']]['parent']['page_id'])
            target = self.databases[entry['target']]
            self.assertEqual([], self.api.pages(target['data_source_id']))
            block = self.api.objects['/blocks/' + entry['block_ids'][1]]
            url = block['paragraph']['rich_text'][0]['text']['link']['url']
            self.assertEqual('https://www.notion.so/' + target['id'].replace('-', ''), url)
            self.assertNotIn(target['data_source_id'].replace('-', ''), url)
        self.assertEqual([], verify_forms(self.api, state))

    def test_second_install_has_no_remote_writes_and_keeps_edited_master_text(self):
        first = self.install()
        key = self.state()['objects']['forms:counseling:body:0']['id']
        self.api.objects['/blocks/' + key]['heading_2']['rich_text'] = rich('교사가 바꾼 구역 이름')
        before = copy.deepcopy(self.api.objects)
        self.api.calls.clear()
        self.assertEqual(first, self.install())
        self.assertEqual([], self.writes())
        self.assertEqual(before, self.api.objects)

    def test_empty_selection_or_unavailable_selected_target_creates_nothing(self):
        self.c['forms'] = []
        self.assertIsNone(self.install())
        self.assertEqual([], self.writes())
        self.c['forms'] = ['guardian']
        self.databases.pop('contacts')
        self.assertIsNone(self.install())
        self.assertEqual([], self.writes())
        self.assertNotIn('forms', self.state())

    def test_optional_modules_and_homeroom_do_not_leak_unselected_form_pages(self):
        self.c['modules'] = dict.fromkeys(self.c['modules'], False)
        self.c['classes'][0]['homeroom'] = False
        self.install()
        self.assertEqual({'counseling', 'lesson'}, set(self.state()['forms']['entries']))
        for key in KEYS - {'counseling', 'lesson'}:
            self.assertNotIn('forms:' + key, self.state()['objects'])

    def test_partial_definite_failure_resumes_without_duplicate_pages_or_blocks(self):
        original = self.api.request

        def fail_table(method, path, payload=None):
            if method == 'PATCH' and path.endswith('/children') and payload['children'][0]['type'] == 'table':
                raise NotionError('table rejected', 400)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail_table), self.assertRaises(NotionError):
            self.install()
        partial = self.state()
        self.assertNotIn('pending', partial)
        existing = copy.deepcopy(partial['objects'])
        self.api.calls.clear()
        self.install()
        final = self.state()
        self.assertTrue(all(final['objects'][key] == value for key, value in existing.items()))
        self.assertEqual([], verify_forms(self.api, final))
        self.assertFalse(any(method == 'POST' and path == '/pages' and
                             payload['properties']['title']['title'][0]['text']['content'] in ('양식 모음', '학생 상담 기록')
                             for method, path, payload in self.writes()))

    def test_ambiguous_nested_table_append_requires_verified_recovery(self):
        original = self.api.request

        def lost_table(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'PATCH' and path.endswith('/children') and payload['children'][0]['type'] == 'table':
                raise NotionError('unknown table append', 503)
            return result

        with patch.object(self.api, 'request', side_effect=lost_table), self.assertRaises(NotionError):
            self.install()
        j = Journal(self.path, self.api)
        pending = j.data['pending']
        self.assertEqual('forms:counseling:body:1', pending['key'])
        page_id = j.data['objects']['forms:counseling']['id']
        table = children(self.api, page_id)[-1]
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '불확실'):
            self.install()
        self.assertEqual([], self.writes())
        recover_block(self.api, pending, table)
        j.data['objects'][pending['key']] = compact(table)
        j.data.pop('pending')
        j.save()
        self.install()
        self.assertEqual(1, [block['id'] for block in children(self.api, page_id)].count(table['id']))
        self.assertEqual([], verify_forms(self.api, self.state()))

    def test_partial_resume_checks_saved_library_and_form_pages_before_any_writes(self):
        original = self.api.request

        def fail_table(method, path, payload=None):
            if method == 'PATCH' and path.endswith('/children') and payload['children'][0]['type'] == 'table':
                raise NotionError('table rejected', 400)
            return original(method, path, payload)

        with patch.object(self.api, 'request', side_effect=fail_table), self.assertRaises(NotionError):
            self.install()
        partial = self.state()
        for key in ('forms:library', 'forms:counseling'):
            page = self.api.objects['/pages/' + partial['objects'][key]['id']]
            before = copy.deepcopy(page)
            for change in ({'parent': {'type': 'page_id', 'page_id': str(uuid4())}},
                           {'archived': True}, {'in_trash': True}):
                page.clear(); page.update(copy.deepcopy(before)); page.update(change)
                self.api.calls.clear()
                with self.subTest(page=key, change=change), self.assertRaisesRegex(ValueError, '추가 작성을 중단'):
                    self.install()
                self.assertEqual([], self.writes())
            page.clear(); page.update(before)
        self.install()
        self.assertEqual([], verify_forms(self.api, self.state()))

    def test_saved_missing_form_page_is_not_recreated_or_filled(self):
        self.install()
        original = self.api.request
        target = '/pages/' + self.state()['objects']['forms:lesson']['id']

        def missing(method, path, payload=None):
            if method == 'GET' and path == target:
                raise NotionError('missing', 404)
            return original(method, path, payload)

        self.api.calls.clear()
        with patch.object(self.api, 'request', side_effect=missing), self.assertRaisesRegex(ValueError, '재생성하지 않습니다'):
            self.install()
        self.assertEqual([], self.writes())

    def test_ambiguous_library_page_creation_is_not_blindly_retried(self):
        original = self.api.request

        def lost_page(method, path, payload=None):
            result = original(method, path, payload)
            if method == 'POST' and path == '/pages':
                raise NotionError('unknown page creation', 503)
            return result

        with patch.object(self.api, 'request', side_effect=lost_page), self.assertRaises(NotionError):
            self.install()
        self.assertEqual('forms:library', self.state()['pending']['key'])
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '불확실'):
            self.install()
        self.assertEqual([], self.writes())
        self.assertEqual(1, len(children(self.api, self.api.parent)))

    def test_changed_definitions_cannot_mix_with_a_previous_library_on_resume(self):
        self.install()
        original = form_blocks

        def revised(key):
            return original(key) + [{'object': 'block', 'type': 'paragraph',
                                     'paragraph': {'rich_text': rich('새 양식 구역')}}]

        self.api.calls.clear()
        with patch('teacher_planner.forms.form_blocks', side_effect=revised), self.assertRaisesRegex(ValueError, '기존 양식 모음'):
            self.install()
        self.assertEqual([], self.writes())

    def test_verification_reports_moved_form_and_missing_link_block_without_writes(self):
        self.install()
        state = self.state()
        entry = state['forms']['entries']['counseling']
        self.api.objects['/pages/' + entry['page_id']]['parent']['page_id'] = str(uuid4())
        link_block = entry['block_ids'][1]
        self.api.block_children[entry['page_id']].remove(link_block)
        del self.api.objects['/blocks/' + link_block]
        self.api.calls.clear()
        issues = verify_forms(self.api, state)
        self.assertTrue(any('페이지 위치' in issue for issue in issues))
        self.assertTrue(any('본문 구역' in issue for issue in issues))
        self.assertEqual([], self.writes())

    def test_verification_handles_null_text_links_and_detects_wrong_target(self):
        self.install()
        state = self.state()
        entry = state['forms']['entries']['lesson']
        target = self.api.objects['/blocks/' + entry['block_ids'][1]]
        target['paragraph']['rich_text'][0]['text']['link'] = None
        self.assertTrue(any('기록 DB 링크' in issue for issue in verify_forms(self.api, state)))
        self.api.calls.clear()
        self.assertEqual([], verify_forms(self.api, {'objects': {}}))
        self.assertEqual([], self.api.calls)


if __name__ == '__main__':
    unittest.main()
