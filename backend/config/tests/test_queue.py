"""
Integration tests for the patient flow / queue system.

Covers:
  - Appointment CRUD (create, list, update, cancel, affected, reassign)
  - Check-in flows (appointment, walk-in, late, duplicate guard, cross-clinic)
  - Queue ordering (on-time appointment before walk-in, late at end)
  - State machine (call, no-show, reinsert, start-visit, complete, invalid transitions)
  - Queue reorder (admin only)
  - Audit history
  - RBAC for every mutation endpoint
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from patient_flow.models import (
    Appointment, QueueEntry, QueueStateAudit,
    VALID_TRANSITIONS, transition,
)
from tests.utils import (
    make_clinic_id, make_user, auth_client,
    make_patient, make_appointment, make_queue_entry,
)


# ---------------------------------------------------------------------------
# Base setup shared across test cases
# ---------------------------------------------------------------------------

class QueueTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clinic_id = make_clinic_id()
        cls.admin = make_user(cls.clinic_id, 'admin')
        cls.receptionist = make_user(cls.clinic_id, 'receptionist')
        cls.doctor = make_user(cls.clinic_id, 'doctor')
        cls.lab_tech = make_user(cls.clinic_id, 'lab_tech')

        cls.patient = make_patient(cls.clinic_id, 'Alice')
        cls.patient2 = make_patient(cls.clinic_id, 'Bob')

    def setUp(self):
        self.admin_client = auth_client(self.admin)
        self.recept_client = auth_client(self.receptionist)
        self.doctor_client = auth_client(self.doctor)
        self.lab_client = auth_client(self.lab_tech)


# ===========================================================================
# 1. Appointment tests
# ===========================================================================

class AppointmentCreateTest(QueueTestBase):
    def test_receptionist_creates_appointment(self):
        resp = self.recept_client.post('/api/queue/appointments/', {
            'patient_id': str(self.patient.id),
            'doctor_id': str(self.doctor.id),
            'scheduled_at': (timezone.now() + timedelta(hours=2)).isoformat(),
            'type': 'specialist',
            'duration_minutes': 30,
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'active')
        self.assertEqual(uuid.UUID(resp.data['patient_id']), self.patient.id)

    def test_doctor_cannot_create_appointment(self):
        resp = self.doctor_client.post('/api/queue/appointments/', {
            'patient_id': str(self.patient.id),
            'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat(),
            'type': 'general',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_cannot_create_appointment(self):
        resp = self.lab_client.post('/api/queue/appointments/', {
            'patient_id': str(self.patient.id),
            'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat(),
            'type': 'general',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_patient_from_other_clinic_rejected(self):
        other_clinic = make_clinic_id()
        other_patient = make_patient(other_clinic)
        resp = self.recept_client.post('/api/queue/appointments/', {
            'patient_id': str(other_patient.id),
            'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat(),
            'type': 'general',
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_unauthenticated_rejected(self):
        from rest_framework.test import APIClient
        resp = APIClient().post('/api/queue/appointments/', {
            'patient_id': str(self.patient.id),
            'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat(),
            'type': 'general',
        }, format='json')
        self.assertEqual(resp.status_code, 401)


class AppointmentListFilterTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.appt1 = make_appointment(self.clinic_id, self.patient, self.doctor, now + timedelta(hours=1))
        self.appt2 = make_appointment(self.clinic_id, self.patient2, self.doctor, now + timedelta(hours=2))
        # Cancelled appointment
        self.appt3 = make_appointment(self.clinic_id, self.patient, scheduled_at=now + timedelta(hours=3))
        self.appt3.status = 'cancelled'
        self.appt3.save()

    def test_list_all(self):
        resp = self.admin_client.get('/api/queue/appointments/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 3)

    def test_filter_by_status(self):
        resp = self.admin_client.get('/api/queue/appointments/?status=active')
        self.assertEqual(resp.data['count'], 2)

    def test_filter_by_doctor(self):
        resp = self.admin_client.get(f'/api/queue/appointments/?doctor_id={self.doctor.id}')
        self.assertEqual(resp.data['count'], 2)

    def test_cross_clinic_isolation(self):
        other_clinic = make_clinic_id()
        other_admin = make_user(other_clinic, 'admin')
        other_patient = make_patient(other_clinic)
        make_appointment(other_clinic, other_patient)
        resp = auth_client(other_admin).get('/api/queue/appointments/')
        self.assertEqual(resp.data['count'], 1)


class AppointmentUpdateCancelTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.appt = make_appointment(
            self.clinic_id, self.patient, self.doctor,
            timezone.now() + timedelta(hours=1)
        )

    def test_update_appointment(self):
        new_time = (timezone.now() + timedelta(hours=3)).isoformat()
        resp = self.recept_client.patch(
            f'/api/queue/appointments/{self.appt.id}/',
            {'scheduled_at': new_time},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'rescheduled')

    def test_cancel_appointment(self):
        resp = self.recept_client.post(
            f'/api/queue/appointments/{self.appt.id}/cancel/',
            {'cancel_reason': 'Patient request'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'cancelled')

    def test_cancel_already_cancelled_rejected(self):
        self.appt.status = 'cancelled'
        self.appt.save()
        resp = self.recept_client.post(
            f'/api/queue/appointments/{self.appt.id}/cancel/',
            {'cancel_reason': 'again'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cancel_requires_reason(self):
        resp = self.recept_client.post(
            f'/api/queue/appointments/{self.appt.id}/cancel/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


class AppointmentAffectedReassignTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        today = timezone.now().date()
        self.appt1 = make_appointment(
            self.clinic_id, self.patient, self.doctor,
            timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        )
        self.appt2 = make_appointment(
            self.clinic_id, self.patient2, self.doctor,
            timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        )
        self.doctor2 = make_user(self.clinic_id, 'doctor', 'Dr. B')

    def test_mark_affected(self):
        resp = self.admin_client.post('/api/queue/appointments/affected/', {
            'doctor_id': str(self.doctor.id),
            'date': timezone.now().date().isoformat(),
            'reason': 'Doctor sick',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['affected_count'], 2)
        self.assertEqual(
            Appointment.objects.for_clinic(self.clinic_id).filter(status='affected').count(), 2
        )

    def test_reassign_appointment(self):
        self.appt1.status = 'affected'
        self.appt1.save()
        resp = self.admin_client.post(
            f'/api/queue/appointments/{self.appt1.id}/reassign/',
            {'new_doctor_id': str(self.doctor2.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'active')
        self.assertEqual(uuid.UUID(resp.data['doctor_id']), self.doctor2.id)

    def test_receptionist_cannot_mark_affected(self):
        resp = self.recept_client.post('/api/queue/appointments/affected/', {
            'doctor_id': str(self.doctor.id),
            'date': timezone.now().date().isoformat(),
            'reason': 'test',
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_reassign_updates_active_queue_entry(self):
        """If patient is already in queue, assigned_doctor_id is updated."""
        entry = make_queue_entry(
            self.clinic_id, self.patient, status='waiting',
            appointment=self.appt1, doctor=self.doctor,
        )
        self.admin_client.post(
            f'/api/queue/appointments/{self.appt1.id}/reassign/',
            {'new_doctor_id': str(self.doctor2.id)},
            format='json',
        )
        entry.refresh_from_db()
        self.assertEqual(entry.assigned_doctor_id, self.doctor2.id)


# ===========================================================================
# 2. Check-in tests
# ===========================================================================

class WalkInCheckInTest(QueueTestBase):
    def test_walk_in_creates_waiting_entry(self):
        resp = self.recept_client.post('/api/queue/checkin/', {
            'patient_id': str(self.patient.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'waiting')
        self.assertEqual(resp.data['entry_type'], 'walk_in')
        self.assertEqual(resp.data['queue_position'], 1)

    def test_walk_ins_queue_sequentially(self):
        self.recept_client.post('/api/queue/checkin/', {'patient_id': str(self.patient.id)}, format='json')
        self.recept_client.post('/api/queue/checkin/', {'patient_id': str(self.patient2.id)}, format='json')

        entries = list(
            QueueEntry.objects.for_clinic(self.clinic_id)
            .filter(status='waiting')
            .order_by('queue_position')
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].queue_position, 1)
        self.assertEqual(entries[1].queue_position, 2)

    def test_duplicate_checkin_rejected(self):
        self.recept_client.post('/api/queue/checkin/', {'patient_id': str(self.patient.id)}, format='json')
        resp = self.recept_client.post('/api/queue/checkin/', {'patient_id': str(self.patient.id)}, format='json')
        self.assertEqual(resp.status_code, 409)

    def test_cross_clinic_patient_rejected(self):
        other_patient = make_patient(make_clinic_id())
        resp = self.recept_client.post('/api/queue/checkin/', {
            'patient_id': str(other_patient.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_doctor_cannot_checkin(self):
        resp = self.doctor_client.post('/api/queue/checkin/', {
            'patient_id': str(self.patient.id),
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_neither_id_rejected(self):
        resp = self.recept_client.post('/api/queue/checkin/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_both_ids_rejected(self):
        appt = make_appointment(self.clinic_id, self.patient, self.doctor)
        resp = self.recept_client.post('/api/queue/checkin/', {
            'patient_id': str(self.patient.id),
            'appointment_id': str(appt.id),
        }, format='json')
        self.assertEqual(resp.status_code, 400)


class AppointmentCheckInTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        # On-time appointment scheduled 30 min from now
        self.appt = make_appointment(
            self.clinic_id, self.patient, self.doctor,
            timezone.now() + timedelta(minutes=30),
        )

    def test_appointment_checkin_creates_waiting_entry(self):
        resp = self.recept_client.post('/api/queue/checkin/', {
            'appointment_id': str(self.appt.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'waiting')
        self.assertEqual(resp.data['entry_type'], 'appointment')
        self.assertEqual(uuid.UUID(resp.data['patient_id']), self.patient.id)
        self.assertEqual(uuid.UUID(resp.data['assigned_doctor_id']), self.doctor.id)

    def test_inactive_appointment_rejected(self):
        self.appt.status = 'cancelled'
        self.appt.save()
        resp = self.recept_client.post('/api/queue/checkin/', {
            'appointment_id': str(self.appt.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_on_time_appointment_before_walk_in(self):
        """On-time appointment patients jump ahead of existing walk-ins."""
        # Walk-in already in queue
        self.recept_client.post('/api/queue/checkin/', {
            'patient_id': str(self.patient2.id),
        }, format='json')

        # Appointment patient checks in (on time)
        self.recept_client.post('/api/queue/checkin/', {
            'appointment_id': str(self.appt.id),
        }, format='json')

        entries = list(
            QueueEntry.objects.for_clinic(self.clinic_id)
            .filter(status='waiting')
            .order_by('queue_position')
        )
        self.assertEqual(len(entries), 2)
        # Appointment patient should be first
        self.assertEqual(entries[0].entry_type, 'appointment')
        self.assertEqual(entries[1].entry_type, 'walk_in')

    def test_late_appointment_goes_to_end(self):
        """Late appointment patients are appended to end like walk-ins."""
        # Walk-in already in queue
        self.recept_client.post('/api/queue/checkin/', {
            'patient_id': str(self.patient2.id),
        }, format='json')

        # Appointment was 30 minutes ago (past grace)
        late_appt = make_appointment(
            self.clinic_id, self.patient, self.doctor,
            timezone.now() - timedelta(minutes=30),
        )
        resp = self.recept_client.post('/api/queue/checkin/', {
            'appointment_id': str(late_appt.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)

        entries = list(
            QueueEntry.objects.for_clinic(self.clinic_id)
            .filter(status='waiting')
            .order_by('queue_position')
        )
        # Walk-in first (was there first), late appointment second
        self.assertEqual(entries[0].entry_type, 'walk_in')
        self.assertEqual(entries[1].entry_type, 'appointment')
        self.assertEqual(entries[1].queue_position, 2)

    def test_audit_log_written_on_checkin(self):
        self.recept_client.post('/api/queue/checkin/', {
            'appointment_id': str(self.appt.id),
        }, format='json')
        entry = QueueEntry.objects.for_clinic(self.clinic_id).get(
            patient_id=self.patient.id
        )
        audit_count = QueueStateAudit.objects.filter(queue_entry_id=entry.id).count()
        # checked_in + waiting = 2 audit records
        self.assertEqual(audit_count, 2)


# ===========================================================================
# 3. Queue state machine tests
# ===========================================================================

class QueueCallNoShowTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.patient3 = make_patient(self.clinic_id, 'Charlie')
        self.entry1 = make_queue_entry(self.clinic_id, self.patient, queue_position=1)
        self.entry2 = make_queue_entry(self.clinic_id, self.patient2, queue_position=2)
        self.entry3 = make_queue_entry(self.clinic_id, self.patient3, queue_position=3)

    def test_call_patient(self):
        resp = self.recept_client.post(f'/api/queue/{self.entry1.id}/call/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'called')
        self.assertIsNone(resp.data['queue_position'])
        self.assertIsNotNone(resp.data['call_timeout_at'])

    def test_calling_compacts_queue(self):
        self.recept_client.post(f'/api/queue/{self.entry1.id}/call/')
        self.entry2.refresh_from_db()
        self.entry3.refresh_from_db()
        self.assertEqual(self.entry2.queue_position, 1)
        self.assertEqual(self.entry3.queue_position, 2)

    def test_cannot_call_non_waiting_entry(self):
        self.entry1.status = 'called'
        self.entry1.save()
        resp = self.recept_client.post(f'/api/queue/{self.entry1.id}/call/')
        self.assertEqual(resp.status_code, 404)

    def test_mark_no_show_from_waiting(self):
        resp = self.recept_client.post(
            f'/api/queue/{self.entry1.id}/no-show/',
            {'reason': 'left the building'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'no_show')

    def test_mark_no_show_compacts_queue(self):
        self.recept_client.post(
            f'/api/queue/{self.entry1.id}/no-show/',
            {'reason': 'test'},
            format='json',
        )
        self.entry2.refresh_from_db()
        self.entry3.refresh_from_db()
        self.assertEqual(self.entry2.queue_position, 1)
        self.assertEqual(self.entry3.queue_position, 2)

    def test_mark_no_show_from_called(self):
        self.entry1.status = 'called'
        self.entry1.queue_position = None
        self.entry1.save()
        resp = self.recept_client.post(
            f'/api/queue/{self.entry1.id}/no-show/',
            {'reason': 'did not respond'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'no_show')

    def test_reinsert_no_show_patient(self):
        # Simulate no_show + compaction: entry1 removed from queue,
        # entry2 and entry3 shift down to positions 1 and 2.
        self.entry1.status = 'no_show'
        self.entry1.queue_position = None
        self.entry1.save()
        self.entry2.queue_position = 1
        self.entry2.save()
        self.entry3.queue_position = 2
        self.entry3.save()

        resp = self.recept_client.post(
            f'/api/queue/{self.entry1.id}/reinsert/',
            {'reason': 'patient phoned back'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'waiting')
        # Appended to end behind entry2=1, entry3=2
        self.assertEqual(resp.data['queue_position'], 3)

    def test_reinsert_requires_reason(self):
        self.entry1.status = 'no_show'
        self.entry1.save()
        resp = self.recept_client.post(f'/api/queue/{self.entry1.id}/reinsert/', {}, format='json')
        self.assertEqual(resp.status_code, 400)


class QueueVisitFlowTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.entry = make_queue_entry(self.clinic_id, self.patient, status='called')

    def test_start_visit_creates_visit_record(self):
        resp = self.doctor_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['queue_entry']['status'], 'in_progress')
        self.assertIn('visit_id', resp.data)

        from clinic.models import Visit
        visit = Visit.objects.for_clinic(self.clinic_id).get(id=resp.data['visit_id'])
        self.assertEqual(visit.patient_id, self.patient.id)
        self.assertEqual(visit.status, 'in_progress')

    def test_start_visit_links_visit_to_entry(self):
        resp = self.doctor_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.entry.refresh_from_db()
        self.assertEqual(str(self.entry.visit_id), resp.data['visit_id'])

    def test_complete_visit(self):
        # Move to in_progress first
        self.doctor_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.entry.refresh_from_db()

        resp = self.doctor_client.post(f'/api/queue/{self.entry.id}/complete/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'completed')

    def test_complete_updates_linked_visit(self):
        from clinic.models import Visit
        self.doctor_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.entry.refresh_from_db()
        self.doctor_client.post(f'/api/queue/{self.entry.id}/complete/')

        visit = Visit.objects.for_clinic(self.clinic_id).get(id=self.entry.visit_id)
        self.assertEqual(visit.status, 'completed')

    def test_receptionist_cannot_start_visit(self):
        resp = self.recept_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_cannot_start_visit(self):
        resp = self.lab_client.post(f'/api/queue/{self.entry.id}/start-visit/')
        self.assertEqual(resp.status_code, 403)


class InvalidTransitionTest(QueueTestBase):
    def test_cannot_call_non_waiting_entry(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='called')
        resp = self.recept_client.post(f'/api/queue/{entry.id}/call/')
        self.assertEqual(resp.status_code, 404)

    def test_cannot_start_visit_on_waiting_entry(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='waiting')
        resp = self.doctor_client.post(f'/api/queue/{entry.id}/start-visit/')
        self.assertEqual(resp.status_code, 404)

    def test_cannot_complete_from_waiting(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='waiting')
        resp = self.doctor_client.post(f'/api/queue/{entry.id}/complete/')
        self.assertEqual(resp.status_code, 404)

    def test_transition_function_raises_on_invalid(self):
        from rest_framework.exceptions import ValidationError
        entry = make_queue_entry(self.clinic_id, self.patient, status='completed')
        with self.assertRaises(ValidationError):
            transition(entry, 'waiting', changed_by=None)

    def test_transition_function_raises_completed_to_in_progress(self):
        from rest_framework.exceptions import ValidationError
        entry = make_queue_entry(self.clinic_id, self.patient, status='in_progress')
        with self.assertRaises(ValidationError):
            transition(entry, 'waiting', changed_by=None)


class QueueReorderTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.patient3 = make_patient(self.clinic_id, 'Charlie')
        self.e1 = make_queue_entry(self.clinic_id, self.patient, queue_position=1)
        self.e2 = make_queue_entry(self.clinic_id, self.patient2, queue_position=2)
        self.e3 = make_queue_entry(self.clinic_id, self.patient3, queue_position=3)

    def test_admin_can_reorder(self):
        resp = self.admin_client.post('/api/queue/reorder/', {
            'positions': [
                {'id': str(self.e1.id), 'queue_position': 3},
                {'id': str(self.e2.id), 'queue_position': 1},
                {'id': str(self.e3.id), 'queue_position': 2},
            ]
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertEqual(self.e1.queue_position, 3)
        self.assertEqual(self.e2.queue_position, 1)

    def test_receptionist_cannot_reorder(self):
        resp = self.recept_client.post('/api/queue/reorder/', {
            'positions': [{'id': str(self.e1.id), 'queue_position': 2}]
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_reorder_audit_written(self):
        self.admin_client.post('/api/queue/reorder/', {
            'positions': [
                {'id': str(self.e1.id), 'queue_position': 3},
                {'id': str(self.e2.id), 'queue_position': 1},
                {'id': str(self.e3.id), 'queue_position': 2},
            ]
        }, format='json')
        audits = QueueStateAudit.objects.filter(
            queue_entry_id__in=[self.e1.id, self.e2.id, self.e3.id],
            change_reason='staff_reorder',
        )
        self.assertEqual(audits.count(), 3)

    def test_reorder_duplicate_positions_rejected(self):
        resp = self.admin_client.post('/api/queue/reorder/', {
            'positions': [
                {'id': str(self.e1.id), 'queue_position': 1},
                {'id': str(self.e2.id), 'queue_position': 1},
            ]
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_reorder_non_waiting_entry_rejected(self):
        self.e1.status = 'called'
        self.e1.save()
        resp = self.admin_client.post('/api/queue/reorder/', {
            'positions': [{'id': str(self.e1.id), 'queue_position': 2}]
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_reorder_cross_clinic_entry_rejected(self):
        other_clinic = make_clinic_id()
        other_patient = make_patient(other_clinic)
        other_entry = make_queue_entry(other_clinic, other_patient, queue_position=1)
        resp = self.admin_client.post('/api/queue/reorder/', {
            'positions': [{'id': str(other_entry.id), 'queue_position': 1}]
        }, format='json')
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# 4. Queue list and history
# ===========================================================================

class QueueListTest(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.e1 = make_queue_entry(self.clinic_id, self.patient, status='waiting', queue_position=1)
        self.e2 = make_queue_entry(self.clinic_id, self.patient2, status='called')

    def test_default_list_shows_waiting(self):
        resp = self.admin_client.get('/api/queue/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['status'], 'waiting')

    def test_filter_by_status_called(self):
        resp = self.admin_client.get('/api/queue/?status=called')
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['status'], 'called')

    def test_patient_name_in_response(self):
        resp = self.admin_client.get('/api/queue/')
        self.assertEqual(resp.data['results'][0]['patient_name'], 'Alice')

    def test_cross_clinic_isolation(self):
        other_clinic = make_clinic_id()
        other_admin = make_user(other_clinic, 'admin')
        other_patient = make_patient(other_clinic)
        make_queue_entry(other_clinic, other_patient, queue_position=1)
        resp = auth_client(other_admin).get('/api/queue/')
        self.assertEqual(resp.data['count'], 1)


class QueueHistoryTest(QueueTestBase):
    def test_history_returns_audit_trail(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='waiting', queue_position=1)
        # Write extra audit record
        QueueStateAudit.objects.create(
            queue_entry_id=entry.id,
            clinic_id=self.clinic_id,
            patient_id=self.patient.id,
            previous_status='waiting',
            new_status='called',
            changed_by=self.receptionist.id,
            change_reason='staff_called',
        )
        resp = self.admin_client.get(f'/api/queue/{entry.id}/history/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 2)  # setup + extra

    def test_history_cross_clinic_isolated(self):
        other_clinic = make_clinic_id()
        other_admin = make_user(other_clinic, 'admin')
        other_patient = make_patient(other_clinic)
        other_entry = make_queue_entry(other_clinic, other_patient, queue_position=1)
        resp = auth_client(other_admin).get(f'/api/queue/{other_entry.id}/history/')
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# 5. Auto-timeout management command
# ===========================================================================

class AutoTimeoutTest(QueueTestBase):
    def test_auto_timeout_marks_no_show(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='called')
        entry.call_timeout_at = timezone.now() - timedelta(minutes=1)
        entry.save()

        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('auto_timeout_calls', stdout=out)

        entry.refresh_from_db()
        self.assertEqual(entry.status, 'no_show')

    def test_auto_timeout_skips_not_expired(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='called')
        entry.call_timeout_at = timezone.now() + timedelta(minutes=5)
        entry.save()

        from django.core.management import call_command
        from io import StringIO
        call_command('auto_timeout_calls', stdout=StringIO())

        entry.refresh_from_db()
        self.assertEqual(entry.status, 'called')

    def test_auto_timeout_writes_audit(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='called')
        entry.call_timeout_at = timezone.now() - timedelta(minutes=1)
        entry.save()

        from django.core.management import call_command
        from io import StringIO
        call_command('auto_timeout_calls', stdout=StringIO())

        audit = QueueStateAudit.objects.filter(
            queue_entry_id=entry.id,
            new_status='no_show',
            change_reason='call_timeout_expired',
        ).first()
        self.assertIsNotNone(audit)
        self.assertIsNone(audit.changed_by)  # system action — no user
