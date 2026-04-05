"""
Billing payment integration tests — Chapa payment flow.

Covers:
  - Pay endpoint: RBAC, invoice status guards, duplicate payment prevention
  - Payment list endpoint: clinic scoping, correct invoice filtering
  - Chapa webhook: signature verification, charge.success processing,
    idempotency, bad-event skipping, verification failure
  - Service-layer edge cases: already-paid, pending duplicate
"""
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from billing.models import Invoice, Payment
from billing.services import BillingError, initiate_payment, process_chapa_webhook
from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_lab_test, make_test_order, make_invoice, make_line_item,
)


def _finalize(invoice, receptionist):
    auth_client(receptionist).post(
        f'/api/billing/invoices/{invoice.id}/finalize/',
        {'discount_amount': '0.00'},
        format='json',
    )
    invoice.refresh_from_db()


def _make_finalized_invoice(clinic_id, admin, receptionist, doctor):
    patient   = make_patient(clinic_id)
    visit     = make_visit(clinic_id, patient, receptionist)
    lab_test  = make_lab_test(clinic_id, admin)
    order     = make_test_order(visit, lab_test, doctor, status='completed')
    invoice   = make_invoice(clinic_id, visit, admin)
    make_line_item(invoice, order, lab_test)
    _finalize(invoice, receptionist)
    return invoice


# ---------------------------------------------------------------------------
# POST /api/billing/invoices/<id>/pay/
# ---------------------------------------------------------------------------

@override_settings(CHAPA_SECRET_KEY='test-chapa-key')
class InvoicePayViewTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        self.invoice      = _make_finalized_invoice(
            self.clinic_id, self.admin, self.receptionist, self.doctor
        )

    @patch('billing.services.ChapaClient')
    def test_receptionist_can_initiate_payment(self, MockClient):
        MockClient.return_value.initialize.return_value = 'https://checkout.chapa.co/test'
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {'callback_url': '', 'return_url': ''},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn('checkout_url', resp.data)
        self.assertIn('payment', resp.data)
        self.assertEqual(resp.data['checkout_url'], 'https://checkout.chapa.co/test')
        self.assertEqual(resp.data['payment']['status'], 'pending')

    @patch('billing.services.ChapaClient')
    def test_payment_amount_matches_invoice_total(self, MockClient):
        MockClient.return_value.initialize.return_value = 'https://checkout.chapa.co/test'
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Decimal(resp.data['payment']['amount']), self.invoice.total_amount)

    def test_doctor_cannot_initiate_payment(self):
        resp = auth_client(self.doctor).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    @patch('billing.services.ChapaClient')
    def test_cannot_pay_draft_invoice(self, MockClient):
        patient  = make_patient(self.clinic_id)
        visit    = make_visit(self.clinic_id, patient, self.receptionist)
        invoice  = make_invoice(self.clinic_id, visit, self.admin)
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('detail', resp.data)

    @patch('billing.services.ChapaClient')
    def test_cannot_pay_same_invoice_twice_pending(self, MockClient):
        MockClient.return_value.initialize.return_value = 'https://checkout.chapa.co/test'
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('pending', resp.data['detail'].lower())

    @patch('billing.services.ChapaClient')
    def test_chapa_error_marks_payment_failed_and_returns_400(self, MockClient):
        from billing.chapa import ChapaError
        MockClient.return_value.initialize.side_effect = ChapaError('Network timeout')
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        payment = Payment.objects.for_clinic(self.clinic_id).filter(
            invoice_id=self.invoice.id
        ).first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, 'failed')

    @patch('billing.services.ChapaClient')
    def test_tx_ref_format(self, MockClient):
        MockClient.return_value.initialize.return_value = 'https://checkout.chapa.co/test'
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        tx_ref = resp.data['payment']['tx_ref']
        self.assertTrue(tx_ref.startswith('clinic-'))

    def test_invoice_from_other_clinic_returns_404(self):
        other_clinic_id = uuid.uuid4()
        other_admin      = make_user(other_clinic_id, 'admin')
        other_recep      = make_user(other_clinic_id, 'receptionist')
        other_doctor     = make_user(other_clinic_id, 'doctor')
        other_invoice    = _make_finalized_invoice(
            other_clinic_id, other_admin, other_recep, other_doctor
        )
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{other_invoice.id}/pay/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# GET /api/billing/invoices/<id>/payments/
# ---------------------------------------------------------------------------

class PaymentListTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        self.invoice      = _make_finalized_invoice(
            self.clinic_id, self.admin, self.receptionist, self.doctor
        )

    def _create_payment(self, invoice, status='pending'):
        return Payment.objects.create(
            clinic_id=invoice.clinic_id,
            invoice_id=invoice.id,
            initiated_by=self.admin.id,
            tx_ref=f'clinic-{invoice.id}-{uuid.uuid4().hex[:8]}',
            amount=invoice.total_amount,
            currency='ETB',
            status=status,
        )

    def test_list_payments_for_invoice(self):
        self._create_payment(self.invoice)
        resp = auth_client(self.receptionist).get(
            f'/api/billing/invoices/{self.invoice.id}/payments/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_only_returns_payments_for_target_invoice(self):
        other_invoice = _make_finalized_invoice(
            self.clinic_id, self.admin, self.receptionist, self.doctor
        )
        self._create_payment(self.invoice)
        self._create_payment(other_invoice)

        resp = auth_client(self.receptionist).get(
            f'/api/billing/invoices/{self.invoice.id}/payments/'
        )
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(
            str(resp.data['results'][0]['invoice_id']),
            str(self.invoice.id),
        )

    def test_clinic_scoping(self):
        other_clinic_id = uuid.uuid4()
        other_admin      = make_user(other_clinic_id, 'admin')
        other_recep      = make_user(other_clinic_id, 'receptionist')
        other_doctor     = make_user(other_clinic_id, 'doctor')
        other_invoice    = _make_finalized_invoice(
            other_clinic_id, other_admin, other_recep, other_doctor
        )
        resp = auth_client(self.receptionist).get(
            f'/api/billing/invoices/{other_invoice.id}/payments/'
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiple_payment_attempts_listed(self):
        self._create_payment(self.invoice, status='failed')
        self._create_payment(self.invoice, status='pending')
        resp = auth_client(self.receptionist).get(
            f'/api/billing/invoices/{self.invoice.id}/payments/'
        )
        self.assertEqual(len(resp.data['results']), 2)


# ---------------------------------------------------------------------------
# POST /api/billing/webhook/chapa/
# ---------------------------------------------------------------------------

@override_settings(
    CHAPA_WEBHOOK_SECRET='test-webhook-secret',
    CHAPA_SECRET_KEY='test-chapa-key',
)
class ChapaWebhookTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        self.invoice      = _make_finalized_invoice(
            self.clinic_id, self.admin, self.receptionist, self.doctor
        )
        self.tx_ref = f'clinic-{self.invoice.id}-abc12345'
        self.payment = Payment.objects.create(
            clinic_id=self.invoice.clinic_id,
            invoice_id=self.invoice.id,
            initiated_by=self.admin.id,
            tx_ref=self.tx_ref,
            amount=self.invoice.total_amount,
            currency='ETB',
            status='pending',
        )

    def _post_webhook(self, payload, secret='test-webhook-secret'):
        body = json.dumps(payload).encode('utf-8')
        sig = hmac.new(
            secret.encode('utf-8'), body, hashlib.sha256
        ).hexdigest()
        from rest_framework.test import APIClient
        client = APIClient()
        return client.post(
            '/api/billing/webhook/chapa/',
            data=body,
            content_type='application/json',
            HTTP_X_CHAPA_SIGNATURE=sig,
        )

    @patch('billing.services.ChapaClient')
    def test_successful_webhook_marks_payment_success(self, MockClient):
        MockClient.return_value.verify.return_value = {
            'status': 'success',
            'reference': 'CHAPA-REF-001',
            'mode': 'test',
        }
        payload = {'event': 'charge.success', 'tx_ref': self.tx_ref}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'success')
        self.assertEqual(self.payment.chapa_ref, 'CHAPA-REF-001')
        self.assertIsNotNone(self.payment.paid_at)

    @patch('billing.services.ChapaClient')
    def test_non_charge_success_event_ignored(self, MockClient):
        payload = {'event': 'charge.pending', 'tx_ref': self.tx_ref}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')
        MockClient.return_value.verify.assert_not_called()

    def test_invalid_signature_returns_400(self):
        payload = {'event': 'charge.success', 'tx_ref': self.tx_ref}
        body = json.dumps(payload).encode('utf-8')
        from rest_framework.test import APIClient
        client = APIClient()
        resp = client.post(
            '/api/billing/webhook/chapa/',
            data=body,
            content_type='application/json',
            HTTP_X_CHAPA_SIGNATURE='bad-signature',
        )
        self.assertEqual(resp.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    @patch('billing.services.ChapaClient')
    def test_idempotent_already_success(self, MockClient):
        self.payment.status = 'success'
        self.payment.save()
        payload = {'event': 'charge.success', 'tx_ref': self.tx_ref}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        MockClient.return_value.verify.assert_not_called()

    @patch('billing.services.ChapaClient')
    def test_chapa_verification_returns_non_success_marks_failed(self, MockClient):
        MockClient.return_value.verify.return_value = {
            'status': 'failed',
        }
        payload = {'event': 'charge.success', 'tx_ref': self.tx_ref}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'failed')

    def test_invalid_json_returns_400(self):
        from rest_framework.test import APIClient
        client = APIClient()
        resp = client.post(
            '/api/billing/webhook/chapa/',
            data=b'not-json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    @patch('billing.services.ChapaClient')
    def test_unknown_tx_ref_returns_200_with_detail(self, MockClient):
        """Return 200 even for unknown tx_ref so Chapa stops retrying."""
        payload = {'event': 'charge.success', 'tx_ref': 'nonexistent-ref'}
        resp = self._post_webhook(payload)
        self.assertEqual(resp.status_code, 200)

    @override_settings(CHAPA_WEBHOOK_SECRET='')
    @patch('billing.services.ChapaClient')
    def test_no_secret_configured_skips_signature_check(self, MockClient):
        """If CHAPA_WEBHOOK_SECRET is empty, all webhooks are accepted (dev mode)."""
        MockClient.return_value.verify.return_value = {
            'status': 'success',
            'reference': 'REF',
            'mode': 'test',
        }
        payload = {'event': 'charge.success', 'tx_ref': self.tx_ref}
        body = json.dumps(payload).encode('utf-8')
        from rest_framework.test import APIClient
        client = APIClient()
        resp = client.post(
            '/api/billing/webhook/chapa/',
            data=body,
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Service layer unit tests (no HTTP)
# ---------------------------------------------------------------------------

class InitiatePaymentServiceTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        self.invoice      = _make_finalized_invoice(
            self.clinic_id, self.admin, self.receptionist, self.doctor
        )

    @patch('billing.services.ChapaClient')
    def test_returns_payment_and_checkout_url(self, MockClient):
        MockClient.return_value.initialize.return_value = 'https://pay.chapa.co/test'
        with self.settings(CHAPA_SECRET_KEY='test-key'):
            payment, url = initiate_payment(
                invoice=self.invoice,
                initiated_by_id=self.admin.id,
            )
        self.assertIsInstance(payment, Payment)
        self.assertEqual(url, 'https://pay.chapa.co/test')
        self.assertEqual(payment.status, 'pending')

    def test_raises_if_invoice_not_finalized(self):
        patient   = make_patient(self.clinic_id)
        visit     = make_visit(self.clinic_id, patient, self.receptionist)
        invoice   = make_invoice(self.clinic_id, visit, self.admin)
        with self.assertRaises(BillingError) as ctx:
            with self.settings(CHAPA_SECRET_KEY='test-key'):
                initiate_payment(invoice=invoice, initiated_by_id=self.admin.id)
        self.assertIn('finalized', str(ctx.exception).lower())

    @patch('billing.services.ChapaClient')
    def test_raises_if_already_paid(self, MockClient):
        Payment.objects.create(
            clinic_id=self.invoice.clinic_id,
            invoice_id=self.invoice.id,
            initiated_by=self.admin.id,
            tx_ref='clinic-already-paid',
            amount=self.invoice.total_amount,
            currency='ETB',
            status='success',
        )
        with self.assertRaises(BillingError) as ctx:
            with self.settings(CHAPA_SECRET_KEY='test-key'):
                initiate_payment(invoice=self.invoice, initiated_by_id=self.admin.id)
        self.assertIn('already been paid', str(ctx.exception).lower())

    @patch('billing.services.ChapaClient')
    def test_raises_if_pending_exists(self, MockClient):
        Payment.objects.create(
            clinic_id=self.invoice.clinic_id,
            invoice_id=self.invoice.id,
            initiated_by=self.admin.id,
            tx_ref='clinic-pending-exists',
            amount=self.invoice.total_amount,
            currency='ETB',
            status='pending',
        )
        with self.assertRaises(BillingError) as ctx:
            with self.settings(CHAPA_SECRET_KEY='test-key'):
                initiate_payment(invoice=self.invoice, initiated_by_id=self.admin.id)
        self.assertIn('pending', str(ctx.exception).lower())

    def test_raises_if_no_chapa_key(self):
        with self.assertRaises(BillingError) as ctx:
            with self.settings(CHAPA_SECRET_KEY=''):
                initiate_payment(invoice=self.invoice, initiated_by_id=self.admin.id)
        self.assertIn('CHAPA_SECRET_KEY', str(ctx.exception))
