from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ['id', 'clinic_id', 'user_id', 'action', 'entity_type', 'entity_id', 'timestamp']
        read_only_fields = fields
