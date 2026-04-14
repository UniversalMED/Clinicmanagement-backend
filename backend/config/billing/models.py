from django.db import models
from core.models import BaseModel
from core.querysets import ClinicScopedManager

INVOICE_STATUS_CHOICES = [
    ('draft',     'Draft'),
    ('finalized', 'Finalized'),
    ('void',      'Void'),
]


class Invoice(BaseModel):
    objects = ClinicScopedManager()

    clinic_id       = models.UUIDField()
    visit_id        = models.UUIDField()
    patient_id      = models.UUIDField()          # denormalized from visit for fast queries
    issued_by       = models.UUIDField()
    finalized_by    = models.UUIDField(null=True, blank=True)
    voided_by       = models.UUIDField(null=True, blank=True)
    status          = models.TextField(choices=INVOICE_STATUS_CHOICES, default='draft')
    subtotal        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes           = models.TextField(blank=True, null=True)
    finalized_at    = models.DateTimeField(null=True, blank=True)
    voided_at       = models.DateTimeField(null=True, blank=True)
    void_reason     = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "invoices"
        managed = False
        ordering = ['-created_at']


class InvoiceLineItem(BaseModel):
    invoice_id    = models.UUIDField()
    test_order_id = models.UUIDField(null=True, blank=True)  # audit ref — not a live price source
    test_name     = models.TextField()                        # snapshot
    unit_price    = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot
    quantity      = models.PositiveIntegerField(default=1)
    subtotal      = models.DecimalField(max_digits=10, decimal_places=2)  # stored = unit_price * quantity
    notes         = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "invoice_line_items"
        managed = False


PAYMENT_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('success', 'Success'),
    ('failed',  'Failed'),
]


class Payment(BaseModel):
    objects = ClinicScopedManager()

    clinic_id   = models.UUIDField()
    invoice_id  = models.UUIDField()
    initiated_by = models.UUIDField()
    tx_ref      = models.TextField(unique=True)   # our reference sent to Chapa
    chapa_ref   = models.TextField(null=True, blank=True)  # Chapa's reference
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    currency    = models.TextField(default='ETB')
    status      = models.TextField(choices=PAYMENT_STATUS_CHOICES, default='pending')
    mode        = models.TextField(default='test')   # 'test' | 'live'
    paid_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payments'
        managed  = False
        ordering = ['-created_at']
