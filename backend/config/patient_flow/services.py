"""
Queue / patient flow service layer.

All queue state transitions live here so they can be called from views,
Celery tasks (auto-timeout, auto-call next), or management commands —
without going through an HTTP request.

Raises QueueError for domain validation failures.
All mutating functions must be called inside transaction.atomic().
"""
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from clinic.models import Visit
from .models import QueueEntry, QueueStateAudit, transition, VALID_TRANSITIONS


class QueueError(Exception):
    """Raised for domain-level queue violations."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers (also useful externally)
# ---------------------------------------------------------------------------

def move_to_waiting(entry: QueueEntry, changed_by_id, is_late: bool) -> None:
    """
    Assign queue_position and transition entry checked_in → waiting.

    Priority rule:
      - On-time appointment patients insert after the last appointment entry,
        before walk-ins.
      - Late appointments and walk-ins append to the end.

    Must be called inside transaction.atomic().
    """
    clinic_id  = entry.clinic_id
    waiting_qs = QueueEntry.objects.for_clinic(clinic_id).filter(
        status='waiting'
    ).select_for_update()

    if not is_late and entry.entry_type == 'appointment':
        last_appt_pos = waiting_qs.filter(entry_type='appointment').aggregate(
            m=Max('queue_position')
        )['m']
        if last_appt_pos is None:
            if waiting_qs.exists():
                waiting_qs.update(queue_position=F('queue_position') + 1)
            entry.queue_position = 1
        else:
            insert_at = last_appt_pos + 1
            waiting_qs.filter(queue_position__gte=insert_at).update(
                queue_position=F('queue_position') + 1
            )
            entry.queue_position = insert_at
    else:
        max_pos = waiting_qs.aggregate(m=Max('queue_position'))['m'] or 0
        entry.queue_position = max_pos + 1

    transition(
        entry, 'waiting',
        changed_by=changed_by_id,
        reason='auto_queued',
        metadata={'queue_position': entry.queue_position, 'is_late': is_late},
    )


def compact_waiting_positions(clinic_id, after_position: int) -> None:
    """
    Shift all waiting entries with queue_position > after_position down by 1.
    Must be called inside transaction.atomic().
    """
    QueueEntry.objects.for_clinic(clinic_id).filter(
        status='waiting',
        queue_position__gt=after_position,
    ).update(queue_position=F('queue_position') - 1)


# ---------------------------------------------------------------------------
# State transition services
# ---------------------------------------------------------------------------

@transaction.atomic
def call_patient(entry: QueueEntry, called_by_id) -> QueueEntry:
    """
    Transition a waiting entry to 'called' and compact the queue.

    Raises:
        QueueError: if entry is not in 'waiting' status.
    """
    if entry.status != 'waiting':
        raise QueueError(f'Cannot call a patient in {entry.status!r} status.')

    old_position     = entry.queue_position
    entry.queue_position = None
    transition(
        entry, 'called',
        changed_by=called_by_id,
        reason='staff_called',
        metadata={'previous_queue_position': old_position},
    )
    if old_position is not None:
        compact_waiting_positions(entry.clinic_id, old_position)

    return entry


@transaction.atomic
def mark_no_show(entry: QueueEntry, changed_by_id, reason: str) -> QueueEntry:
    """
    Transition a waiting or called entry to 'no_show' and compact the queue.

    Raises:
        QueueError: if entry is not in 'waiting' or 'called' status.
    """
    if entry.status not in ('waiting', 'called'):
        raise QueueError(f'Cannot mark no-show for entry in {entry.status!r} status.')

    old_position         = entry.queue_position
    entry.queue_position = None
    transition(entry, 'no_show', changed_by=changed_by_id, reason=reason)
    if old_position is not None:
        compact_waiting_positions(entry.clinic_id, old_position)

    return entry


@transaction.atomic
def reinsert_patient(entry: QueueEntry, changed_by_id, reason: str) -> QueueEntry:
    """
    Re-add a no_show patient to the end of the waiting queue.

    Raises:
        QueueError: if entry is not in 'no_show' status.
    """
    if entry.status != 'no_show':
        raise QueueError(f'Cannot reinsert an entry in {entry.status!r} status.')

    waiting_qs = QueueEntry.objects.for_clinic(entry.clinic_id).filter(
        status='waiting'
    ).select_for_update()
    max_pos              = waiting_qs.aggregate(m=Max('queue_position'))['m'] or 0
    entry.queue_position = max_pos + 1
    transition(
        entry, 'waiting',
        changed_by=changed_by_id,
        reason=reason,
        metadata={'queue_position': entry.queue_position},
    )

    return entry


@transaction.atomic
def start_visit(entry: QueueEntry, clinic_id, started_by_id) -> tuple:
    """
    Transition a called entry to 'in_progress' and create a Visit record.

    Returns:
        (entry, visit) tuple.

    Raises:
        QueueError: if entry is not in 'called' status.
    """
    if entry.status != 'called':
        raise QueueError(f'Cannot start a visit for entry in {entry.status!r} status.')

    visit = Visit.objects.create(
        clinic_id=clinic_id,
        patient_id=entry.patient_id,
        created_by=started_by_id,
        assigned_doctor_id=entry.assigned_doctor_id or started_by_id,
        status='in_progress',
    )
    entry.visit_id = visit.id
    transition(
        entry, 'in_progress',
        changed_by=started_by_id,
        reason='visit_started',
        metadata={'visit_id': str(visit.id)},
    )

    return entry, visit


@transaction.atomic
def complete_visit(entry: QueueEntry, clinic_id, completed_by_id) -> QueueEntry:
    """
    Transition an in_progress entry to 'completed' and mark the linked visit done.

    Raises:
        QueueError: if entry is not in 'in_progress' status.
    """
    if entry.status != 'in_progress':
        raise QueueError(f'Cannot complete an entry in {entry.status!r} status.')

    transition(entry, 'completed', changed_by=completed_by_id, reason='visit_completed')

    if entry.visit_id:
        Visit.objects.for_clinic(clinic_id).filter(
            id=entry.visit_id
        ).update(status='completed')

    return entry
