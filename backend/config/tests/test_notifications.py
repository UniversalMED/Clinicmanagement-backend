"""
Integration tests for the notification system.

Covers:
  - lab_test_requested fires when a test order is created → lab techs notified
  - lab_test_started fires when order patched to in_progress → doctor notified
  - lab_test_completed fires when order patched to completed → doctor notified
  - No duplicate notifications (idempotent emit)
  - Acknowledge endpoint marks notification delivered
  - Recipients only see their own notifications
  - Cross-clinic isolation
"""
from django.test import TestCase

from notifications.models import Notification
from tests.utils import (
    make_clinic_id, make_user, auth_client,
    make_patient, make_visit, make_lab_test, make_test_order,
)


class NotificationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic_id   = make_clinic_id()
        cls.admin       = make_user(cls.clinic_id, 'admin')
        cls.doctor      = make_user(cls.clinic_id, 'doctor')
        cls.lab_tech1   = make_user(cls.clinic_id, 'lab_tech')
        cls.lab_tech2   = make_user(cls.clinic_id, 'lab_tech')
        cls.patient     = make_patient(cls.clinic_id)
        cls.visit       = make_visit(cls.clinic_id, cls.patient, cls.admin)
        cls.lab_test    = make_lab_test(cls.clinic_id, cls.admin)

    def setUp(self):
        self.doctor_client    = auth_client(self.doctor)
        self.lab_tech1_client = auth_client(self.lab_tech1)
        self.lab_tech2_client = auth_client(self.lab_tech2)


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

class LabTestRequestedNotificationTest(NotificationBase):
    def test_creating_order_notifies_all_lab_techs(self):
        resp = self.doctor_client.post('/api/lab/orders/', {
            'visit_id': str(self.visit.id),
            'test_id':  str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        notifs = Notification.objects.get_queryset().filter(
            event_type='lab_test_requested',
            entity_id=resp.data['id'],
        )
        recipient_ids = set(str(n.recipient_id) for n in notifs)
        self.assertIn(str(self.lab_tech1.id), recipient_ids)
        self.assertIn(str(self.lab_tech2.id), recipient_ids)
        self.assertNotIn(str(self.doctor.id), recipient_ids)

    def test_no_duplicate_notification_on_repeat_emit(self):
        order = make_test_order(self.visit, self.lab_test, self.doctor)
        from core.events import emit_lab_event, EVENT_LAB_TEST_REQUESTED
        emit_lab_event(EVENT_LAB_TEST_REQUESTED, order, self.clinic_id, self.doctor.id)
        emit_lab_event(EVENT_LAB_TEST_REQUESTED, order, self.clinic_id, self.doctor.id)

        count = Notification.objects.get_queryset().filter(
            event_type='lab_test_requested',
            entity_id=order.id,
            recipient_id=self.lab_tech1.id,
        ).count()
        self.assertEqual(count, 1)


class LabTestStatusNotificationTest(NotificationBase):
    def setUp(self):
        super().setUp()
        self.order = make_test_order(self.visit, self.lab_test, self.doctor)

    def test_in_progress_notifies_doctor(self):
        self.lab_tech1_client.patch(
            f'/api/lab/orders/{self.order.id}/',
            {'status': 'in_progress'},
            format='json',
        )
        notif = Notification.objects.get_queryset().get(
            event_type='lab_test_started',
            entity_id=self.order.id,
            recipient_id=self.doctor.id,
        )
        self.assertEqual(notif.status, 'delivered')

    def test_completed_notifies_doctor(self):
        self.lab_tech1_client.patch(
            f'/api/lab/orders/{self.order.id}/',
            {'status': 'completed'},
            format='json',
        )
        notif = Notification.objects.get_queryset().get(
            event_type='lab_test_completed',
            entity_id=self.order.id,
            recipient_id=self.doctor.id,
        )
        self.assertEqual(notif.status, 'delivered')

    def test_lab_tech_not_notified_on_status_change(self):
        self.lab_tech1_client.patch(
            f'/api/lab/orders/{self.order.id}/',
            {'status': 'completed'},
            format='json',
        )
        count = Notification.objects.get_queryset().filter(
            event_type='lab_test_completed',
            entity_id=self.order.id,
            recipient_id=self.lab_tech1.id,
        ).count()
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# Notification API
# ---------------------------------------------------------------------------

class NotificationListTest(NotificationBase):
    def setUp(self):
        super().setUp()
        self.order = make_test_order(self.visit, self.lab_test, self.doctor)
        # Create pending notification directly to avoid CELERY_TASK_ALWAYS_EAGER
        # delivering it synchronously before the list test runs.
        self.notif = Notification.objects.get_queryset().create(
            clinic_id=self.clinic_id,
            recipient_id=self.lab_tech1.id,
            event_type='lab_test_requested',
            entity_id=self.order.id,
            payload={},
            status='pending',
        )

    def test_lab_tech_sees_own_notifications(self):
        resp = self.lab_tech1_client.get('/api/notifications/')
        self.assertEqual(resp.status_code, 200)
        ids = [n['entity_id'] for n in resp.data['results']]
        self.assertIn(str(self.order.id), ids)

    def test_doctor_does_not_see_lab_tech_notifications(self):
        resp = self.doctor_client.get('/api/notifications/')
        self.assertEqual(resp.status_code, 200)
        ids = [n['entity_id'] for n in resp.data['results']]
        self.assertNotIn(str(self.order.id), ids)

    def test_unauthenticated_rejected(self):
        from rest_framework.test import APIClient
        resp = APIClient().get('/api/notifications/')
        self.assertEqual(resp.status_code, 401)


class NotificationAcknowledgeTest(NotificationBase):
    def setUp(self):
        super().setUp()
        self.order = make_test_order(self.visit, self.lab_test, self.doctor)
        from core.events import emit_lab_event, EVENT_LAB_TEST_REQUESTED
        emit_lab_event(EVENT_LAB_TEST_REQUESTED, self.order, self.clinic_id, self.doctor.id)
        self.notif = Notification.objects.get_queryset().get(
            event_type='lab_test_requested',
            entity_id=self.order.id,
            recipient_id=self.lab_tech1.id,
        )

    def test_acknowledge_marks_delivered(self):
        resp = self.lab_tech1_client.post(f'/api/notifications/{self.notif.id}/acknowledge/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'delivered')
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.status, 'delivered')

    def test_acknowledge_is_idempotent(self):
        self.lab_tech1_client.post(f'/api/notifications/{self.notif.id}/acknowledge/')
        resp = self.lab_tech1_client.post(f'/api/notifications/{self.notif.id}/acknowledge/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'delivered')

    def test_other_user_cannot_acknowledge(self):
        resp = self.lab_tech2_client.post(f'/api/notifications/{self.notif.id}/acknowledge/')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Cross-clinic isolation
# ---------------------------------------------------------------------------

class NotificationClinicIsolationTest(NotificationBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_clinic   = make_clinic_id()
        cls.other_lab_tech = make_user(cls.other_clinic, 'lab_tech')

    def setUp(self):
        super().setUp()
        self.other_client = auth_client(self.other_lab_tech)

    def test_other_clinic_lab_tech_gets_no_notifications(self):
        order = make_test_order(self.visit, self.lab_test, self.doctor)
        from core.events import emit_lab_event, EVENT_LAB_TEST_REQUESTED
        emit_lab_event(EVENT_LAB_TEST_REQUESTED, order, self.clinic_id, self.doctor.id)

        resp = self.other_client.get('/api/notifications/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)
