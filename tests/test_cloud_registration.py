import base64
import copy
import hashlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teacher_planner.cli import main
from teacher_planner.cloud_registration import (
    CloudRegistrationError, MANIFEST_KEYS, build_manifest, enrollment_url, validate_manifest,
)


IDS = [f'12345678-1234-4123-8123-{number:012d}' for number in range(1, 5)]
SECRET = 'DO_NOT_EXPORT_CREDENTIAL'


def manifest():
    return {'version': 1, 'office_code': 'B10', 'school_code': '1234567',
            'school_name': '가상 테스트 학교', 'academic_year': 2026,
            **dict(zip(('root_page_id', 'agenda_data_source_id', 'meals_block_id', 'status_block_id'), IDS))}


def rest_state():
    m = manifest()
    return {'complete': True, 'config': {'academic_year': 2026, 'teacher': '가상교사'},
            'objects': {'root': {'id': IDS[0]}, 'extras:meals': {'id': IDS[2]},
                        'student:private': {'name': 'PRIVATE_STUDENT_RECORD'}},
            'databases': {'agenda': {'id': 'separate-database-id', 'data_source_id': IDS[1]}},
            'extras': {'root_id': IDS[0], 'meal_block_id': IDS[2]},
            'cloud_sync': {'status_block_id': IDS[3]}, 'token': SECRET,
            'config_private': {'phone': 'PRIVATE_PHONE'},
            'identity': 'PRIVATE_WORKSPACE_ID',
            'neis_source': hashlib.sha256(json.dumps({key: m[key] for key in
                ('office_code', 'school_code', 'academic_year')}, sort_keys=True).encode()).hexdigest()}


def decode_link(value):
    fragment = value.split('#', 1)[1]
    return json.loads(base64.urlsafe_b64decode(fragment + '=' * (-len(fragment) % 4)))


class ManifestTests(unittest.TestCase):
    def test_exact_public_contract_and_normalized_ids(self):
        raw = manifest()
        raw['school_name'] = '  가상 테스트 학교  '
        raw['root_page_id'] = raw['root_page_id'].replace('-', '').upper()
        before = copy.deepcopy(raw)
        self.assertEqual(manifest(), validate_manifest(raw))
        self.assertEqual(before, raw)
        self.assertEqual(MANIFEST_KEYS, set(validate_manifest(raw)))

    def test_extra_and_missing_fields_fail_without_echoing_secrets(self):
        for key in ('token', 'api_key', 'students', 'callback_url'):
            raw = {**manifest(), key: SECRET}
            with self.subTest(key=key), self.assertRaises(CloudRegistrationError) as raised:
                validate_manifest(raw)
            self.assertNotIn(SECRET, str(raised.exception))
        for key in MANIFEST_KEYS:
            raw = manifest()
            raw.pop(key)
            with self.subTest(missing=key), self.assertRaises(CloudRegistrationError):
                validate_manifest(raw)

    def test_tampered_types_codes_ids_and_oversized_names_fail(self):
        cases = [('version', True), ('version', 2), ('academic_year', True),
                 ('academic_year', '2026'), ('academic_year', 9999),
                 ('office_code', 'b10'), ('office_code', 'B10?token=' + SECRET),
                 ('school_code', 1234567), ('school_code', '123'),
                 ('root_page_id', 'https://notion.so/' + IDS[0]),
                 ('root_page_id', '0' * 32), ('status_block_id', IDS[2]),
                 ('meals_block_id', None), ('school_name', 'x' * 4097), ('school_name', ' ' * 201 + '학교'),
                 ('school_name', '\n학교'), ('school_name', 'Bearer ' + SECRET),
                 ('school_name', 'ntn_' + SECRET), ('school_name', 'nrt_' + SECRET),
                 ('school_name', '<script>학교</script>')]
        for key, value in cases:
            with self.subTest(key=key, kind=type(value).__name__), self.assertRaises(CloudRegistrationError):
                validate_manifest({**manifest(), key: value})
        for value in ([], None, 'manifest', 1):
            with self.assertRaises(CloudRegistrationError):
                validate_manifest(value)

    def test_link_uses_only_a_minimal_base64url_fragment(self):
        link = enrollment_url('https://EXAMPLE.web.app', manifest())
        self.assertTrue(link.startswith('https://example.web.app/connect#'))
        self.assertNotIn('?', link)
        self.assertNotIn('=', link.split('#')[1])
        self.assertEqual(manifest(), decode_link(link))

    def test_service_origin_rejects_http_local_addresses_credentials_and_paths(self):
        invalid = ['http://example.web.app', 'https://example.web.app/',
                   'https://example.web.app/path', 'https://example.web.app?key=' + SECRET,
                   'https://example.web.app?', 'https://example.web.app#',
                   'https://user:' + SECRET + '@example.web.app', 'https://localhost',
                   'https://a.localhost', 'https://127.0.0.1', 'https://[::1]',
                   'https://10.0.0.1', 'https://169.254.169.254', 'https://service.internal',
                   'https://example.web.app:99999', 'https://example.web.app:', 'https://example.web.app\\path',
                   'https://example.web.app\n', 'https://service', None]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(CloudRegistrationError) as raised:
                enrollment_url(value, manifest())
            self.assertNotIn(SECRET, str(raised.exception))


class RestManifestTests(unittest.TestCase):
    def test_builder_exports_only_public_fields_without_mutating_source(self):
        state = rest_state()
        school = {key: manifest()[key] for key in ('office_code', 'school_code', 'school_name', 'academic_year')}
        school.update(api_key=SECRET, rows=[{'private_student': 'PRIVATE_STUDENT_RECORD'}])
        before = copy.deepcopy((state, school))
        result = build_manifest(state, school)
        self.assertEqual(manifest(), result)
        self.assertEqual(before, (state, school))
        encoded = json.dumps(result)
        for private in (SECRET, 'PRIVATE_STUDENT_RECORD', 'PRIVATE_WORKSPACE_ID', 'PRIVATE_PHONE'):
            self.assertNotIn(private, encoded)

    def test_existing_config_school_and_explicit_recorded_status_are_supported(self):
        state = rest_state()
        state['config']['school'] = {key: manifest()[key] for key in ('office_code', 'school_code', 'school_name')}
        state.pop('cloud_sync')
        self.assertEqual(manifest(), build_manifest(state, status_block_id=IDS[3]))

    def test_mcp_route_partial_install_and_pending_are_rejected(self):
        cases = [{'route': 'mcp'}, {'complete': False}, {'config': []},
                 {'pending': {'key': SECRET}}, {'route': 'unknown'}]
        for change in cases:
            with self.subTest(change=next(iter(change))), self.assertRaises(CloudRegistrationError) as raised:
                build_manifest({**rest_state(), **change}, manifest())
            self.assertNotIn(SECRET, str(raised.exception))

    def test_school_year_or_existing_school_binding_mismatch_is_rejected(self):
        for school in ({**manifest(), 'academic_year': 2025}, {**manifest(), 'school_code': '7654321'}):
            with self.assertRaises(CloudRegistrationError):
                build_manifest(rest_state(), school)
        with self.assertRaises(CloudRegistrationError):
            build_manifest(rest_state())

    def test_recorded_target_id_conflicts_and_missing_targets_are_rejected(self):
        state = rest_state()
        with self.assertRaises(CloudRegistrationError):
            build_manifest(state, manifest(), status_block_id=IDS[2])
        state['extras']['meal_block_id'] = IDS[3]
        with self.assertRaises(CloudRegistrationError):
            build_manifest(state, manifest())
        state = rest_state()
        state['databases']['agenda'].pop('data_source_id')
        with self.assertRaises(CloudRegistrationError):
            build_manifest(state, manifest())


class CloudConnectCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / '.local'
        self.private.mkdir()
        self.source = self.private / 'manifest.json'
        self.source.write_text(json.dumps(manifest()))
        self.out, self.err = io.StringIO(), io.StringIO()

    def run_cli(self, *args):
        with patch('sys.stdout', self.out), patch('sys.stderr', self.err), \
                patch('teacher_planner.cli.Client', side_effect=AssertionError('No Notion connection')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('No network')), \
                patch('socket.create_connection', side_effect=AssertionError('No network')):
            return main(['cloud-connect', '--service-url', 'https://example.web.app', *args])

    def test_manifest_mode_is_offline_and_output_is_owner_only_manifest(self):
        output = self.private / 'output.json'
        output.write_text('old')
        output.chmod(0o644)
        before = self.source.read_bytes()
        self.assertEqual(0, self.run_cli('--manifest', str(self.source), '--output', str(output)))
        report = json.loads(self.out.getvalue())
        self.assertFalse(report['connected'])
        self.assertFalse(report['daily_sync_enabled'])
        self.assertEqual('daily', report['requested_schedule'])
        self.assertEqual(manifest(), decode_link(report['enrollment_url']))
        self.assertEqual(manifest(), json.loads(output.read_text()))
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        self.assertEqual(before, self.source.read_bytes())

    def test_rest_mode_reuses_existing_context_and_omits_secrets(self):
        state_path, school_path = self.private / 'state.json', self.private / 'school.json'
        state_path.write_text(json.dumps(rest_state()))
        school_path.write_text(json.dumps({**manifest(), 'api_key': SECRET}))
        before = state_path.read_bytes()
        self.assertEqual(0, self.run_cli('--state', str(state_path), '--school-config', str(school_path)))
        self.assertEqual(manifest(), decode_link(json.loads(self.out.getvalue())['enrollment_url']))
        self.assertEqual(before, state_path.read_bytes())
        self.assertNotIn(SECRET, self.out.getvalue() + self.err.getvalue())

    def test_invalid_json_duplicate_fields_oversize_and_secrets_are_redacted(self):
        invalid = ['{"version":1,"version":1}', SECRET + '{',
                   json.dumps({**manifest(), 'token': SECRET}), ' ' * 5000 + SECRET]
        for value in invalid:
            self.source.write_text(value)
            self.out.seek(0); self.out.truncate()
            self.err.seek(0); self.err.truncate()
            self.assertEqual(1, self.run_cli('--manifest', str(self.source)))
            self.assertEqual('', self.out.getvalue())
            self.assertNotIn(SECRET, self.err.getvalue())

    def test_mcp_state_and_manifest_override_options_are_rejected(self):
        self.source.write_text(json.dumps({'route': 'mcp', **rest_state()}))
        self.assertEqual(1, self.run_cli('--state', str(self.source)))
        self.source.write_text(json.dumps(manifest()))
        self.assertEqual(1, self.run_cli('--manifest', str(self.source), '--status-block-id', IDS[3]))

    def test_public_output_and_environment_files_are_rejected_without_writes(self):
        output = Path(self.temp.name) / 'public.json'
        self.assertEqual(1, self.run_cli('--manifest', str(self.source), '--output', str(output)))
        self.assertFalse(output.exists())
        private_env = self.private / '.env'
        private_env.write_text(SECRET)
        self.assertEqual(1, self.run_cli('--manifest', str(private_env)))
        self.assertNotIn(SECRET, self.err.getvalue())

    def test_output_cannot_replace_source_manifest_or_rest_installation_inputs(self):
        state_path, school_path = self.private / 'state.json', self.private / 'school.json'
        state_path.write_text(json.dumps(rest_state()))
        school_path.write_text(json.dumps(manifest()))
        state_alias = self.private / 'state-alias.json'
        state_alias.symlink_to(state_path)
        cases = [(['--manifest', str(self.source)], self.source)]
        for output in (state_path, school_path, state_alias):
            cases.append((['--state', str(state_path), '--school-config', str(school_path)], output))
        before = {path: path.read_bytes() for path in (self.source, state_path, school_path)}
        for args, output in cases:
            with self.subTest(output=output.name):
                self.assertEqual(1, self.run_cli(*args, '--output', str(output)))
                for path, content in before.items():
                    self.assertEqual(content, path.read_bytes())
        self.assertEqual('', self.out.getvalue())


if __name__ == '__main__':
    unittest.main()
