"""
Billing service layer.

All business logic lives here so it can be called from views, Celery tasks,
webhooks (e.g. Chapa payment confirmation), or management commands — without
going through an HTTP request.

Raises BillingError for domain validation failures.
All mutating functions are atomic.
"""
import base64
import io
import uuid
import logging

import qrcode

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Invoice, InvoiceLineItem, Payment
from lab.models import TestOrder
from .chapa import ChapaClient, ChapaError

logger = logging.getLogger(__name__)


class BillingError(Exception):
    """Raised for domain-level billing violations."""
    pass


def recompute_invoice_totals(invoice, discount_amount=None):
    """
    Recalculate subtotal and total_amount from current line items and save.
    Called after adding or removing a line item.
    """
    subtotal = (
        InvoiceLineItem.objects.filter(invoice_id=invoice.id)
        .aggregate(total=Sum('subtotal'))['total'] or 0
    )
    if discount_amount is None:
        discount_amount = invoice.discount_amount
    total = max(subtotal - discount_amount, 0)
    invoice.subtotal        = subtotal
    invoice.discount_amount = discount_amount
    invoice.total_amount    = total
    invoice.save(update_fields=['subtotal', 'discount_amount', 'total_amount'])


@transaction.atomic
def finalize_invoice(invoice, finalized_by_id, discount_amount=0, notes=None):
    """
    Lock a draft invoice:
      1. Validate status and line items.
      2. Guard against double-billing.
      3. Compute totals.
      4. Stamp each billed test order with billed_invoice_id.
      5. Set status to 'finalized'.

    Args:
        invoice:         Invoice instance (must be in 'draft' status).
        finalized_by_id: UUID of the user finalizing the invoice.
        discount_amount: Decimal discount to apply (default 0).
        notes:           Optional notes override.

    Returns:
        The updated Invoice instance.

    Raises:
        BillingError: on any domain validation failure.
    """
    if invoice.status != 'draft':
        raise BillingError('Only draft invoices can be finalized.')

    line_items = list(InvoiceLineItem.objects.filter(invoice_id=invoice.id))
    if not line_items:
        raise BillingError('Invoice must have at least one line item before finalization.')

    order_ids = [li.test_order_id for li in line_items if li.test_order_id]
    if order_ids:
        conflicting = list(
            TestOrder.objects.filter(id__in=order_ids)
            .exclude(billed_invoice_id__isnull=True)
            .values_list('id', flat=True)
        )
        if conflicting:
            raise BillingError(
                f'One or more test orders are already billed on another invoice: '
                f'{[str(oid) for oid in conflicting]}'
            )

    subtotal     = sum(li.subtotal for li in line_items)
    total_amount = max(subtotal - discount_amount, 0)

    invoice.status          = 'finalized'
    invoice.subtotal        = subtotal
    invoice.discount_amount = discount_amount
    invoice.total_amount    = total_amount
    invoice.finalized_by    = finalized_by_id
    invoice.finalized_at    = timezone.now()
    if notes is not None:
        invoice.notes = notes
    invoice.save()

    if order_ids:
        TestOrder.objects.filter(id__in=order_ids).update(billed_invoice_id=invoice.id)

    return invoice


# ---------------------------------------------------------------------------
# Chapa payment services
# ---------------------------------------------------------------------------

def _get_chapa_client() -> ChapaClient:
    secret_key = getattr(settings, 'CHAPA_SECRET_KEY', '')
    if not secret_key:
        raise BillingError('CHAPA_SECRET_KEY is not configured.')
    return ChapaClient(secret_key)


def initiate_payment(invoice, initiated_by_id, callback_url: str = '', return_url: str = '') -> tuple:
    """
    Initialize a Chapa payment for a finalized invoice.

    Flow:
      1. Validate invoice is finalized with no pending/successful payment.
      2. Generate a unique tx_ref.
      3. Create a Payment row in 'pending' status.
      4. Call Chapa initialize API.
      5. Return (payment, checkout_url).

    Raises:
        BillingError: on domain validation or Chapa errors.
    """
    if invoice.status != 'finalized':
        raise BillingError('Only finalized invoices can be paid.')

    existing = Payment.objects.for_clinic(invoice.clinic_id).filter(
        invoice_id=invoice.id,
        status__in=['pending', 'success'],
    ).first()
    if existing:
        if existing.status == 'success':
            raise BillingError('This invoice has already been paid.')
        raise BillingError('A payment is already pending for this invoice.')

    tx_ref = f'clinic-{invoice.id.hex}-{uuid.uuid4().hex[:4]}'

    payment = Payment.objects.create(
        clinic_id=invoice.clinic_id,
        invoice_id=invoice.id,
        initiated_by=initiated_by_id,
        tx_ref=tx_ref,
        amount=invoice.total_amount,
        currency='ETB',
        status='pending',
    )

    try:
        client = _get_chapa_client()
        checkout_url = client.initialize(
            tx_ref=tx_ref,
            amount=str(invoice.total_amount),
            callback_url=callback_url,
            return_url=return_url,
            description=f'Invoice {invoice.id}',
        )
    except (ChapaError, BillingError) as exc:
        payment.status = 'failed'
        payment.save(update_fields=['status'])
        raise BillingError(str(exc))

    return payment, checkout_url


def process_chapa_webhook(payload: dict) -> Payment | None:
    """
    Handle an incoming Chapa webhook payload.

    Flow:
      1. Only process 'charge.success' events.
      2. Find the Payment by tx_ref.
      3. Idempotent — skip if already 'success'.
      4. Call Chapa verify endpoint to confirm.
      5. Update payment status and paid_at.

    Returns:
        Updated Payment instance, or None if event is not actionable.

    Raises:
        BillingError: if verification fails or payment not found.
    """
    event  = payload.get('event')
    tx_ref = payload.get('tx_ref')

    if event != 'charge.success':
        return None

    try:
        payment = Payment.objects.get_queryset().get(tx_ref=tx_ref)
    except Payment.DoesNotExist:
        raise BillingError(f'No payment found for tx_ref: {tx_ref}')

    if payment.status == 'success':
        return payment  # idempotent

    # Always re-verify with Chapa before granting value
    try:
        client = _get_chapa_client()
        verification = client.verify(tx_ref)
    except ChapaError as exc:
        logger.error('Chapa verification failed for tx_ref=%s: %s', tx_ref, exc)
        raise BillingError(f'Chapa verification failed: {exc}')

    verified_status = verification.get('status')
    if verified_status != 'success':
        payment.status = 'failed'
        payment.save(update_fields=['status'])
        return payment

    with transaction.atomic():
        payment.status    = 'success'
        payment.chapa_ref = verification.get('reference', '')
        payment.mode      = verification.get('mode', 'test')
        payment.paid_at   = timezone.now()
        payment.save(update_fields=['status', 'chapa_ref', 'mode', 'paid_at'])

        _release_test_orders(payment.invoice_id)

    return payment
def generate_qr_code(url: str) -> str:
    """
    Generate a QR code PNG for *url* and return it as a base64-encoded string
    suitable for embedding directly in a JSON response:
        "data:image/png;base64,<encoded>"
    The frontend can render it with:  <img src="...qr_code value..." />
    """
    img = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f'data:image/png;base64,{encoded}'


@transaction.atomic
def record_cash_payment(invoice, received_by_id) -> Payment:
    """
    Record an immediate cash payment for a finalized invoice.

    - No Chapa involved.
    - Payment is created and immediately marked 'success'.
    - Guards against paying an already-paid invoice.

    Args:
        invoice:         Invoice instance (must be 'finalized').
        received_by_id:  UUID of the staff member who collected the cash.

    Returns:
        The created Payment instance.

    Raises:
        BillingError: if invoice is not finalized or already paid.
    """
    if invoice.status != 'finalized':
        raise BillingError('Only finalized invoices can be paid.')

    existing = Payment.objects.for_clinic(invoice.clinic_id).filter(
        invoice_id=invoice.id,
        status='success',
    ).first()
    if existing:
        raise BillingError('This invoice has already been paid.')

    payment = Payment.objects.create(
        clinic_id=invoice.clinic_id,
        invoice_id=invoice.id,
        initiated_by=received_by_id,
        tx_ref=f'cash-{invoice.id}-{uuid.uuid4().hex[:8]}',
        amount=invoice.total_amount,
        currency='ETB',
        status='success',
        mode='cash',
        paid_at=timezone.now(),
    )
    _release_test_orders(invoice.id)
    return payment


def _release_test_orders(invoice_id):
    """
    Flip all test orders on a paid invoice from 'awaiting_payment' → 'pending'
    so lab techs can see and process them.
    Called after both cash and Chapa payment success.
    """
    order_ids = list(
        InvoiceLineItem.objects.filter(invoice_id=invoice_id)
        .exclude(test_order_id__isnull=True)
        .values_list('test_order_id', flat=True)
    )
    if order_ids:
        TestOrder.objects.filter(
            id__in=order_ids, status='awaiting_payment'
        ).update(status='pending')


@transaction.atomic
def void_invoice(invoice, voided_by_id, void_reason):
    """
    Void a finalized invoice and release all stamped test orders for re-billing.

    Args:
        invoice:      Invoice instance (must be in 'finalized' status).
        voided_by_id: UUID of the user voiding the invoice.
        void_reason:  Required reason string.

    Returns:
        The updated Invoice instance.

    Raises:
        BillingError: if invoice is not finalized.
    """
    if invoice.status != 'finalized':
        raise BillingError('Only finalized invoices can be voided.')

    order_ids = list(
        InvoiceLineItem.objects.filter(invoice_id=invoice.id)
        .exclude(test_order_id__isnull=True)
        .values_list('test_order_id', flat=True)
    )

    if order_ids:
        TestOrder.objects.filter(
            id__in=order_ids, billed_invoice_id=invoice.id
        ).update(billed_invoice_id=None, status='awaiting_payment')

    invoice.status      = 'void'
    invoice.voided_by   = voided_by_id
    invoice.voided_at   = timezone.now()
    invoice.void_reason = void_reason
    invoice.save()

    return invoice

