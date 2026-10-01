import base64
import json
import unittest

from teacher_planner.cloud_registration import CloudRegistrationError
from teacher_planner.updates import update_url, validate_targets


def uid(n):
    return f'11111111-1111-4111-8111-{n:012d}'


class UpdateLinks(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(version=1,office_code='X10',school_code='1234567',school_name='가상학교',academic_year=2026,
            root_page_id=uid(1),agenda_data_source_id=uid(2),meals_block_id=uid(3),status_block_id=uid(4))
        self.targets = dict(version=1,students_data_source_id=uid(5),counseling_data_source_id=uid(6),student_relation_property_id='ab%3Fz')

    def test_fragment_preserves_original_manifest_and_exact_property_id(self):
        before = json.dumps(self.manifest)
        url = update_url('https://planner.example.com', self.manifest, self.targets)
        self.assertEqual(url.split('#')[0], 'https://planner.example.com/connect')
        fragment = url.split('#')[1]
        bundle = json.loads(base64.urlsafe_b64decode(fragment + '=' * (-len(fragment) % 4)))
        self.assertEqual(bundle, {'connection':self.manifest,'updates':self.targets})
        self.assertEqual(json.dumps(self.manifest), before)

    def test_rejects_secret_fields_duplicates_and_unsafe_service_origins(self):
        for targets in [dict(self.targets,token='secret'), dict(self.targets,version=True),
                        dict(self.targets,counseling_data_source_id=uid(5)), dict(self.targets,student_relation_property_id='__proto__'),
                        dict(self.targets,student_relation_property_id='name with space'), dict(self.targets,students_data_source_id=uid(2))]:
            with self.assertRaises(CloudRegistrationError): update_url('https://planner.example.com',self.manifest,targets)
        for origin in ['https://planner.example.com/path','http://planner.example.com','https://user:secret@planner.example.com','https://planner.example.com:443']:
            with self.assertRaises(CloudRegistrationError): update_url(origin,self.manifest,self.targets)

    def test_canonicalizes_source_ids_but_not_property_ids(self):
        targets=dict(self.targets,students_data_source_id=uid(5).replace('-','').upper())
        self.assertEqual(validate_targets(targets),self.targets)


if __name__ == '__main__':
    unittest.main()
