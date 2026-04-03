from django.db import models
from core.models import BaseModel
from core.querysets import ClinicScopedManager

GENDER_CHOICES = [
    ('male', 'Male'),
    ('female', 'Female'),
]

VISIT_STATUS_CHOICES = [
    ('open', 'Open'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
]


class Patient(BaseModel):
    objects = ClinicScopedManager()

    clinic_id = models.UUIDField()
    full_name = models.TextField()
    gender = models.TextField(choices=GENDER_CHOICES, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "patients"
        managed = False


class Visit(BaseModel):
    objects = ClinicScopedManager()

    clinic_id = models.UUIDField()
    patient_id = models.UUIDField()
    created_by = models.UUIDField()
    assigned_doctor_id = models.UUIDField(null=True, blank=True)
    status = models.TextField(choices=VISIT_STATUS_CHOICES, default="open")

    class Meta:
        db_table = "visits"
        managed = False


class Consultation(BaseModel):
    visit_id = models.UUIDField()
    doctor_id = models.UUIDField()
    symptoms = models.TextField(blank=True, null=True)
    diagnosis = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "consultations"
        managed = False
