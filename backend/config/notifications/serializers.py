from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'event_type', 'entity_type', 'entity_id',
            'payload', 'status', 'retry_count', 'created_at', 'delivered_at',
        ]
        read_only_fields = fields
