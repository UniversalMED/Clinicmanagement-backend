import hashlib
import hmac
import json
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Invoice, InvoiceLineItem, Payment
from .serializers import (
    InvoiceSerializer,
    CreateInvoiceSerializer,
    AddLineItemSerializer,
    FinalizeInvoiceSerializer,
    VoidInvoiceSerializer,
    PaymentSerializer,
)
from .services import (
    BillingError,
    finalize_invoice, void_invoice, recompute_invoice_totals,
    initiate_payment, process_chapa_webhook,
)
from clinic.models import Visit
from lab.models import LabTest, TestOrder
from users.permissions import HasPermission
from core.querysets import PaginatedListMixin
from audit.mixins import AuditLogMixin

logger = logging.getLogger(__name__)


def _parse_uuid(value, field_name):
    try:
        return uuid.UUID(str(value)), None
    except (ValueError, AttributeError):
        return None, Response(
            {field_name: 'Must be a valid UUID.'},
            status=status.HTTP_400_BAD_REQUEST,
        )


class InvoiceListView(AuditLogMixin, PaginatedListMixin, APIView):
    """
    GET  /api/billing/invoices/   — all authenticated staff, scoped to clinic
                                     ?visit_id=  ?status=  ?patient_id=
    POST /api/billing/invoices/   — receptionist, admin
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('manage_billing')()]
        return super().get_permissions()

    def get(self, request):
        qs = Invoice.objects.for_clinic(request.user.clinic_id)
        visit_id   = request.query_params.get('visit_id')
        patient_id = request.query_params.get('patient_id')
        status_f   = request.query_params.get('status')
        if visit_id:
            qs = qs.filter(visit_id=visit_id)
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if status_f:
            qs = qs.filter(status=status_f)
        return self.paginate(qs, InvoiceSerializer, request)

    def post(self, request):
        serializer = CreateInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        visit_id_parsed, err = _parse_uuid(data['visit_id'], 'visit_id')
        if err:
            return err

        visit = get_object_or_404(
            Visit.objects.for_clinic(request.user.clinic_id), id=visit_id_parsed
        )

        invoice = Invoice.objects.create(
            clinic_id=request.user.clinic_id,
            visit_id=visit.id,
            patient_id=visit.patient_id,
            issued_by=request.user.id,
            notes=data.get('notes', ''),
        )
        self.log_action(request, 'create', 'invoice', invoice.id)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceDetailView(APIView):
    """GET /api/billing/invoices/<id>/  — all authenticated staff"""

    def _get_invoice(self, request, invoice_id):
        return get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )

    def get(self, request, invoice_id):
        return Response(InvoiceSerializer(self._get_invoice(request, invoice_id)).data)


class InvoiceLineItemsView(AuditLogMixin, APIView):
    """
    POST   /api/billing/invoices/<id>/items/   — add line item to draft (receptionist, admin)
    """
    permission_classes = [HasPermission.for_permission('manage_billing')]

    def _get_draft_invoice(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        if invoice.status != 'draft':
            return invoice, Response(
                {'detail': 'Line items can only be modified on draft invoices.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return invoice, None

    def post(self, request, invoice_id):
        invoice, err = self._get_draft_invoice(request, invoice_id)
        if err:
            return err

        serializer = AddLineItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        test_order_id = data.get('test_order_id')

        if test_order_id:
            # Validate the test order belongs to this clinic
            visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
            test_order = get_object_or_404(TestOrder, id=test_order_id, visit_id__in=visit_ids)

            if not test_order.is_billable:
                return Response(
                    {'detail': 'This test order is marked as non-billable.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if test_order.status == 'canceled':
                return Response(
                    {'detail': 'Cannot bill a canceled test order.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if test_order.billed_invoice_id is not None:
                return Response(
                    {'detail': 'This test order has already been billed on another invoice.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if InvoiceLineItem.objects.filter(
                invoice_id=invoice.id, test_order_id=test_order_id
            ).exists():
                return Response(
                    {'detail': 'This test order is already on this invoice.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Resolve snapshot values from the order (never from live lab_test.price)
            lab_test = get_object_or_404(
                LabTest.objects.for_clinic(request.user.clinic_id), id=test_order.test_id
            )
            test_name  = lab_test.name
            unit_price = test_order.price_at_order_time
        else:
            # Ad-hoc line item (consultation fee, admin charge, etc.)
            test_name  = data['test_name']
            unit_price = data['unit_price']
            test_order_id = None

        quantity = data.get('quantity', 1)
        subtotal = unit_price * quantity

        with transaction.atomic():
            line_item = InvoiceLineItem.objects.create(
                invoice_id=invoice.id,
                test_order_id=test_order_id,
                test_name=test_name,
                unit_price=unit_price,
                quantity=quantity,
                subtotal=subtotal,
                notes=data.get('notes', ''),
            )
            recompute_invoice_totals(invoice)

        self.log_action(request, 'update', 'invoice', invoice.id)
        from .serializers import InvoiceLineItemSerializer
        return Response(InvoiceLineItemSerializer(line_item).data, status=status.HTTP_201_CREATED)


class InvoiceLineItemDetailView(AuditLogMixin, APIView):
    """
    DELETE /api/billing/invoices/<id>/items/<item_id>/  — remove line item from draft
    """
    permission_classes = [HasPermission.for_permission('manage_billing')]

    def delete(self, request, invoice_id, item_id):
        invoice = get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        if invoice.status != 'draft':
            return Response(
                {'detail': 'Line items can only be modified on draft invoices.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        line_item = get_object_or_404(InvoiceLineItem, id=item_id, invoice_id=invoice.id)

        with transaction.atomic():
            line_item.delete()
            recompute_invoice_totals(invoice)

        self.log_action(request, 'update', 'invoice', invoice.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvoiceFinalizeView(AuditLogMixin, APIView):
    """
    POST /api/billing/invoices/<id>/finalize/  — lock invoice and stamp test orders
    """
    permission_classes = [HasPermission.for_permission('manage_billing')]

    def post(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        serializer = FinalizeInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            finalize_invoice(
                invoice=invoice,
                finalized_by_id=request.user.id,
                discount_amount=data.get('discount_amount', 0),
                notes=data.get('notes'),
            )
        except BillingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self.log_action(request, 'update', 'invoice', invoice.id)
        return Response(InvoiceSerializer(invoice).data)


class InvoiceVoidView(AuditLogMixin, APIView):
    """
    POST /api/billing/invoices/<id>/void/  — void a finalized invoice (admin only)
    Releases all stamped test orders so they can be re-billed.
    """
    permission_classes = [HasPermission.for_permission('void_invoice')]

    def post(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        serializer = VoidInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            void_invoice(
                invoice=invoice,
                voided_by_id=request.user.id,
                void_reason=serializer.validated_data['void_reason'],
            )
        except BillingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self.log_action(request, 'update', 'invoice', invoice.id)
        return Response(InvoiceSerializer(invoice).data)


class InvoicePayView(AuditLogMixin, APIView):
    """
    POST /api/billing/invoices/<id>/pay/
    Initiate a Chapa payment for a finalized invoice.
    Returns a checkout_url to redirect the payer.
    """
    permission_classes = [HasPermission.for_permission('manage_billing')]

    def post(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        callback_url = request.data.get('callback_url', '')
        return_url   = request.data.get('return_url', '')

        try:
            payment, checkout_url = initiate_payment(
                invoice=invoice,
                initiated_by_id=request.user.id,
                callback_url=callback_url,
                return_url=return_url,
            )
        except BillingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self.log_action(request, 'create', 'payment', payment.id)
        return Response({
            'payment':      PaymentSerializer(payment).data,
            'checkout_url': checkout_url,
        }, status=status.HTTP_201_CREATED)


class InvoicePaymentListView(PaginatedListMixin, APIView):
    """
    GET /api/billing/invoices/<id>/payments/
    List all payment attempts for an invoice.
    """
    def get(self, request, invoice_id):
        get_object_or_404(
            Invoice.objects.for_clinic(request.user.clinic_id), id=invoice_id
        )
        qs = Payment.objects.for_clinic(request.user.clinic_id).filter(
            invoice_id=invoice_id
        )
        return self.paginate(qs, PaymentSerializer, request)


class ChapaWebhookView(APIView):
    """
    POST /api/billing/webhook/chapa/

    Receives payment confirmation from Chapa.
    - No JWT auth (webhook comes from Chapa, not a clinic user).
    - Verified via HMAC-SHA256 signature.
    - Must return HTTP 200 or Chapa retries every 10 min for 72 hours.
    """
    authentication_classes = []
    permission_classes      = [AllowAny]

    def post(self, request):
        # Verify signature
        secret = getattr(settings, 'CHAPA_WEBHOOK_SECRET', '')
        if secret:
            sig = (
                request.headers.get('x-chapa-signature') or
                request.headers.get('Chapa-Signature', '')
            )
            body = request.body
            expected = hmac.new(
                secret.encode('utf-8'), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, sig):
                logger.warning('Chapa webhook signature mismatch')
                return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return Response({'detail': 'Invalid JSON.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            process_chapa_webhook(payload)
        except BillingError as exc:
            logger.error('Chapa webhook processing error: %s', exc)
            # Still return 200 to stop Chapa retrying for unresolvable errors
            return Response({'detail': str(exc)})

        return Response({'detail': 'ok'})
