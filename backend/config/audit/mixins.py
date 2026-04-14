"""
AuditLogMixin — call self.log_action(...) in any view after a successful
create or update. Failures are silenced so a broken audit write never
disrupts the primary operation.
"""
import logging

from audit.models import AuditLog

logger = logging.getLogger(__name__)


class AuditLogMixin:
    def log_action(self, request, action: str, entity_type: str, entity_id):
        try:
            AuditLog.objects.create(
                clinic_id=request.user.clinic_id,
                user_id=request.user.id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        except Exception as exc:
            logger.exception(
                "Audit log write failed — action=%s entity_type=%s entity_id=%s: %s",
                action, entity_type, entity_id, exc,
            )
