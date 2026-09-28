import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from teacher_planner.blocks import BlockJournal, children, recover_block
from teacher_planner.client import NotionError
from teacher_planner.install import Journal, block
from test_planner import FakeNotion


class BlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.api = FakeNotion()
        self.journal = Journal(self.path, self.api)
        self.blocks = BlockJournal(self.journal)

    def state(self):
        return json.loads(self.path.read_text())

    def columns(self):
        return {'object': 'block', 'type': 'column_list', 'column_list': {'children': [
            {'object': 'block', 'type': 'column', 'column': {'width_ratio': .65, 'children': [block('heading_2', '시간표')]}},
            {'object': 'block', 'type': 'column', 'column': {'width_ratio': .35, 'children': [block('heading_2', '빠른 이동')]}},
        ]}}

    def lost_response(self, expected):
        original = self.api.request

        def create_then_fail(method, path, payload=None):
            original(method, path, payload)
            raise NotionError('lost response')

        with patch.object(self.api, 'request', side_effect=create_then_fail):
            with self.assertRaises(NotionError):
                self.blocks.append('layout:test', self.api.parent, expected)
        pending = self.state()['pending']
        obj = list(children(self.api, self.api.parent))[0]
        return pending, obj

    def test_append_records_one_block_and_repeated_key_does_not_duplicate(self):
        first = self.blocks.append('layout:title', self.api.parent, block('heading_2', 'Things to do'))
        second = BlockJournal(Journal(self.path, self.api)).append('layout:title', self.api.parent, block('heading_2', 'Things to do'))
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.api.calls))
        self.assertEqual(first, self.state()['objects']['layout:title'])
        self.assertNotIn('pending', self.state())
        self.assertEqual('PATCH', self.api.calls[0][0])

    def test_position_shapes_place_new_blocks_without_reordering_existing(self):
        a = self.blocks.append('a', self.api.parent, block('paragraph', 'A'))
        c = self.blocks.append('c', self.api.parent, block('paragraph', 'C'), {'type': 'end'})
        b = self.blocks.append('b', self.api.parent, block('paragraph', 'B'), {'type': 'after_block', 'after_block': {'id': a['id']}})
        start = self.blocks.append('start', self.api.parent, block('paragraph', 'First'), {'type': 'start'})
        self.assertEqual([start['id'], a['id'], b['id'], c['id']], [r['id'] for r in children(self.api, self.api.parent)])
        with self.assertRaisesRegex(ValueError, 'after_block.id'):
            self.blocks.append('invalid', self.api.parent, block('paragraph', 'X'), {'type': 'after', 'after': a['id']})
        self.assertNotIn('pending', self.state())

    def test_ambiguous_append_records_method_parent_and_blocks_retry(self):
        with patch.object(self.api, 'request', side_effect=NotionError('timeout')):
            with self.assertRaises(NotionError):
                self.blocks.append('layout:title', self.api.parent, block('heading_2', 'Schedule'))
        pending = self.state()['pending']
        self.assertEqual('PATCH', pending['method'])
        self.assertEqual(self.api.parent, pending['block_parent'])
        self.assertEqual('/blocks/' + self.api.parent + '/children', pending['endpoint'])
        calls = len(self.api.calls)
        with self.assertRaisesRegex(ValueError, '불확실'):
            BlockJournal(Journal(self.path, self.api)).append('layout:title', self.api.parent, block('heading_2', 'Schedule'))
        self.assertEqual(calls, len(self.api.calls))

    def test_definitive_rejection_clears_pending_but_server_error_does_not(self):
        for status in (400, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                journal = Journal(Path(self.temp.name) / f'{status}.json', self.api)
                with patch.object(self.api, 'request', side_effect=NotionError('rejected', status)):
                    with self.assertRaises(NotionError):
                        BlockJournal(journal).append('key', self.api.parent, block('paragraph', 'X'))
                self.assertEqual(status == 500, 'pending' in journal.data)

    def test_malformed_success_keeps_pending(self):
        for response in ({}, {'results': []}, {'results': [{'type': 'paragraph'}]},
                         {'results': [{'id': str(uuid4()), 'type': 'table'}]}, {'results': [{}, {}]}):
            with self.subTest(response=response):
                path = Path(self.temp.name) / (str(uuid4()) + '.json')
                journal = Journal(path, self.api)
                with patch.object(self.api, 'request', return_value=response):
                    with self.assertRaises(ValueError):
                        BlockJournal(journal).append('key', self.api.parent, block('paragraph', 'X'))
                self.assertIn('pending', json.loads(path.read_text()))
                self.assertNotIn('key', journal.data['objects'])

    def test_children_paginates_and_preserves_order(self):
        created = self.api.add_blocks(self.api.parent, [block('paragraph', str(i)) for i in range(105)])
        actual = list(children(self.api, self.api.parent))
        self.assertEqual([row['id'] for row in created], [row['id'] for row in actual])
        self.assertEqual(2, len(self.api.calls))
        self.assertIn('start_cursor=100', self.api.calls[1][1])

    def test_children_rejects_missing_or_repeated_next_cursor(self):
        for response in ({'results': [], 'has_more': True, 'next_cursor': None},
                         {'results': [], 'has_more': True, 'next_cursor': 'same'}):
            with self.subTest(response=response), patch.object(self.api, 'request', return_value=response):
                with self.assertRaisesRegex(ValueError, '다음 페이지'):
                    list(children(self.api, self.api.parent))

    def test_recovery_verifies_nested_columns_and_ignores_response_metadata(self):
        pending, obj = self.lost_response(self.columns())
        columns = list(children(self.api, obj['id']))
        heading = list(children(self.api, columns[0]['id']))[0]
        rich = self.api.objects['/blocks/' + heading['id']]['heading_2']['rich_text'][0]
        rich['plain_text'] = rich['text']['content']
        rich['annotations'] = {'bold': False, 'color': 'default'}
        self.assertIsNone(recover_block(self.api, pending, obj))
        self.assertIn('pending', self.state())

    def test_recovery_rejects_wrong_parent_or_changed_nested_width_or_text(self):
        pending, obj = self.lost_response(self.columns())
        wrong = copy.deepcopy(obj)
        wrong['parent']['page_id'] = str(uuid4())
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, wrong)
        column = list(children(self.api, obj['id']))[0]
        self.api.objects['/blocks/' + column['id']]['column']['width_ratio'] = .5
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, obj)
        self.api.objects['/blocks/' + column['id']]['column']['width_ratio'] = .65
        heading = list(children(self.api, column['id']))[0]
        self.api.objects['/blocks/' + heading['id']]['heading_2']['rich_text'][0]['text']['content'] = '다른 제목'
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, obj)

    def test_recovery_checks_table_width_cells_and_extra_children(self):
        expected = {'object': 'block', 'type': 'table', 'table': {
            'table_width': 2, 'has_column_header': True, 'has_row_header': False,
            'children': [{'object': 'block', 'type': 'table_row', 'table_row': {'cells': [
                [{'type': 'text', 'text': {'content': '교시'}}], [{'type': 'text', 'text': {'content': '월'}}]]}}]}}
        pending, obj = self.lost_response(expected)
        recover_block(self.api, pending, obj)
        wrong = copy.deepcopy(obj)
        wrong['table']['table_width'] = 3
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, wrong)
        row = list(children(self.api, obj['id']))[0]
        self.api.objects['/blocks/' + row['id']]['table_row']['cells'][0][0]['text']['content'] = '변경'
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, obj)
        self.api.objects['/blocks/' + row['id']]['table_row']['cells'][0][0]['text']['content'] = '교시'
        self.api.add_blocks(obj['id'], [expected['table']['children'][0]])
        with self.assertRaisesRegex(ValueError, '하위 블록 수'):
            recover_block(self.api, pending, obj)

    def test_recovery_rejects_in_trash_and_unexpected_child_content(self):
        pending, obj = self.lost_response(block('paragraph', '메모'))
        trashed = copy.deepcopy(obj)
        trashed['in_trash'] = True
        with self.assertRaises(ValueError):
            recover_block(self.api, pending, trashed)
        self.api.add_blocks(obj['id'], [block('paragraph', '추가한 내용')])
        changed = self.api.request('GET', '/blocks/' + obj['id'])
        with self.assertRaisesRegex(ValueError, '하위 블록 수'):
            recover_block(self.api, pending, changed)

    def test_recovery_of_synced_reference_checks_target_not_mirrored_children(self):
        original = self.blocks.append('original', self.api.parent, {
            'object': 'block', 'type': 'synced_block', 'synced_block': {
                'synced_from': None, 'children': [block('paragraph', '공유 시간표')]}})
        expected = {'object': 'block', 'type': 'synced_block', 'synced_block': {
            'synced_from': {'type': 'block_id', 'block_id': original['id']}}}
        request = self.api.request

        def lose_response(method, path, payload=None):
            result = request(method, path, payload)
            if method == 'PATCH':
                raise NotionError('lost response')
            return result

        with patch.object(self.api, 'request', side_effect=lose_response):
            with self.assertRaises(NotionError):
                self.blocks.append('reference', self.api.parent, expected)
        reference = children(self.api, self.api.parent)[-1]
        self.assertTrue(reference['has_children'])
        self.assertEqual(1, len(children(self.api, reference['id'])))
        recover_block(self.api, self.state()['pending'], reference)
        reference['synced_block']['synced_from']['block_id'] = str(uuid4())
        with self.assertRaises(ValueError):
            recover_block(self.api, self.state()['pending'], reference)


if __name__ == '__main__':
    unittest.main()
