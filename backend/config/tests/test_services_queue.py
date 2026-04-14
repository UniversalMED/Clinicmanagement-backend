"""
Unit tests for queue service layer functions.

Tests call service functions directly — no HTTP — so they are fast and isolated.
Covers: move_to_waiting, compact_waiting_positions, call_patient, mark_no_show,
        reinsert_patient, start_visit, complete_visit.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from patient_flow.models import QueueEntry, QueueStateAudit
from patient_flow.services import (
    QueueError,
    call_patient,
    compact_waiting_positions,
    complete_visit,
    mark_no_show,
    move_to_waiting,
    reinsert_patient,
    start_visit,
)
from clinic.models import Visit
from .utils import (
    make_user, make_patient, make_visit,
    make_queue_entry,
)


def _make_waiting(clinic_id, patient, receptionist, position=1):
    return make_queue_entry(clinic_id, patient, status='waiting', queue_position=position)


def _make_called(clinic_id, patient):
    return make_queue_entry(clinic_id, patient, status='called')


class MoveToWaitingTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')

    def test_appends_to_end_of_queue(self):
        p1 = make_patient(self.clinic_id)
        p2 = make_patient(self.clinic_id)
        e1 = make_queue_entry(self.clinic_id, p1, status='checked_in')
        e2 = make_queue_entry(self.clinic_id, p2, status='checked_in')
        _make_waiting(self.clinic_id, make_patient(self.clinic_id), self.receptionist, position=1)

        move_to_waiting(e1, self.receptionist.id, is_late=False)
        self.assertEqual(e1.queue_position, 2)

    def test_late_patient_goes_to_end(self):
        p1 = make_patient(self.clinic_id)
        _make_waiting(self.clinic_id, make_patient(self.clinic_id), self.receptionist, position=1)
        entry = make_queue_entry(self.clinic_id, p1, status='checked_in')
        move_to_waiting(entry, self.receptionist.id, is_late=True)
        self.assertEqual(entry.queue_position, 2)

    def test_sets_status_to_waiting(self):
        p = make_patient(self.clinic_id)
        entry = make_queue_entry(self.clinic_id, p, status='checked_in')
        move_to_waiting(entry, self.receptionist.id, is_late=False)
        self.assertEqual(entry.status, 'waiting')


class CompactWaitingPositionsTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')

    def test_shifts_positions_down_after_gap(self):
        p2 = make_patient(self.clinic_id)
        p3 = make_patient(self.clinic_id)
        e2 = _make_waiting(self.clinic_id, p2, self.receptionist, position=2)
        e3 = _make_waiting(self.clinic_id, p3, self.receptionist, position=3)

        compact_waiting_positions(self.clinic_id, after_position=1)

        e2.refresh_from_db()
        e3.refresh_from_db()
        self.assertEqual(e2.queue_position, 1)
        self.assertEqual(e3.queue_position, 2)

    def test_does_not_shift_positions_before_gap(self):
        p1 = make_patient(self.clinic_id)
        p3 = make_patient(self.clinic_id)
        e1 = _make_waiting(self.clinic_id, p1, self.receptionist, position=1)
        e3 = _make_waiting(self.clinic_id, p3, self.receptionist, position=3)

        compact_waiting_positions(self.clinic_id, after_position=2)

        e1.refresh_from_db()
        self.assertEqual(e1.queue_position, 1)  # unchanged


class CallPatientServiceTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor    = make_user(self.clinic_id, 'doctor')
        self.patient   = make_patient(self.clinic_id)
        self.entry     = _make_waiting(
            self.clinic_id, self.patient,
            make_user(self.clinic_id, 'receptionist'), position=1
        )

    def test_transitions_to_called(self):
        call_patient(self.entry, self.doctor.id)
        self.assertEqual(self.entry.status, 'called')

    def test_clears_queue_position(self):
        call_patient(self.entry, self.doctor.id)
        self.assertIsNone(self.entry.queue_position)

    def test_sets_called_at(self):
        call_patient(self.entry, self.doctor.id)
        self.assertIsNotNone(self.entry.called_at)

    def test_raises_if_not_waiting(self):
        called = _make_called(self.clinic_id, make_patient(self.clinic_id))
        with self.assertRaises(QueueError):
            call_patient(called, self.doctor.id)

    def test_writes_audit_log(self):
        before = QueueStateAudit.objects.filter(queue_entry_id=self.entry.id).count()
        call_patient(self.entry, self.doctor.id)
        after = QueueStateAudit.objects.filter(queue_entry_id=self.entry.id).count()
        self.assertEqual(after, before + 1)


class MarkNoShowServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.patient      = make_patient(self.clinic_id)

    def test_from_waiting_transitions_to_no_show(self):
        entry = _make_waiting(self.clinic_id, self.patient, self.receptionist, position=1)
        mark_no_show(entry, self.receptionist.id, 'Did not arrive')
        self.assertEqual(entry.status, 'no_show')

    def test_from_called_transitions_to_no_show(self):
        entry = _make_called(self.clinic_id, self.patient)
        mark_no_show(entry, self.receptionist.id, 'Left without being seen')
        self.assertEqual(entry.status, 'no_show')

    def test_sets_no_show_at(self):
        entry = _make_waiting(self.clinic_id, self.patient, self.receptionist, position=1)
        mark_no_show(entry, self.receptionist.id, 'reason')
        self.assertIsNotNone(entry.no_show_at)

    def test_raises_if_in_progress(self):
        entry = make_queue_entry(self.clinic_id, self.patient, status='in_progress')
        with self.assertRaises(QueueError):
            mark_no_show(entry, self.receptionist.id, 'reason')


class ReinsertPatientServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.patient      = make_patient(self.clinic_id)
        self.entry        = make_queue_entry(self.clinic_id, self.patient, status='no_show')

    def test_transitions_to_waiting(self):
        reinsert_patient(self.entry, self.receptionist.id, 'Patient returned')
        self.assertEqual(self.entry.status, 'waiting')

    def test_appends_to_end_of_queue(self):
        existing = _make_waiting(
            self.clinic_id, make_patient(self.clinic_id), self.receptionist, position=1
        )
        reinsert_patient(self.entry, self.receptionist.id, 'Patient returned')
        self.assertEqual(self.entry.queue_position, 2)

    def test_raises_if_not_no_show(self):
        waiting = _make_waiting(self.clinic_id, make_patient(self.clinic_id), self.receptionist, position=1)
        with self.assertRaises(QueueError):
            reinsert_patient(waiting, self.receptionist.id, 'reason')


class StartVisitServiceTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor    = make_user(self.clinic_id, 'doctor')
        self.patient   = make_patient(self.clinic_id)
        self.entry     = _make_called(self.clinic_id, self.patient)

    def test_transitions_to_in_progress(self):
        entry, visit = start_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertEqual(entry.status, 'in_progress')

    def test_creates_visit_record(self):
        entry, visit = start_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertIsNotNone(visit)
        self.assertIsInstance(visit, Visit)

    def test_links_visit_to_entry(self):
        entry, visit = start_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertEqual(entry.visit_id, visit.id)

    def test_visit_has_correct_patient(self):
        entry, visit = start_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertEqual(visit.patient_id, self.patient.id)

    def test_raises_if_not_called(self):
        waiting = _make_waiting(
            self.clinic_id, make_patient(self.clinic_id),
            make_user(self.clinic_id, 'receptionist'), position=1
        )
        with self.assertRaises(QueueError):
            start_visit(waiting, self.clinic_id, self.doctor.id)


class CompleteVisitServiceTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.doctor    = make_user(self.clinic_id, 'doctor')
        self.patient   = make_patient(self.clinic_id)
        self.entry     = _make_called(self.clinic_id, self.patient)
        self.entry, self.visit = start_visit(self.entry, self.clinic_id, self.doctor.id)

    def test_transitions_to_completed(self):
        complete_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertEqual(self.entry.status, 'completed')

    def test_sets_completed_at(self):
        complete_visit(self.entry, self.clinic_id, self.doctor.id)
        self.assertIsNotNone(self.entry.completed_at)

    def test_updates_visit_status_to_completed(self):
        complete_visit(self.entry, self.clinic_id, self.doctor.id)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, 'completed')

    def test_raises_if_not_in_progress(self):
        waiting = _make_waiting(
            self.clinic_id, make_patient(self.clinic_id),
            make_user(self.clinic_id, 'receptionist'), position=1
        )
        with self.assertRaises(QueueError):
            complete_visit(waiting, self.clinic_id, self.doctor.id)
