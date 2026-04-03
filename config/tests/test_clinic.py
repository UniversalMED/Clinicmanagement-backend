\
"""
Clinic integration tests.

Covers:
  - Full create → fetch → update flow for patients
  - Role-based access (receptionist creates, doctor cannot)
  - Clinic isolation: clinic A users cannot read or write clinic B data
  - Visit and consultation flows
"""
import uuid

from django.test import TestCase

from clinic.models import Patient, Visit
from .utils import (
    auth_client, make_user, make_patient, make_visit, make_consultation,
)


class PatientCRUDTests(TestCase):
    """Critical flow: create → fetch → update a patient."""

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.admin = make_user(self.clinic_id, 'admin')

    def test_receptionist_can_create_patient(self):
        resp = auth_client(self.receptionist).post('/api/clinic/patients/', {
            'full_name': 'John Doe',
            'gender': 'male',
            'phone': '0501234567',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['full_name'], 'John Doe')
        self.assertEqual(str(resp.data['clinic_id']), str(self.clinic_id))

    def test_doctor_cannot_create_patient(self):
        resp = auth_client(self.doctor).post('/api/clinic/patients/', {
            'full_name': 'Jane Doe',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_create_then_fetch_patient(self):
        patient = make_patient(self.clinic_id, 'Alice')
        resp = auth_client(self.doctor).get(f'/api/clinic/patients/{patient.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['full_name'], 'Alice')

    def test_create_then_update_patient(self):
        patient = make_patient(self.clinic_id, 'Bob')
        resp = auth_client(self.receptionist).patch(
            f'/api/clinic/patients/{patient.id}/',
            {'full_name': 'Robert', 'phone': '0509999999'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['full_name'], 'Robert')
        patient.refresh_from_db()
        self.assertEqual(patient.phone, '0509999999')

    def test_doctor_cannot_update_patient(self):
        patient = make_patient(self.clinic_id)
        resp = auth_client(self.doctor).patch(
            f'/api/clinic/patients/{patient.id}/',
            {'full_name': 'Hacked'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_list_patients_returns_only_clinic_patients(self):
        make_patient(self.clinic_id, 'Clinic A Patient')
        make_patient(uuid.uuid4(), 'Clinic B Patient')  # other clinic

        resp = auth_client(self.receptionist).get('/api/clinic/patients/')
        self.assertEqual(resp.status_code, 200)
        names = [p['full_name'] for p in resp.data['results']]
        self.assertIn('Clinic A Patient', names)
        self.assertNotIn('Clinic B Patient', names)


class PatientClinicIsolationTests(TestCase):
    """Clinic A users must not be able to touch Clinic B patients."""

    def setUp(self):
        self.clinic_a_id = uuid.uuid4()
        self.clinic_b_id = uuid.uuid4()

        self.user_a = make_user(self.clinic_a_id, 'receptionist')
        self.patient_b = make_patient(self.clinic_b_id, 'Clinic B Patient')

    def test_cannot_read_other_clinic_patient(self):
        resp = auth_client(self.user_a).get(f'/api/clinic/patients/{self.patient_b.id}/')
        self.assertEqual(resp.status_code, 404)

    def test_cannot_update_other_clinic_patient(self):
        resp = auth_client(self.user_a).patch(
            f'/api/clinic/patients/{self.patient_b.id}/',
            {'full_name': 'Stolen'},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)


class VisitTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.patient = make_patient(self.clinic_id)

    def test_receptionist_can_create_visit(self):
        resp = auth_client(self.receptionist).post('/api/clinic/visits/', {
            'patient_id': str(self.patient.id),
            'status': 'open',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'open')

    def test_doctor_cannot_create_visit(self):
        resp = auth_client(self.doctor).post('/api/clinic/visits/', {
            'patient_id': str(self.patient.id),
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_cannot_create_visit_for_other_clinic_patient(self):
        other_patient = make_patient(uuid.uuid4())
        resp = auth_client(self.receptionist).post('/api/clinic/visits/', {
            'patient_id': str(other_patient.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_patient_id_uuid_returns_400(self):
        resp = auth_client(self.receptionist).post('/api/clinic/visits/', {
            'patient_id': 'not-a-uuid',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_doctor_can_update_visit_status(self):
        visit = make_visit(self.clinic_id, self.patient, self.receptionist)
        resp = auth_client(self.doctor).patch(
            f'/api/clinic/visits/{visit.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'in_progress')

    def test_visit_clinic_isolation(self):
        other_clinic_id = uuid.uuid4()
        other_patient = make_patient(other_clinic_id)
        other_user = make_user(other_clinic_id, 'receptionist')
        other_visit = make_visit(other_clinic_id, other_patient, other_user)

        resp = auth_client(self.receptionist).get(f'/api/clinic/visits/{other_visit.id}/')
        self.assertEqual(resp.status_code, 404)

    def test_list_visits_filtered_by_status(self):
        make_visit(self.clinic_id, self.patient, self.receptionist)
        resp = auth_client(self.doctor).get('/api/clinic/visits/?status=open')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(v['status'] == 'open' for v in resp.data['results']))


class ConsultationTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.patient = make_patient(self.clinic_id)
        self.visit = make_visit(self.clinic_id, self.patient, self.receptionist)

    def test_doctor_can_create_consultation(self):
        resp = auth_client(self.doctor).post('/api/clinic/consultations/', {
            'visit_id': str(self.visit.id),
            'symptoms': 'fever',
            'diagnosis': 'flu',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data['doctor_id']), str(self.doctor.id))

    def test_receptionist_cannot_create_consultation(self):
        resp = auth_client(self.receptionist).post('/api/clinic/consultations/', {
            'visit_id': str(self.visit.id),
            'symptoms': 'fever',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_cannot_create_consultation_for_other_clinic_visit(self):
        other_clinic_id = uuid.uuid4()
        other_user = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_user)

        resp = auth_client(self.doctor).post('/api/clinic/consultations/', {
            'visit_id': str(other_visit.id),
            'symptoms': 'fever',
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_consultation_clinic_isolation_on_get(self):
        other_clinic_id = uuid.uuid4()
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_patient = make_patient(other_clinic_id)
        other_user = make_user(other_clinic_id, 'receptionist')
        other_visit = make_visit(other_clinic_id, other_patient, other_user)
        other_consultation = make_consultation(other_visit, other_doctor)

        resp = auth_client(self.doctor).get(
            f'/api/clinic/consultations/{other_consultation.id}/'
        )
        self.assertEqual(resp.status_code, 404)
