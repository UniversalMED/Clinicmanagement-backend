from rest_framework.views import APIView

from .models import AuditLog
from .serializers import AuditLogSerializer
from users.permissions import HasPermission
from core.querysets import PaginatedListMixin


class AuditLogListView(PaginatedListMixin, APIView):
    """
    GET /api/audit/logs/  — admin only, scoped to clinic.

    Query params:
      ?entity_type=<type>   filter by entity type (patient, visit, ...)
      ?action=<action>      filter by action (create, update)
      ?entity_id=<uuid>     filter by a specific entity
    """
    permission_classes = [HasPermission.for_permission('view_audit_log')]

    def get(self, request):
        qs = AuditLog.objects.filter(clinic_id=request.user.clinic_id)

        entity_type = request.query_params.get('entity_type')
        action = request.query_params.get('action')
        entity_id = request.query_params.get('entity_id')

        if entity_type:
            qs = qs.filter(entity_type=entity_type)
        if action:
            qs = qs.filter(action=action)
        if entity_id:
            qs = qs.filter(entity_id=entity_id)

        return self.paginate(qs, AuditLogSerializer, request)
