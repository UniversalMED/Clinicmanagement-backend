from rest_framework import serializers
from .models import Patient, Visit, Consultation, Prescription, PrescriptionItem


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ['id', 'clinic_id', 'full_name', 'gender', 'date_of_birth', 'phone', 'created_at']
        read_only_fields = ['id', 'clinic_id', 'created_at']


class VisitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Visit
        fields = ['id', 'clinic_id', 'patient_id', 'created_by', 'assigned_doctor_id', 'status', 'created_at']
        read_only_fields = ['id', 'clinic_id', 'created_by', 'created_at']


class ConsultationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Consultation
        fields = ['id', 'visit_id', 'doctor_id', 'symptoms', 'diagnosis', 'notes', 'created_at']
        read_only_fields = ['id', 'doctor_id', 'created_at']


class PrescriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrescriptionItem
        fields = ['id', 'medication', 'dosage', 'frequency', 'duration', 'instructions', 'created_at']
        read_only_fields = ['id', 'created_at']


class PrescriptionSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Prescription
        fields = ['id', 'consultation_id', 'prescribed_by', 'notes', 'items', 'created_at']
        read_only_fields = ['id', 'prescribed_by', 'items', 'created_at']

    def get_items(self, obj):
        return PrescriptionItemSerializer(
            PrescriptionItem.objects.filter(prescription_id=obj.id),
            many=True,
        ).data


class PrescriptionCreateSerializer(serializers.Serializer):
    consultation_id = serializers.UUIDField()
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    items = PrescriptionItemSerializer(many=True, min_length=1)
