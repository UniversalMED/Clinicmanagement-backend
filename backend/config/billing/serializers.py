from rest_framework import serializers
from .models import Invoice, InvoiceLineItem, Payment


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = [
            'id', 'invoice_id', 'test_order_id',
            'test_name', 'unit_price', 'quantity', 'subtotal',
            'notes', 'created_at',
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    line_items = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'clinic_id', 'visit_id', 'patient_id',
            'issued_by', 'finalized_by', 'voided_by',
            'status', 'subtotal', 'discount_amount', 'total_amount',
            'notes', 'finalized_at', 'voided_at', 'void_reason',
            'line_items', 'created_at',
        ]
        read_only_fields = fields

    def get_line_items(self, obj):
        return InvoiceLineItemSerializer(
            InvoiceLineItem.objects.filter(invoice_id=obj.id),
            many=True,
        ).data


# ---------------------------------------------------------------------------
# Write serializers
# ---------------------------------------------------------------------------

class CreateInvoiceSerializer(serializers.Serializer):
    visit_id = serializers.UUIDField()
    notes    = serializers.CharField(required=False, allow_blank=True, default='')


class AddLineItemSerializer(serializers.Serializer):
    """
    Two mutually exclusive modes:
      A) test_order_id  — system resolves test_name + unit_price from the order snapshot
      B) test_name + unit_price  — ad-hoc charge (consultation fee, admin fee, etc.)
    """
    test_order_id = serializers.UUIDField(required=False)
    test_name     = serializers.CharField(required=False, max_length=500)
    unit_price    = serializers.DecimalField(required=False, max_digits=10, decimal_places=2, min_value=0)
    quantity      = serializers.IntegerField(default=1, min_value=1)
    notes         = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        has_order = bool(data.get('test_order_id'))
        has_adhoc = bool(data.get('test_name')) and data.get('unit_price') is not None

        if not has_order and not has_adhoc:
            raise serializers.ValidationError(
                'Provide either test_order_id or both test_name and unit_price.'
            )
        if has_order and (data.get('test_name') or data.get('unit_price') is not None):
            raise serializers.ValidationError(
                'Provide test_order_id or test_name/unit_price — not both.'
            )
        return data


class FinalizeInvoiceSerializer(serializers.Serializer):
    discount_amount = serializers.DecimalField(
        required=False, max_digits=10, decimal_places=2, min_value=0, default=0
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class VoidInvoiceSerializer(serializers.Serializer):
    void_reason = serializers.CharField(min_length=1)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Payment
        fields = [
            'id', 'invoice_id', 'tx_ref', 'chapa_ref',
            'amount', 'currency', 'status', 'mode',
            'paid_at', 'created_at',
        ]
        read_only_fields = fields
