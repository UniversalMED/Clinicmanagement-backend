import uuid

from django.db import models


class Clinic(models.Model):
    """
    Master record for a clinic tenant.
    The id here IS the clinic_id stamped on every JWT and every other model.
    Before this model existed, clinic_id was just a bare UUID from the token.
    Now it references a real row so super admin can manage the tenant list.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.TextField(unique=True)
    slug       = models.SlugField(max_length=100, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'clinics'
        managed  = False
        ordering = ['name']

    def __str__(self):
        return self.name
