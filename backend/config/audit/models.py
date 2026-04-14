import uuid
from django.db import models


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic_id = models.UUIDField()
    user_id = models.UUIDField()
    action = models.TextField()       # 'create' | 'update'
    entity_type = models.TextField()  # 'patient' | 'visit' | 'consultation' | ...
    entity_id = models.UUIDField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_logs"
        managed = False
        ordering = ['-timestamp']
