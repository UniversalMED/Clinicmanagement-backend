from django.db import models

from core.querysets import ClinicScopedManager

ROLE_CHOICES = [
    ('receptionist', 'Receptionist'),
    ('doctor', 'Doctor'),
    ('lab_tech', 'Lab Tech'),
    ('admin', 'Admin'),
]


class Profile(models.Model):
    objects = ClinicScopedManager()

    id = models.UUIDField(primary_key=True)  # mirrors auth.users(id) in Supabase
    clinic_id = models.UUIDField()
    full_name = models.TextField(blank=True, null=True)
    role = models.TextField(choices=ROLE_CHOICES, null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "profiles"
        managed = False  # table is owned by Supabase; Django never runs migrations on it

    # Required by DRF permission system without extending AbstractBaseUser
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False
