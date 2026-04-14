"""
Billing integration tests.

Covers:
  - Invoice creation (RBAC, clinic scoping)
  - Line items: from test order (snapshot) and ad-hoc; validation guards
  - Subtotal recomputed on add/remove
  - Finalization: locks totals, stamps test_orders.billed_invoice_id, duplicate-billing guard
  - Void: releases test_orders, requires reason, admin only
  - price_at_order_time snapshotted at order creation
  - Full end-to-end workflow
"""
import uuid
from decimal import Decimal

from django.test import TestCase

from billing.models import Invoice, InvoiceLineItem
from lab.models import TestOrder
from .utils import (
    auth_client, make_user, make_patient, make_visit,
    make_lab_test, make_test_order, make_invoice, make_line_item,
)


class InvoiceCreateTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')
        self.lab_tech     = make_user(self.clinic_id, 'lab_tech')

        patient = make_patient(self.clinic_id)
        self.visit = make_visit(self.clinic_id, patient, self.receptionist)

    def test_receptionist_can_create_invoice(self):
        resp = auth_client(self.receptionist).post('/api/billing/invoices/', {
            'visit_id': str(self.visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'draft')
        self.assertEqual(resp.data['line_items'], [])
        self.assertEqual(resp.data['total_amount'], '0.00')

    def test_admin_can_create_invoice(self):
        resp = auth_client(self.admin).post('/api/billing/invoices/', {
            'visit_id': str(self.visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)

    def test_doctor_cannot_create_invoice(self):
        resp = auth_client(self.doctor).post('/api/billing/invoices/', {
            'visit_id': str(self.visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_lab_tech_cannot_create_invoice(self):
        resp = auth_client(self.lab_tech).post('/api/billing/invoices/', {
            'visit_id': str(self.visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_visit_from_other_clinic_returns_404(self):
        other_clinic_id = uuid.uuid4()
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)

        resp = auth_client(self.receptionist).post('/api/billing/invoices/', {
            'visit_id': str(other_visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    def test_patient_id_denormalized_from_visit(self):
        resp = auth_client(self.receptionist).post('/api/billing/invoices/', {
            'visit_id': str(self.visit.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data['patient_id']), str(self.visit.patient_id))

    def test_multiple_invoices_allowed_per_visit(self):
        auth_client(self.receptionist).post('/api/billing/invoices/', {'visit_id': str(self.visit.id)}, format='json')
        auth_client(self.receptionist).post('/api/billing/invoices/', {'visit_id': str(self.visit.id)}, format='json')
        count = Invoice.objects.for_clinic(self.clinic_id).filter(visit_id=self.visit.id).count()
        self.assertEqual(count, 2)


class InvoiceListTests(TestCase):

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        patient = make_patient(self.clinic_id)
        self.visit = make_visit(self.clinic_id, patient, self.receptionist)

    def test_clinic_isolation(self):
        make_invoice(self.clinic_id, self.visit, self.admin)

        other_clinic_id = uuid.uuid4()
        other_admin = make_user(other_clinic_id, 'admin')
        other_patient = make_patient(other_clinic_id)
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        make_invoice(other_clinic_id, other_visit, other_admin)

        resp = auth_client(self.admin).get('/api/billing/invoices/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_filter_by_visit_id(self):
        patient2 = make_patient(self.clinic_id)
        visit2 = make_visit(self.clinic_id, patient2, self.receptionist)
        make_invoice(self.clinic_id, self.visit, self.admin)
        make_invoice(self.clinic_id, visit2, self.admin)

        resp = auth_client(self.admin).get(f'/api/billing/invoices/?visit_id={self.visit.id}')
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(str(resp.data['results'][0]['visit_id']), str(self.visit.id))

    def test_filter_by_status(self):
        make_invoice(self.clinic_id, self.visit, self.admin)  # draft
        resp = auth_client(self.admin).get('/api/billing/invoices/?status=draft')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(i['status'] == 'draft' for i in resp.data['results']))


class LineItemTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')

        patient        = make_patient(self.clinic_id)
        self.visit     = make_visit(self.clinic_id, patient, self.receptionist)
        self.lab_test  = make_lab_test(self.clinic_id, self.admin)
        self.order     = make_test_order(self.visit, self.lab_test, self.doctor, status='completed')
        self.invoice   = make_invoice(self.clinic_id, self.visit, self.admin)

    def test_add_line_item_from_test_order(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['test_name'], self.lab_test.name)
        self.assertEqual(Decimal(resp.data['unit_price']), Decimal(str(self.order.price_at_order_time)))
        self.assertEqual(resp.data['quantity'], 1)

    def test_unit_price_snapshotted_not_live(self):
        """Changing lab_test.price after creating a line item must not affect it."""
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        # Simulate price change on the live catalogue
        self.lab_test.price = Decimal('999.00')
        self.lab_test.save()

        resp = auth_client(self.receptionist).get(f'/api/billing/invoices/{self.invoice.id}/')
        item = resp.data['line_items'][0]
        self.assertEqual(Decimal(item['unit_price']), Decimal('25.00'))  # original price

    def test_add_adhoc_line_item(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_name': 'Consultation Fee', 'unit_price': '50.00', 'quantity': 1},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['test_name'], 'Consultation Fee')
        self.assertIsNone(resp.data['test_order_id'])

    def test_subtotal_recomputed_on_add(self):
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.subtotal, Decimal(str(self.order.price_at_order_time)))
        self.assertEqual(self.invoice.total_amount, Decimal(str(self.order.price_at_order_time)))

    def test_cannot_add_canceled_order(self):
        self.order.status = 'canceled'
        self.order.save()
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_add_non_billable_order(self):
        self.order.is_billable = False
        self.order.save()
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_add_same_order_twice(self):
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_add_order_from_other_clinic(self):
        other_clinic_id = uuid.uuid4()
        other_admin = make_user(other_clinic_id, 'admin')
        other_doctor = make_user(other_clinic_id, 'doctor')
        other_receptionist = make_user(other_clinic_id, 'receptionist')
        other_patient = make_patient(other_clinic_id)
        other_visit = make_visit(other_clinic_id, other_patient, other_receptionist)
        other_lab_test = make_lab_test(other_clinic_id, other_admin)
        other_order = make_test_order(other_visit, other_lab_test, other_doctor, status='completed')

        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(other_order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_remove_line_item_recomputes_subtotal(self):
        add_resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        item_id = add_resp.data['id']
        del_resp = auth_client(self.receptionist).delete(
            f'/api/billing/invoices/{self.invoice.id}/items/{item_id}/'
        )
        self.assertEqual(del_resp.status_code, 204)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.subtotal, Decimal('0.00'))

    def test_cannot_add_to_finalized_invoice(self):
        make_line_item(self.invoice, self.order, self.lab_test)
        auth_client(self.admin).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        another_lab_test = make_lab_test(self.clinic_id, self.admin)
        another_order = make_test_order(self.visit, another_lab_test, self.doctor, status='completed')
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(another_order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_doctor_cannot_add_line_item(self):
        resp = auth_client(self.doctor).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(self.order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_adhoc_requires_both_name_and_price(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_name': 'Consultation Fee'},   # missing unit_price
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


class InvoiceFinalizeTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')

        patient       = make_patient(self.clinic_id)
        self.visit    = make_visit(self.clinic_id, patient, self.receptionist)
        self.lab_test = make_lab_test(self.clinic_id, self.admin)
        self.order    = make_test_order(self.visit, self.lab_test, self.doctor, status='completed')
        self.invoice  = make_invoice(self.clinic_id, self.visit, self.admin)
        make_line_item(self.invoice, self.order, self.lab_test)

    def test_receptionist_can_finalize(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'finalized')

    def test_finalization_locks_total_amount(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(Decimal(resp.data['total_amount']), Decimal('25.00'))

    def test_finalization_with_discount(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '5.00'},
            format='json',
        )
        self.assertEqual(Decimal(resp.data['total_amount']), Decimal('20.00'))
        self.assertEqual(Decimal(resp.data['discount_amount']), Decimal('5.00'))

    def test_finalization_stamps_test_order(self):
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.order.refresh_from_db()
        self.assertEqual(str(self.order.billed_invoice_id), str(self.invoice.id))

    def test_cannot_finalize_empty_invoice(self):
        empty_invoice = make_invoice(self.clinic_id, self.visit, self.admin)
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{empty_invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_finalize_twice(self):
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_billing_blocked(self):
        """Same test order on two invoices — second finalization must fail."""
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        second_invoice = make_invoice(self.clinic_id, self.visit, self.admin)
        make_line_item(second_invoice, self.order, self.lab_test)
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{second_invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('detail', resp.data)

    def test_finalized_invoice_immutable_to_line_item_add(self):
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        another_test = make_lab_test(self.clinic_id, self.admin)
        another_order = make_test_order(self.visit, another_test, self.doctor, status='completed')
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/items/',
            {'test_order_id': str(another_order.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


class InvoiceVoidTests(TestCase):

    def setUp(self):
        self.clinic_id    = uuid.uuid4()
        self.admin        = make_user(self.clinic_id, 'admin')
        self.receptionist = make_user(self.clinic_id, 'receptionist')
        self.doctor       = make_user(self.clinic_id, 'doctor')

        patient       = make_patient(self.clinic_id)
        self.visit    = make_visit(self.clinic_id, patient, self.receptionist)
        self.lab_test = make_lab_test(self.clinic_id, self.admin)
        self.order    = make_test_order(self.visit, self.lab_test, self.doctor, status='completed')
        self.invoice  = make_invoice(self.clinic_id, self.visit, self.admin)
        make_line_item(self.invoice, self.order, self.lab_test)

        # Finalize first
        auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )

    def test_admin_can_void_finalized_invoice(self):
        resp = auth_client(self.admin).post(
            f'/api/billing/invoices/{self.invoice.id}/void/',
            {'void_reason': 'Patient requested cancellation'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'void')
        self.assertEqual(resp.data['void_reason'], 'Patient requested cancellation')

    def test_void_releases_test_order_for_rebilling(self):
        auth_client(self.admin).post(
            f'/api/billing/invoices/{self.invoice.id}/void/',
            {'void_reason': 'Rebilling required'},
            format='json',
        )
        self.order.refresh_from_db()
        self.assertIsNone(self.order.billed_invoice_id)

    def test_voided_order_can_be_billed_on_new_invoice(self):
        auth_client(self.admin).post(
            f'/api/billing/invoices/{self.invoice.id}/void/',
            {'void_reason': 'Rebilling'},
            format='json',
        )
        new_invoice = make_invoice(self.clinic_id, self.visit, self.admin)
        make_line_item(new_invoice, self.order, self.lab_test)
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{new_invoice.id}/finalize/',
            {'discount_amount': '0.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_receptionist_cannot_void(self):
        resp = auth_client(self.receptionist).post(
            f'/api/billing/invoices/{self.invoice.id}/void/',
            {'void_reason': 'Test'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_void_requires_reason(self):
        resp = auth_client(self.admin).post(
            f'/api/billing/invoices/{self.invoice.id}/void/',
            {'void_reason': ''},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_void_draft_invoice(self):
        draft_invoice = make_invoice(self.clinic_id, self.visit, self.admin)
        resp = auth_client(self.admin).post(
            f'/api/billing/invoices/{draft_invoice.id}/void/',
            {'void_reason': 'Test'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


class PriceSnapshotTests(TestCase):
    """price_at_order_time is snapshotted from lab_test.price at order creation time."""

    def setUp(self):
        self.clinic_id = uuid.uuid4()
        self.admin     = make_user(self.clinic_id, 'admin')
        self.doctor    = make_user(self.clinic_id, 'doctor')
        self.receptionist = make_user(self.clinic_id, 'receptionist')

        patient         = make_patient(self.clinic_id)
        self.visit      = make_visit(self.clinic_id, patient, self.receptionist)
        self.lab_test   = make_lab_test(self.clinic_id, self.admin)  # price = 25.00

    def test_price_snapshotted_via_api(self):
        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id':  str(self.visit.id),
            'test_id':   str(self.lab_test.id),
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Decimal(resp.data['price_at_order_time']), Decimal('25.00'))

    def test_price_snapshot_unaffected_by_catalogue_change(self):
        resp = auth_client(self.doctor).post('/api/lab/orders/', {
            'visit_id': str(self.visit.id),
            'test_id':  str(self.lab_test.id),
        }, format='json')
        order_id = resp.data['id']

        # Admin raises the price in the catalogue
        auth_client(self.admin).patch(
            f'/api/lab/tests/{self.lab_test.id}/',
            {'price': '99.00'},
            format='json',
        )

        order = TestOrder.objects.get(id=order_id)
        self.assertEqual(order.price_at_order_time, Decimal('25.00'))
