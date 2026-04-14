import uuid

from django.db import models

from core.models import BaseModel
from core.querysets import ClinicScopedManager

NOTIFICATION_EVENT_TYPES = [
    ('lab_test_requested',  'Lab Test Requested'),
    ('lab_test_started',    'Lab Test Started'),
    ('lab_test_completed',  'Lab Test Completed'),
]

NOTIFICATION_STATUS_CHOICES = [
    ('pending',   'Pending'),
    ('delivered', 'Delivered'),
    ('failed',    'Failed'),
]


class Notification(BaseModel):
    objects = ClinicScopedManager()

    clinic_id    = models.UUIDField()
    recipient_id = models.UUIDField()          # Profile.id
    event_type   = models.TextField(choices=NOTIFICATION_EVENT_TYPES)
    entity_type  = models.TextField(default='test_order')
    entity_id    = models.UUIDField()
    payload      = models.JSONField(default=dict)
    status       = models.TextField(choices=NOTIFICATION_STATUS_CHOICES, default='pending')
    retry_count  = models.IntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        managed  = False
        ordering = ['-created_at']
