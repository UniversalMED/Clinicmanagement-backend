"""
Search and filter parameter tests.

Covers:
  - Patient search by name, phone
  - Lab test search by name
  - Visit filter by patient_id
  - User search by name
  - Pagination: page_size, next/previous pages
"""
import uuid

from django.test import TestCase

from lab.models import LabTest
from .utils import (
    auth_client, make_user, make_patient, make_visit, make_lab_test,
)


# ---------------------------------------------------------------------------
# Patient search
# ---------------------------------------------------------------------------

class PatientSearchTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        make_patient(self.clinic_id, full_name='Abebe Kebede')
        make_patient(self.clinic_id, full_name='Chaltu Debebe')
        make_patient(self.clinic_id, full_name='Dawit Haile')

    def test_search_by_name_returns_match(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?search=Abebe')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['full_name'], 'Abebe Kebede')

    def test_search_is_case_insensitive(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?search=abebe')
        self.assertEqual(len(resp.data['results']), 1)

    def test_search_matches_partial_name(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?search=ebe')
        # matches 'Abebe Kebede' and 'Chaltu Debebe'
        self.assertEqual(len(resp.data['results']), 2)

    def test_search_no_match_returns_empty(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?search=zzznomatch')
        self.assertEqual(len(resp.data['results']), 0)

    def test_search_by_phone(self):
        from clinic.models import Patient
        Patient.objects.create(
            clinic_id=self.clinic_id,
            full_name='Test Patient',
            gender='male',
            phone='+251911111111',
        )
        resp = auth_client(self.admin).get('/api/clinic/patients/?search=91111')
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['full_name'], 'Test Patient')

    def test_no_search_returns_all(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/')
        self.assertEqual(len(resp.data['results']), 3)

    def test_search_scoped_to_clinic(self):
        other_clinic_id = uuid.uuid4()
        other_admin     = make_user(other_clinic_id, 'admin')
        make_patient(other_clinic_id, full_name='Abebe Other Clinic')

        resp = auth_client(self.admin).get('/api/clinic/patients/?search=Abebe')
        self.assertEqual(len(resp.data['results']), 1)  # only own clinic


# ---------------------------------------------------------------------------
# Lab test search
# ---------------------------------------------------------------------------

class LabTestSearchTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        make_lab_test(self.clinic_id, self.admin)          # name='CBC'
        LabTest.objects.create(
            clinic_id=self.clinic_id,
            name='Blood Glucose',
            price='80.00',
            is_active=True,
            created_by=self.admin.id,
        )
        LabTest.objects.create(
            clinic_id=self.clinic_id,
            name='Urinalysis',
            price='60.00',
            is_active=True,
            created_by=self.admin.id,
        )

    def test_search_by_test_name(self):
        resp = auth_client(self.admin).get('/api/lab/tests/?search=blood')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['name'], 'Blood Glucose')

    def test_search_is_case_insensitive(self):
        resp = auth_client(self.admin).get('/api/lab/tests/?search=BLOOD')
        self.assertEqual(len(resp.data['results']), 1)

    def test_search_partial_match(self):
        resp = auth_client(self.admin).get('/api/lab/tests/?search=sis')
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['name'], 'Urinalysis')

    def test_search_no_match_returns_empty(self):
        resp = auth_client(self.admin).get('/api/lab/tests/?search=xray')
        self.assertEqual(len(resp.data['results']), 0)

    def test_no_search_returns_all(self):
        resp = auth_client(self.admin).get('/api/lab/tests/')
        self.assertEqual(len(resp.data['results']), 3)

    def test_search_combined_with_active_filter(self):
        LabTest.objects.create(
            clinic_id=self.clinic_id,
            name='Blood Typing',
            price='100.00',
            is_active=False,
            created_by=self.admin.id,
        )
        doctor = make_user(self.clinic_id, 'doctor')
        # Doctor only sees active tests — inactive 'Blood Typing' excluded
        resp = auth_client(doctor).get('/api/lab/tests/?search=blood')
        self.assertEqual(len(resp.data['results']), 1)
        # Admin sees all including inactive
        resp = auth_client(self.admin).get('/api/lab/tests/?search=blood')
        self.assertEqual(len(resp.data['results']), 2)


# ---------------------------------------------------------------------------
# Visit filter by patient_id
# ---------------------------------------------------------------------------

class VisitPatientFilterTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.patient1     = make_patient(self.clinic_id)
        self.patient2     = make_patient(self.clinic_id)
        make_visit(self.clinic_id, self.patient1, self.receptionist)
        make_visit(self.clinic_id, self.patient1, self.receptionist)
        make_visit(self.clinic_id, self.patient2, self.receptionist)

    def test_filter_by_patient_id(self):
        resp = auth_client(self.admin).get(
            f'/api/clinic/visits/?patient_id={self.patient1.id}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 2)
        for v in resp.data['results']:
            self.assertEqual(str(v['patient_id']), str(self.patient1.id))

    def test_filter_returns_empty_for_unknown_patient(self):
        resp = auth_client(self.admin).get(
            f'/api/clinic/visits/?patient_id={uuid.uuid4()}'
        )
        self.assertEqual(len(resp.data['results']), 0)

    def test_no_filter_returns_all(self):
        resp = auth_client(self.admin).get('/api/clinic/visits/')
        self.assertEqual(len(resp.data['results']), 3)

    def test_patient_id_and_status_combined(self):
        make_visit(self.clinic_id, self.patient1, self.receptionist)
        resp = auth_client(self.admin).get(
            f'/api/clinic/visits/?patient_id={self.patient1.id}&status=open'
        )
        self.assertTrue(len(resp.data['results']) >= 1)
        for v in resp.data['results']:
            self.assertEqual(str(v['patient_id']), str(self.patient1.id))
            self.assertEqual(v['status'], 'open')


# ---------------------------------------------------------------------------
# User search by name
# ---------------------------------------------------------------------------

class UserSearchTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin', full_name='Admin User')
        make_user(self.clinic_id, 'doctor',       full_name='Dr Abebe')
        make_user(self.clinic_id, 'receptionist', full_name='Sara Kebede')

    def test_search_by_name(self):
        resp = auth_client(self.admin).get('/api/users/?search=Abebe')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['full_name'], 'Dr Abebe')

    def test_search_case_insensitive(self):
        resp = auth_client(self.admin).get('/api/users/?search=sara')
        self.assertEqual(len(resp.data['results']), 1)

    def test_search_and_role_combined(self):
        make_user(self.clinic_id, 'doctor', full_name='Dr Sara')
        # search 'Sara' with role=doctor → only 'Dr Sara'
        resp = auth_client(self.admin).get('/api/users/?search=Sara&role=doctor')
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['role'], 'doctor')

    def test_no_search_returns_all(self):
        resp = auth_client(self.admin).get('/api/users/')
        self.assertEqual(len(resp.data['results']), 3)

    def test_search_no_match_returns_empty(self):
        resp = auth_client(self.admin).get('/api/users/?search=zzznomatch')
        self.assertEqual(len(resp.data['results']), 0)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginationBehaviourTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        for i in range(5):
            make_patient(self.clinic_id, full_name=f'Patient {i}')

    def test_default_page_size_returns_all_within_limit(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/')
        self.assertEqual(resp.data['count'], 5)
        self.assertIn('results', resp.data)

    def test_page_size_param(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=2')
        self.assertEqual(len(resp.data['results']), 2)

    def test_next_link_present_when_more_pages(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=2')
        self.assertIsNotNone(resp.data['next'])

    def test_no_next_link_on_last_page(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=10')
        self.assertIsNone(resp.data['next'])

    def test_second_page_returns_correct_results(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=3&page=2')
        self.assertEqual(len(resp.data['results']), 2)

    def test_max_page_size_capped_at_100(self):
        for i in range(5, 110):
            make_patient(self.clinic_id, full_name=f'Patient {i}')
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=200')
        self.assertLessEqual(len(resp.data['results']), 100)
