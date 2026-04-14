from rest_framework import serializers
from .models import Profile, ROLE_CHOICES


class CreateUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)
    full_name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLE_CHOICES)


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['id', 'clinic_id', 'full_name', 'role', 'created_at']
        read_only_fields = ['id', 'clinic_id', 'created_at']


class AssignRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ROLE_CHOICES)


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['full_name']

    def validate_full_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("full_name cannot be blank.")
        return value.strip()
