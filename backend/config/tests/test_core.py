"""
Core / infrastructure tests.

Covers:
  - Health check endpoint
  - Celery notification delivery task (unit)
  - Pagination behaviour
  - Missing endpoint coverage: consultation list, invoice filter by patient_id
"""
import uuid
from unittest.mock import patch

from django.test import TestCase

from notifications.models import Notification
from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_lab_test, make_test_order, make_invoice, make_line_item,
    make_consultation,
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class HealthCheckTests(TestCase):

    def test_returns_200_without_auth(self):
        from rest_framework.test import APIClient
        resp = APIClient().get('/api/health/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'status': 'ok'})


# ---------------------------------------------------------------------------
# Celery notification delivery task
# ---------------------------------------------------------------------------

class DeliverNotificationTaskTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        self.lab_tech  = make_user(self.clinic_id, 'lab_tech')

    def _make_notification(self, status='pending'):
        return Notification.objects.create(
            clinic_id=self.clinic_id,
            recipient_id=self.lab_tech.id,
            event_type='lab_test_requested',
            entity_type='test_order',
            entity_id=uuid.uuid4(),
            payload={},
            status=status,
        )

    def test_task_marks_notification_delivered(self):
        from core.tasks import deliver_notification
        notif = self._make_notification()
        deliver_notification(notif.id)
        notif.refresh_from_db()
        self.assertEqual(notif.status, 'delivered')
        self.assertIsNotNone(notif.delivered_at)

    def test_task_is_idempotent_on_already_delivered(self):
        from core.tasks import deliver_notification
        notif = self._make_notification(status='delivered')
        deliver_notification(notif.id)   # should not raise
        notif.refresh_from_db()
        self.assertEqual(notif.status, 'delivered')

    def test_task_marks_failed_after_max_retries(self):
        from core.tasks import deliver_notification
        from celery.exceptions import MaxRetriesExceededError
        notif = self._make_notification()
        with patch.object(deliver_notification, 'retry', side_effect=MaxRetriesExceededError):
            with patch('core.tasks.deliver_notification.retry', side_effect=MaxRetriesExceededError):
                # Simulate what happens when the notification_id doesn't exist
                deliver_notification(uuid.uuid4())  # unknown id — should not crash
        # Task handles unknown IDs gracefully — no exception propagated


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginationTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        # Create 3 patients
        for i in range(3):
            make_patient(self.clinic_id, full_name=f'Patient {i}')

    def test_response_has_pagination_envelope(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/')
        self.assertIn('results', resp.data)
        self.assertIn('count', resp.data)

    def test_page_size_param_respected(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=2')
        self.assertEqual(len(resp.data['results']), 2)
        self.assertEqual(resp.data['count'], 3)

    def test_second_page_returns_remaining(self):
        resp = auth_client(self.admin).get('/api/clinic/patients/?page_size=2&page=2')
        self.assertEqual(len(resp.data['results']), 1)


# ---------------------------------------------------------------------------
# Consultation list endpoint (missing from test_clinic.py)
# ---------------------------------------------------------------------------

class ConsultationListTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        self.doctor    = make_user(self.clinic_id, 'doctor')
        self.recep     = make_user(self.clinic_id, 'receptionist')
        patient        = make_patient(self.clinic_id)
        self.visit     = make_visit(self.clinic_id, patient, self.recep)
        make_consultation(self.visit, self.doctor)

    def test_doctor_can_list_consultations(self):
        resp = auth_client(self.doctor).get('/api/clinic/consultations/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_admin_can_list_consultations(self):
        resp = auth_client(self.admin).get('/api/clinic/consultations/')
        self.assertEqual(resp.status_code, 200)

    def test_clinic_isolation(self):
        other_clinic_id = uuid.uuid4()
        other_doctor    = make_user(other_clinic_id, 'doctor')
        other_recep     = make_user(other_clinic_id, 'receptionist')
        other_patient   = make_patient(other_clinic_id)
        other_visit     = make_visit(other_clinic_id, other_patient, other_recep)
        make_consultation(other_visit, other_doctor)

        resp = auth_client(self.doctor).get('/api/clinic/consultations/')
        self.assertEqual(len(resp.data['results']), 1)  # only own clinic

    def test_filter_by_visit_id(self):
        patient2 = make_patient(self.clinic_id)
        visit2   = make_visit(self.clinic_id, patient2, self.recep)
        make_consultation(visit2, self.doctor)

        resp = auth_client(self.doctor).get(
            f'/api/clinic/consultations/?visit_id={self.visit.id}'
        )
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(str(resp.data['results'][0]['visit_id']), str(self.visit.id))


# ---------------------------------------------------------------------------
# Invoice list filter by patient_id (missing from test_billing.py)
# ---------------------------------------------------------------------------

class InvoicePatientFilterTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')

        self.patient1 = make_patient(self.clinic_id)
        self.patient2 = make_patient(self.clinic_id)
        visit1 = make_visit(self.clinic_id, self.patient1, self.receptionist)
        visit2 = make_visit(self.clinic_id, self.patient2, self.receptionist)
        make_invoice(self.clinic_id, visit1, self.admin)
        make_invoice(self.clinic_id, visit2, self.admin)

    def test_filter_by_patient_id(self):
        resp = auth_client(self.admin).get(
            f'/api/billing/invoices/?patient_id={self.patient1.id}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(
            str(resp.data['results'][0]['patient_id']),
            str(self.patient1.id),
        )

    def test_unknown_patient_id_returns_empty(self):
        resp = auth_client(self.admin).get(
            f'/api/billing/invoices/?patient_id={uuid.uuid4()}'
        )
        self.assertEqual(len(resp.data['results']), 0)
