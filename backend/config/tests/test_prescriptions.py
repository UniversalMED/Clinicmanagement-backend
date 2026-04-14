"""
Prescription integration tests.

Covers:
  - Only doctors can create prescriptions
  - Prescription must have at least one item
  - Consultation must belong to the same clinic
  - Clinic isolation on list and detail
  - prescribed_by is set from the authenticated user
  - Items are returned inline on read
"""
import uuid

from django.test import TestCase

from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_consultation, make_prescription,
)


class PrescriptionCreateTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.lab_tech = make_user(self.clinic_id, 'lab_tech')
        self.admin = make_user(self.clinic_id, 'admin')

        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        self.consultation = make_consultation(visit, self.doctor)

    def _valid_payload(self, consultation_id=None):
        return {
            'consultation_id': str(consultation_id or self.consultation.id),
            'notes': 'Take after meals.',
            'items': [
                {
                    'medication': 'Amoxicillin',
                    'dosage': '250mg',
                    'frequency': '2x daily',
                    'duration': '7 days',
                    'instructions': 'With food',
                },
            ],
        }

    def test_doctor_can_create_prescription(self):
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', self._valid_payload(), format='json'
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data['prescribed_by']), str(self.doctor.id))
        self.assertEqual(len(resp.data['items']), 1)
        self.assertEqual(resp.data['items'][0]['medication'], 'Amoxicillin')

    def test_prescribed_by_is_set_automatically(self):
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', self._valid_payload(), format='json'
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data['prescribed_by']), str(self.doctor.id))

    def test_receptionist_cannot_create_prescription(self):
        resp = auth_client(self.receptionist).post(
            '/api/clinic/prescriptions/', self._valid_payload(), format='json'
        )
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_cannot_create_prescription(self):
        resp = auth_client(self.lab_tech).post(
            '/api/clinic/prescriptions/', self._valid_payload(), format='json'
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_create_prescription(self):
        resp = auth_client(self.admin).post(
            '/api/clinic/prescriptions/', self._valid_payload(), format='json'
        )
        self.assertEqual(resp.status_code, 403)

    def test_empty_items_list_returns_400(self):
        payload = self._valid_payload()
        payload['items'] = []
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', payload, format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_items_returns_400(self):
        payload = self._valid_payload()
        del payload['items']
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', payload, format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_item_missing_required_field_returns_400(self):
        payload = self._valid_payload()
        payload['items'] = [{'medication': 'Aspirin'}]  # missing dosage + frequency
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', payload, format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_consultation_from_other_clinic_returns_404(self):
        other_clinic_id = uuid.uuid4()
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_consultation = make_consultation(other_visit, other_doctor)

        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/',
            self._valid_payload(consultation_id=other_consultation.id),
            format='json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiple_items_are_saved(self):
        payload = self._valid_payload()
        payload['items'] = [
            {'medication': 'Drug A', 'dosage': '10mg', 'frequency': 'once daily'},
            {'medication': 'Drug B', 'dosage': '5mg', 'frequency': 'twice daily', 'duration': '5 days'},
        ]
        resp = auth_client(self.doctor).post(
            '/api/clinic/prescriptions/', payload, format='json'
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data['items']), 2)


class PrescriptionReadTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')

        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        self.consultation = make_consultation(visit, self.doctor)
        self.prescription = make_prescription(self.consultation, self.doctor)

    def test_doctor_can_list_prescriptions(self):
        resp = auth_client(self.doctor).get('/api/clinic/prescriptions/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_receptionist_can_list_prescriptions(self):
        resp = auth_client(self.receptionist).get('/api/clinic/prescriptions/')
        self.assertEqual(resp.status_code, 200)

    def test_list_filter_by_consultation_id(self):
        # Create a second consultation + prescription
        patient2 = make_patient(self.clinic_id)
        visit2 = make_visit(self.clinic_id, patient2, self.receptionist)
        consultation2 = make_consultation(visit2, self.doctor)
        make_prescription(consultation2, self.doctor)

        resp = auth_client(self.doctor).get(
            f'/api/clinic/prescriptions/?consultation_id={self.consultation.id}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(str(resp.data['results'][0]['consultation_id']), str(self.consultation.id))

    def test_detail_returns_items(self):
        resp = auth_client(self.doctor).get(f'/api/clinic/prescriptions/{self.prescription.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('items', resp.data)
        self.assertGreater(len(resp.data['items']), 0)

    def test_clinic_isolation_on_list(self):
        other_clinic_id = uuid.uuid4()
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_consultation = make_consultation(other_visit, other_doctor)
        make_prescription(other_consultation, other_doctor)

        resp = auth_client(self.doctor).get('/api/clinic/prescriptions/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)  # only own clinic's

    def test_clinic_isolation_on_detail(self):
        other_clinic_id = uuid.uuid4()
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_consultation = make_consultation(other_visit, other_doctor)
        other_prescription = make_prescription(other_consultation, other_doctor)

        resp = auth_client(self.doctor).get(f'/api/clinic/prescriptions/{other_prescription.id}/')
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_request_returns_401(self):
        from rest_framework.test import APIClient
        resp = APIClient().get('/api/clinic/prescriptions/')
        self.assertEqual(resp.status_code, 401)
