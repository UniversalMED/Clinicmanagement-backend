"""
Lab integration tests.

Covers:
  - Only doctors can order tests
  - Only lab_techs can update order status and record results
  - Admin manages lab test catalogue (create, deactivate)
  - Clinic isolation across tests, orders, and results
"""
import uuid

from django.test import TestCase

from lab.models import TestOrder
from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_lab_test, make_test_order, make_consultation,
)


class LabTestCatalogueTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')

    def test_admin_can_create_lab_test(self):
        resp = auth_client(self.admin).post('/api/lab/tests/', {
            'name': 'CBC',
            'price': '30.00',
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], 'CBC')

    def test_doctor_cannot_create_lab_test(self):
        resp = auth_client(self.doctor).post('/api/lab/tests/', {
            'name': 'CBC',
            'price': '30.00',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_deactivate_lab_test(self):
        lab_test = make_lab_test(self.clinic_id, self.admin)
        resp = auth_client(self.admin).patch(
            f'/api/lab/tests/{lab_test.id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_active'])

    def test_inactive_tests_hidden_from_non_admin(self):
        active = make_lab_test(self.clinic_id, self.admin)
        inactive = make_lab_test(self.clinic_id, self.admin)
        inactive.is_active = False
        inactive.save()

        resp = auth_client(self.doctor).get('/api/lab/tests/')
        self.assertEqual(resp.status_code, 200)
        ids = [t['id'] for t in resp.data['results']]
        self.assertIn(str(active.id), ids)
        self.assertNotIn(str(inactive.id), ids)

    def test_inactive_tests_visible_to_admin(self):
        lab_test = make_lab_test(self.clinic_id, self.admin)
        lab_test.is_active = False
        lab_test.save()

        resp = auth_client(self.admin).get('/api/lab/tests/')
        ids = [t['id'] for t in resp.data['results']]
        self.assertIn(str(lab_test.id), ids)

    def test_lab_test_clinic_isolation(self):
        other_clinic_id = uuid.uuid4()
        other_admin = make_user(other_clinic_id, 'admin')
        other_test = make_lab_test(other_clinic_id, other_admin)

        resp = auth_client(self.admin).get('/api/lab/tests/')
        ids = [t['id'] for t in resp.data['results']]
        self.assertNotIn(str(other_test.id), ids)


class TestOrderTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.lab_tech = make_user(self.clinic_id, 'lab_tech')
        self.receptionist = make_user(self.clinic_id, 'receptionist')

        self.patient = make_patient(self.clinic_id)
        self.visit = make_visit(self.clinic_id, self.patient, self.receptionist)
        self.lab_test = make_lab_test(self.clinic_id, self.admin)

    def test_doctor_can_order_test(self):
        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': str(self.visit.id),
            'test_id': str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'pending')
        self.assertEqual(str(resp.data['ordered_by']), str(self.doctor.id))

    def test_receptionist_cannot_order_test(self):
        resp = auth_client(self.receptionist).post('/api/lab/orders/', {
            'visit_id': str(self.visit.id),
            'test_id': str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_cannot_order_inactive_test(self):
        self.lab_test.is_active = False
        self.lab_test.save()

        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': str(self.visit.id),
            'test_id': str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_cannot_order_test_for_other_clinic_visit(self):
        other_clinic_id = uuid.uuid4()
        other_user = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_user)

        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': str(other_visit.id),
            'test_id': str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_visit_id_returns_400(self):
        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': 'bad-uuid',
            'test_id': str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_lab_tech_can_update_order_status(self):
        order = make_test_order(self.visit, self.lab_test, self.doctor)
        resp = auth_client(self.lab_tech).patch(
            f'/api/lab/orders/{order.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'in_progress')

    def test_doctor_cannot_update_order_status(self):
        order = make_test_order(self.visit, self.lab_test, self.doctor)
        resp = auth_client(self.doctor).patch(
            f'/api/lab/orders/{order.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_order_clinic_isolation(self):
        other_clinic_id = uuid.uuid4()
        other_admin = make_user(other_clinic_id, 'admin')
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_patient = make_patient(other_clinic_id)
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_lab_test = make_lab_test(other_clinic_id, other_admin)
        other_order = make_test_order(other_visit, other_lab_test, other_doctor)

        resp = auth_client(self.lab_tech).get(f'/api/lab/orders/{other_order.id}/')
        self.assertEqual(resp.status_code, 404)

    def test_list_orders_filtered_by_status(self):
        make_test_order(self.visit, self.lab_test, self.doctor)
        resp = auth_client(self.doctor).get('/api/lab/orders/?status=pending')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(o['status'] == 'pending' for o in resp.data['results']))


class TestResultTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.lab_tech = make_user(self.clinic_id, 'lab_tech')
        self.receptionist = make_user(self.clinic_id, 'receptionist')

        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test = make_lab_test(self.clinic_id, self.admin)
        self.order = make_test_order(visit, lab_test, self.doctor)

    def test_lab_tech_can_record_result(self):
        resp = auth_client(self.lab_tech).post('/api/lab/results/', {
            'test_order_id': str(self.order.id),
            'result_data': {'value': '12.5', 'unit': 'g/dL'},
            'remarks': 'Normal range',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data['technician_id']), str(self.lab_tech.id))
        self.assertEqual(resp.data['result_data']['value'], '12.5')

    def test_doctor_cannot_record_result(self):
        resp = auth_client(self.doctor).post('/api/lab/results/', {
            'test_order_id': str(self.order.id),
            'result_data': {'value': '12.5'},
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_cannot_record_result_for_other_clinic_order(self):
        other_clinic_id = uuid.uuid4()
        other_admin = make_user(other_clinic_id, 'admin')
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_lab_test = make_lab_test(other_clinic_id, other_admin)
        other_order = make_test_order(other_visit, other_lab_test, other_doctor)

        resp = auth_client(self.lab_tech).post('/api/lab/results/', {
            'test_order_id': str(other_order.id),
            'result_data': {'value': '5.0'},
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_result_clinic_isolation_on_get(self):
        # Record a result for our own order first
        auth_client(self.lab_tech).post('/api/lab/results/', {
            'test_order_id': str(self.order.id),
            'result_data': {'value': '9.0'},
        }, format='json')

        # User from another clinic tries to list results
        other_lab_tech = make_user(uuid.uuid4(), 'lab_tech')
        resp = auth_client(other_lab_tech).get('/api/lab/results/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 0)
