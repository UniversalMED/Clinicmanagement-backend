from rest_framework import serializers

from .models import Appointment, QueueEntry, QueueStateAudit


# ---------------------------------------------------------------------------
# Appointment serializers
# ---------------------------------------------------------------------------

class AppointmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = [
            'id', 'clinic_id', 'patient_id', 'doctor_id',
            'scheduled_at', 'duration_minutes', 'type', 'notes',
            'status', 'cancelled_at', 'cancelled_by', 'cancel_reason',
            'created_at',
        ]
        read_only_fields = [
            'id', 'clinic_id', 'status', 'cancelled_at', 'cancelled_by',
            'cancel_reason', 'created_at',
        ]


class CreateAppointmentSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    doctor_id = serializers.UUIDField(required=False, allow_null=True)
    scheduled_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(default=30, min_value=5, max_value=480)
    type = serializers.ChoiceField(choices=['specialist', 'general'])
    notes = serializers.CharField(required=False, allow_blank=True)


class UpdateAppointmentSerializer(serializers.Serializer):
    doctor_id = serializers.UUIDField(required=False, allow_null=True)
    scheduled_at = serializers.DateTimeField(required=False)
    duration_minutes = serializers.IntegerField(required=False, min_value=5, max_value=480)
    notes = serializers.CharField(required=False, allow_blank=True)


class CancelAppointmentSerializer(serializers.Serializer):
    cancel_reason = serializers.CharField(min_length=1)


class AppointmentAffectedSerializer(serializers.Serializer):
    doctor_id = serializers.UUIDField()
    date = serializers.DateField()
    reason = serializers.CharField(min_length=1)


class AppointmentReassignSerializer(serializers.Serializer):
    new_doctor_id = serializers.UUIDField()


# ---------------------------------------------------------------------------
# Queue entry serializers
# ---------------------------------------------------------------------------

class QueueEntrySerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()

    class Meta:
        model = QueueEntry
        fields = [
            'id', 'clinic_id', 'patient_id', 'patient_name',
            'appointment_id', 'visit_id',
            'status', 'queue_position', 'entry_type', 'priority_override',
            'scheduled_at', 'checked_in_at', 'called_at',
            'in_progress_at', 'completed_at', 'no_show_at',
            'grace_period_ends_at', 'call_timeout_at',
            'assigned_doctor_id', 'created_at',
        ]

    def get_patient_name(self, obj):
        from clinic.models import Patient
        try:
            return Patient.objects.for_clinic(obj.clinic_id).get(id=obj.patient_id).full_name
        except Patient.DoesNotExist:
            return None


class CheckInSerializer(serializers.Serializer):
    appointment_id = serializers.UUIDField(required=False)
    patient_id = serializers.UUIDField(required=False)

    def validate(self, data):
        if not data.get('appointment_id') and not data.get('patient_id'):
            raise serializers.ValidationError(
                'Provide either appointment_id (appointment check-in) or patient_id (walk-in).'
            )
        if data.get('appointment_id') and data.get('patient_id'):
            raise serializers.ValidationError(
                'Provide appointment_id OR patient_id, not both.'
            )
        return data


class NoShowSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default='staff_marked_no_show')


class ReinsertSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=1)


class ReorderItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    queue_position = serializers.IntegerField(min_value=1)


class ReorderSerializer(serializers.Serializer):
    positions = ReorderItemSerializer(many=True, min_length=1)

    def validate_positions(self, value):
        pos_values = [item['queue_position'] for item in value]
        if len(set(pos_values)) != len(pos_values):
            raise serializers.ValidationError('Duplicate queue_position values.')
        return value


# ---------------------------------------------------------------------------
# Audit history serializer
# ---------------------------------------------------------------------------

class QueueStateAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = QueueStateAudit
        fields = [
            'id', 'queue_entry_id', 'previous_status', 'new_status',
            'changed_by', 'change_reason', 'metadata', 'created_at',
        ]
