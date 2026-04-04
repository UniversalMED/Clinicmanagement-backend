import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.models import BaseModel
from core.querysets import ClinicScopedManager

# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

QUEUE_STATUS_CHOICES = [
    ('scheduled', 'Scheduled'),
    ('checked_in', 'Checked In'),
    ('waiting', 'Waiting'),
    ('called', 'Called'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
    ('no_show', 'No Show'),
]

APPOINTMENT_TYPE_CHOICES = [
    ('specialist', 'Specialist'),
    ('general', 'General'),
]

APPOINTMENT_STATUS_CHOICES = [
    ('active', 'Active'),
    ('cancelled', 'Cancelled'),
    ('rescheduled', 'Rescheduled'),
    ('affected', 'Affected'),
]

ENTRY_TYPE_CHOICES = [
    ('appointment', 'Appointment'),
    ('walk_in', 'Walk-in'),
]

# ---------------------------------------------------------------------------
# State machine — single source of truth for valid transitions
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, set[str]] = {
    'scheduled':   {'checked_in', 'no_show'},
    'checked_in':  {'waiting'},
    'waiting':     {'called', 'no_show'},
    'called':      {'in_progress', 'waiting', 'no_show'},
    'in_progress': {'completed'},
    'completed':   set(),      # terminal
    'no_show':     {'waiting'},  # reinsert only
}

TERMINAL_STATUSES = {'completed', 'no_show'}

_TIMESTAMP_FIELDS = {
    'checked_in': 'checked_in_at',
    'called': 'called_at',
    'in_progress': 'in_progress_at',
    'completed': 'completed_at',
    'no_show': 'no_show_at',
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Appointment(BaseModel):
    objects = ClinicScopedManager()

    clinic_id = models.UUIDField()
    patient_id = models.UUIDField()
    doctor_id = models.UUIDField(null=True, blank=True)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.IntegerField(default=30)
    type = models.TextField(choices=APPOINTMENT_TYPE_CHOICES)
    notes = models.TextField(null=True, blank=True)
    status = models.TextField(choices=APPOINTMENT_STATUS_CHOICES, default='active')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.UUIDField(null=True, blank=True)
    cancel_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'appointments'
        managed = False
        ordering = ['scheduled_at']


class QueueEntry(BaseModel):
    objects = ClinicScopedManager()

    clinic_id = models.UUIDField()
    patient_id = models.UUIDField()
    appointment_id = models.UUIDField(null=True, blank=True)
    visit_id = models.UUIDField(null=True, blank=True)

    status = models.TextField(choices=QUEUE_STATUS_CHOICES, default='checked_in')
    queue_position = models.IntegerField(null=True, blank=True)
    entry_type = models.TextField(choices=ENTRY_TYPE_CHOICES)
    priority_override = models.IntegerField(default=0)

    # Per-transition timestamps
    scheduled_at = models.DateTimeField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    called_at = models.DateTimeField(null=True, blank=True)
    in_progress_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    no_show_at = models.DateTimeField(null=True, blank=True)

    # Time-based rule helpers
    grace_period_ends_at = models.DateTimeField(null=True, blank=True)
    call_timeout_at = models.DateTimeField(null=True, blank=True)

    assigned_doctor_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = 'queue_entries'
        managed = False
        ordering = ['queue_position', 'created_at']


class QueueStateAudit(BaseModel):
    """Full history of every state change — system or staff."""

    queue_entry_id = models.UUIDField()
    clinic_id = models.UUIDField()
    patient_id = models.UUIDField()
    previous_status = models.TextField(null=True, blank=True)
    new_status = models.TextField()
    changed_by = models.UUIDField(null=True, blank=True)  # NULL = system action
    change_reason = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'queue_state_audit'
        managed = False
        ordering = ['-created_at']


# ---------------------------------------------------------------------------
# State machine helper
# ---------------------------------------------------------------------------

def transition(entry: QueueEntry, new_status: str, changed_by=None,
               reason: str = None, metadata: dict = None) -> None:
    """
    Validate and apply a state transition on a QueueEntry.

    - Updates entry.status and the corresponding timestamp field.
    - For 'called' transitions: stamps call_timeout_at.
    - Saves the entry.
    - Writes a QueueStateAudit record.

    Caller must wrap in transaction.atomic() when other DB writes must be atomic
    with this transition (e.g. creating a Visit, compacting positions).

    Raises ValidationError on invalid transition.
    """
    allowed = VALID_TRANSITIONS.get(entry.status, set())
    if new_status not in allowed:
        raise ValidationError(
            {'detail': f"Transition '{entry.status}' → '{new_status}' is not allowed."}
        )

    prev_status = entry.status
    now = timezone.now()
    entry.status = new_status

    ts_field = _TIMESTAMP_FIELDS.get(new_status)
    if ts_field:
        setattr(entry, ts_field, now)

    if new_status == 'called':
        timeout_mins = getattr(settings, 'QUEUE_CALL_TIMEOUT_MINUTES', 5)
        entry.call_timeout_at = now + timedelta(minutes=timeout_mins)

    entry.save()

    QueueStateAudit.objects.create(
        queue_entry_id=entry.id,
        clinic_id=entry.clinic_id,
        patient_id=entry.patient_id,
        previous_status=prev_status,
        new_status=new_status,
        changed_by=changed_by,
        change_reason=reason,
        metadata=metadata or {},
    )
