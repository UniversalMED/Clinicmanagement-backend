from django.db import models
from core.models import BaseModel
from core.querysets import ClinicScopedManager

TEST_ORDER_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('in_progress', 'In Progress'),
    ('completed', 'Completed'),
]


class LabTest(BaseModel):
    objects = ClinicScopedManager()

    clinic_id = models.UUIDField()
    name = models.TextField()
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.UUIDField(null=True)

    class Meta:
        db_table = "lab_tests"
        managed = False


class TestOrder(BaseModel):
    visit_id = models.UUIDField()
    consultation_id = models.UUIDField(null=True, blank=True)
    test_id = models.UUIDField()
    ordered_by = models.UUIDField()
    assigned_to = models.UUIDField(null=True, blank=True)
    status = models.TextField(choices=TEST_ORDER_STATUS_CHOICES, default="pending")

    class Meta:
        db_table = "test_orders"
        managed = False


class TestResult(BaseModel):
    test_order_id = models.UUIDField()
    technician_id = models.UUIDField()
    result_data = models.JSONField()
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "test_results"
        managed = False
