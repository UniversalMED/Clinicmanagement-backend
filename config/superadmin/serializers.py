from django.utils.text import slugify
from rest_framework import serializers

from .models import Clinic


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Clinic
        fields = ['id', 'name', 'slug', 'is_active', 'created_at']
        read_only_fields = fields


class OnboardClinicSerializer(serializers.Serializer):
    # Clinic fields
    clinic_name = serializers.CharField(max_length=255)

    # Initial admin user fields
    admin_email     = serializers.EmailField()
    admin_password  = serializers.CharField(min_length=8, write_only=True)
    admin_full_name = serializers.CharField(max_length=255)

    def validate_clinic_name(self, value):
        slug = slugify(value)
        if Clinic.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A clinic with this name already exists.")
        if Clinic.objects.filter(slug=slug).exists():
            raise serializers.ValidationError("A clinic with an equivalent slug already exists.")
        return value
