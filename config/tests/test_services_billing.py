"""
Unit tests for billing service layer functions.

Tests call service functions directly — no HTTP — so they are fast and isolated.
Covers: recompute_invoice_totals, finalize_invoice, void_invoice, record_cash_payment.
"""
import uuid
from decimal import Decimal

from django.test import TestCase

from billing.models import Invoice, InvoiceLineItem, Payment
from billing.services import (
    BillingError,
    finalize_invoice,
    generate_qr_code,
    record_cash_payment,
    recompute_invoice_totals,
    void_invoice,
)
from lab.models import TestOrder
from .utils import (
    make_user, make_patient, make_visit,
    make_lab_test, make_test_order, make_invoice, make_line_item,
)


class RecomputeInvoiceTotalsTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        patient           = make_patient(self.clinic_id)
        visit             = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test          = make_lab_test(self.clinic_id, self.admin)
        self.order        = make_test_order(visit, lab_test, self.doctor)
        self.invoice      = make_invoice(self.clinic_id, visit, self.admin)
        make_line_item(self.invoice, self.order, lab_test)

    def test_subtotal_equals_sum_of_line_items(self):
        recompute_invoice_totals(self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.subtotal, Decimal('25.00'))

    def test_total_equals_subtotal_minus_discount(self):
        recompute_invoice_totals(self.invoice, discount_amount=Decimal('5.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total_amount, Decimal('20.00'))
        self.assertEqual(self.invoice.discount_amount, Decimal('5.00'))

    def test_total_never_goes_negative(self):
        recompute_invoice_totals(self.invoice, discount_amount=Decimal('999.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total_amount, Decimal('0.00'))

    def test_preserves_existing_discount_when_none_passed(self):
        self.invoice.discount_amount = Decimal('3.00')
        self.invoice.save()
        recompute_invoice_totals(self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.discount_amount, Decimal('3.00'))
        self.assertEqual(self.invoice.total_amount, Decimal('22.00'))


class FinalizeInvoiceServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        patient           = make_patient(self.clinic_id)
        visit             = make_visit(self.clinic_id, patient, self.receptionist)
        self.lab_test     = make_lab_test(self.clinic_id, self.admin)
        self.order        = make_test_order(visit, self.lab_test, self.doctor)
        self.invoice      = make_invoice(self.clinic_id, visit, self.admin)
        make_line_item(self.invoice, self.order, self.lab_test)

    def test_sets_status_to_finalized(self):
        finalize_invoice(self.invoice, self.admin.id)
        self.assertEqual(self.invoice.status, 'finalized')

    def test_stamps_finalized_by_and_at(self):
        finalize_invoice(self.invoice, self.admin.id)
        self.assertEqual(self.invoice.finalized_by, self.admin.id)
        self.assertIsNotNone(self.invoice.finalized_at)

    def test_computes_totals_correctly(self):
        finalize_invoice(self.invoice, self.admin.id, discount_amount=Decimal('5.00'))
        self.assertEqual(self.invoice.subtotal, Decimal('25.00'))
        self.assertEqual(self.invoice.discount_amount, Decimal('5.00'))
        self.assertEqual(self.invoice.total_amount, Decimal('20.00'))

    def test_stamps_test_order_with_invoice_id(self):
        finalize_invoice(self.invoice, self.admin.id)
        self.order.refresh_from_db()
        self.assertEqual(self.order.billed_invoice_id, self.invoice.id)

    def test_raises_if_not_draft(self):
        self.invoice.status = 'finalized'
        self.invoice.save()
        with self.assertRaises(BillingError) as ctx:
            finalize_invoice(self.invoice, self.admin.id)
        self.assertIn('draft', str(ctx.exception).lower())

    def test_raises_if_no_line_items(self):
        empty_invoice = make_invoice(
            self.clinic_id,
            make_visit(self.clinic_id, make_patient(self.clinic_id), self.receptionist),
            self.admin,
        )
        with self.assertRaises(BillingError) as ctx:
            finalize_invoice(empty_invoice, self.admin.id)
        self.assertIn('line item', str(ctx.exception).lower())

    def test_raises_on_double_billing(self):
        finalize_invoice(self.invoice, self.admin.id)
        second_invoice = make_invoice(
            self.clinic_id,
            make_visit(self.clinic_id, make_patient(self.clinic_id), self.receptionist),
            self.admin,
        )
        make_line_item(second_invoice, self.order, self.lab_test)
        with self.assertRaises(BillingError) as ctx:
            finalize_invoice(second_invoice, self.admin.id)
        self.assertIn('already billed', str(ctx.exception).lower())

    def test_notes_override(self):
        finalize_invoice(self.invoice, self.admin.id, notes='Special case')
        self.assertEqual(self.invoice.notes, 'Special case')

    def test_notes_not_overridden_when_none(self):
        self.invoice.notes = 'Original'
        self.invoice.save()
        finalize_invoice(self.invoice, self.admin.id, notes=None)
        self.assertEqual(self.invoice.notes, 'Original')


class VoidInvoiceServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        patient           = make_patient(self.clinic_id)
        visit             = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test          = make_lab_test(self.clinic_id, self.admin)
        self.order        = make_test_order(visit, lab_test, self.doctor)
        self.invoice      = make_invoice(self.clinic_id, visit, self.admin)
        make_line_item(self.invoice, self.order, lab_test)
        finalize_invoice(self.invoice, self.admin.id)

    def test_sets_status_to_void(self):
        void_invoice(self.invoice, self.admin.id, 'Duplicate charge')
        self.assertEqual(self.invoice.status, 'void')

    def test_stamps_voided_by_reason_and_at(self):
        void_invoice(self.invoice, self.admin.id, 'Duplicate charge')
        self.assertEqual(self.invoice.voided_by, self.admin.id)
        self.assertEqual(self.invoice.void_reason, 'Duplicate charge')
        self.assertIsNotNone(self.invoice.voided_at)

    def test_releases_test_order(self):
        void_invoice(self.invoice, self.admin.id, 'Test')
        self.order.refresh_from_db()
        self.assertIsNone(self.order.billed_invoice_id)

    def test_raises_if_not_finalized(self):
        draft = make_invoice(
            self.clinic_id,
            make_visit(self.clinic_id, make_patient(self.clinic_id), self.receptionist),
            self.admin,
        )
        with self.assertRaises(BillingError) as ctx:
            void_invoice(draft, self.admin.id, 'Test')
        self.assertIn('finalized', str(ctx.exception).lower())


class RecordCashPaymentServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        patient           = make_patient(self.clinic_id)
        visit             = make_visit(self.clinic_id, patient, self.receptionist)
        lab_test          = make_lab_test(self.clinic_id, self.admin)
        order             = make_test_order(visit, lab_test, self.doctor)
        self.invoice      = make_invoice(self.clinic_id, visit, self.admin)
        make_line_item(self.invoice, order, lab_test)
        finalize_invoice(self.invoice, self.admin.id)

    def test_creates_payment_with_success_status(self):
        payment = record_cash_payment(self.invoice, self.receptionist.id)
        self.assertEqual(payment.status, 'success')

    def test_mode_is_cash(self):
        payment = record_cash_payment(self.invoice, self.receptionist.id)
        self.assertEqual(payment.mode, 'cash')

    def test_paid_at_is_set(self):
        payment = record_cash_payment(self.invoice, self.receptionist.id)
        self.assertIsNotNone(payment.paid_at)

    def test_tx_ref_starts_with_cash(self):
        payment = record_cash_payment(self.invoice, self.receptionist.id)
        self.assertTrue(payment.tx_ref.startswith('cash-'))

    def test_amount_matches_invoice_total(self):
        payment = record_cash_payment(self.invoice, self.receptionist.id)
        self.assertEqual(payment.amount, self.invoice.total_amount)

    def test_raises_if_not_finalized(self):
        draft = make_invoice(
            self.clinic_id,
            make_visit(self.clinic_id, make_patient(self.clinic_id), self.receptionist),
            self.admin,
        )
        with self.assertRaises(BillingError):
            record_cash_payment(draft, self.receptionist.id)

    def test_raises_if_already_paid(self):
        record_cash_payment(self.invoice, self.receptionist.id)
        with self.assertRaises(BillingError) as ctx:
            record_cash_payment(self.invoice, self.receptionist.id)
        self.assertIn('already been paid', str(ctx.exception).lower())


class GenerateQRCodeTests(TestCase):

    def test_returns_data_uri(self):
        result = generate_qr_code('https://checkout.chapa.co/test')
        self.assertTrue(result.startswith('data:image/png;base64,'))

    def test_different_urls_produce_different_qr_codes(self):
        qr1 = generate_qr_code('https://checkout.chapa.co/abc')
        qr2 = generate_qr_code('https://checkout.chapa.co/xyz')
        self.assertNotEqual(qr1, qr2)

    def test_output_is_valid_base64(self):
        import base64
        result = generate_qr_code('https://example.com')
        prefix = 'data:image/png;base64,'
        encoded = result[len(prefix):]
        decoded = base64.b64decode(encoded)
        # PNG files start with the PNG magic bytes
        self.assertTrue(decoded[:8] == b'\x89PNG\r\n\x1a\n')
