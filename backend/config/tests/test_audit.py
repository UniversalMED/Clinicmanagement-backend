"""
Audit log integration tests.

Covers:
  - Only admins can read audit logs
  - Logs are scoped to the requesting admin's clinic
  - Filters: entity_type, action, entity_id
  - Audit entries are actually written on create/update operations
"""
import uuid

from django.test import TestCase

from audit.models import AuditLog
from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_consultation, make_lab_test, make_test_order,
)


class AuditLogAccessTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.lab_tech = make_user(self.clinic_id, 'lab_tech')

    def test_admin_can_list_audit_logs(self):
        resp = auth_client(self.admin).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 200)

    def test_doctor_cannot_list_audit_logs(self):
        resp = auth_client(self.doctor).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 403)

    def test_receptionist_cannot_list_audit_logs(self):
        resp = auth_client(self.receptionist).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_cannot_list_audit_logs(self):
        resp = auth_client(self.lab_tech).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 403)

    def test_clinic_isolation(self):
        # Insert a log for another clinic
        other_clinic_id = uuid.uuid4()
        AuditLog.objects.create(
            clinic_id=other_clinic_id,
            user_id=uuid.uuid4(),
            action='create',
            entity_type='patient',
            entity_id=uuid.uuid4(),
        )
        # Our admin must not see it
        resp = auth_client(self.admin).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 0)

    def test_admin_only_sees_own_clinic_logs(self):
        AuditLog.objects.create(
            clinic_id=self.clinic_id,
            user_id=self.admin.id,
            action='create',
            entity_type='patient',
            entity_id=uuid.uuid4(),
        )
        AuditLog.objects.create(
            clinic_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            action='create',
            entity_type='patient',
            entity_id=uuid.uuid4(),
        )
        resp = auth_client(self.admin).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(str(resp.data['results'][0]['clinic_id']), str(self.clinic_id))


class AuditLogFilterTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')

        entity_id_1 = uuid.uuid4()
        entity_id_2 = uuid.uuid4()

        AuditLog.objects.create(clinic_id=self.clinic_id, user_id=self.admin.id,
                                action='create', entity_type='patient', entity_id=entity_id_1)
        AuditLog.objects.create(clinic_id=self.clinic_id, user_id=self.admin.id,
                                action='update', entity_type='patient', entity_id=entity_id_1)
        AuditLog.objects.create(clinic_id=self.clinic_id, user_id=self.admin.id,
                                action='create', entity_type='visit', entity_id=entity_id_2)
        self.entity_id_1 = entity_id_1

    def test_filter_by_entity_type(self):
        resp = auth_client(self.admin).get('/api/audit/logs/?entity_type=patient')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 2)
        self.assertTrue(all(r['entity_type'] == 'patient' for r in resp.data['results']))

    def test_filter_by_action(self):
        resp = auth_client(self.admin).get('/api/audit/logs/?action=create')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 2)
        self.assertTrue(all(r['action'] == 'create' for r in resp.data['results']))

    def test_filter_by_entity_id(self):
        resp = auth_client(self.admin).get(f'/api/audit/logs/?entity_id={self.entity_id_1}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 2)

    def test_combined_filters(self):
        resp = auth_client(self.admin).get(
            f'/api/audit/logs/?entity_type=patient&action=update'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['action'], 'update')
        self.assertEqual(resp.data['results'][0]['entity_type'], 'patient')

    def test_no_results_for_unknown_entity_type(self):
        resp = auth_client(self.admin).get('/api/audit/logs/?entity_type=nonexistent')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 0)


class AuditLogWrittenOnOperationsTests(TestCase):
    """Verifies audit entries are created as side-effects of API calls."""

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.doctor = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.lab_tech = make_user(self.clinic_id, 'lab_tech')

    def test_creating_patient_writes_audit_log(self):
        auth_client(self.receptionist).post('/api/clinic/patients/', {
            'full_name': 'Test Patient',
            'gender': 'female',
        }, format='json')

        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='create', entity_type='patient'
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(str(log.user_id), str(self.receptionist.id))

    def test_updating_patient_writes_audit_log(self):
        patient = make_patient(self.clinic_id)
        auth_client(self.receptionist).patch(
            f'/api/clinic/patients/{patient.id}/',
            {'full_name': 'Updated Name'},
            format='json',
        )
        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='update', entity_type='patient',
            entity_id=patient.id,
        ).first()
        self.assertIsNotNone(log)

    def test_creating_visit_writes_audit_log(self):
        patient = make_patient(self.clinic_id)
        auth_client(self.receptionist).post('/api/clinic/visits/', {
            'patient_id': str(patient.id),
        }, format='json')

        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='create', entity_type='visit'
        ).first()
        self.assertIsNotNone(log)

    def test_updating_visit_writes_audit_log(self):
        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        auth_client(self.doctor).patch(
            f'/api/clinic/visits/{visit.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='update', entity_type='visit',
            entity_id=visit.id,
        ).first()
        self.assertIsNotNone(log)

    def test_creating_lab_test_order_writes_audit_log(self):
        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test = make_lab_test(self.clinic_id, self.admin)
        auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': str(visit.id),
            'test_id': str(lab_test.id),
        }, format='json')

        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='create', entity_type='test_order'
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(str(log.user_id), str(self.doctor.id))

    def test_updating_lab_order_writes_audit_log(self):
        patient = make_patient(self.clinic_id)
        visit = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test = make_lab_test(self.clinic_id, self.admin)
        order = make_test_order(visit, lab_test, self.doctor)
        auth_client(self.lab_tech).patch(
            f'/api/lab/orders/{order.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        log = AuditLog.objects.filter(
            clinic_id=self.clinic_id, action='update', entity_type='test_order',
            entity_id=order.id,
        ).first()
        self.assertIsNotNone(log)

    def test_response_field_shape(self):
        AuditLog.objects.create(
            clinic_id=self.clinic_id,
            user_id=self.admin.id,
            action='create',
            entity_type='patient',
            entity_id=uuid.uuid4(),
        )
        resp = auth_client(self.admin).get('/api/audit/logs/')
        self.assertEqual(resp.status_code, 200)
        entry = resp.data['results'][0]
        for field in ['id', 'clinic_id', 'user_id', 'action', 'entity_type', 'entity_id', 'timestamp']:
            self.assertIn(field, entry)
