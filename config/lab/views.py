import uuid

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import LabTest, TestOrder, TestResult
from .serializers import (
    LabTestSerializer,
    TestOrderSerializer,
    TestOrderUpdateSerializer,
    TestResultSerializer,
)
from clinic.models import Visit
from users.permissions import HasPermission
from core.querysets import PaginatedListMixin


def _parse_uuid(value, field_name):
    """Return (uuid, None) on success or (None, Response) on invalid format."""
    try:
        return uuid.UUID(str(value)), None
    except (ValueError, AttributeError):
        return None, Response(
            {field_name: "Must be a valid UUID."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class LabTestListView(PaginatedListMixin, APIView):
    """
    GET  /api/lab/tests/   — all authenticated staff (active tests only for non-admins)
    POST /api/lab/tests/   — admin
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('manage_lab_catalogue')()]
        return super().get_permissions()

    def get(self, request):
        qs = LabTest.objects.for_clinic(request.user.clinic_id)
        if request.user.role != 'admin':
            qs = qs.filter(is_active=True)
        return self.paginate(qs, LabTestSerializer, request)

    def post(self, request):
        serializer = LabTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(clinic_id=request.user.clinic_id, created_by=request.user.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class LabTestDetailView(APIView):
    """
    GET   /api/lab/tests/<id>/   — all authenticated staff
    PATCH /api/lab/tests/<id>/   — admin
    """

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [HasPermission.for_permission('manage_lab_catalogue')()]
        return super().get_permissions()

    def _get_object(self, request, test_id):
        return get_object_or_404(LabTest.objects.for_clinic(request.user.clinic_id), id=test_id)

    def get(self, request, test_id):
        return Response(LabTestSerializer(self._get_object(request, test_id)).data)

    def patch(self, request, test_id):
        lab_test = self._get_object(request, test_id)
        serializer = LabTestSerializer(lab_test, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class TestOrderListView(PaginatedListMixin, APIView):
    """
    GET  /api/lab/orders/   — all authenticated staff
    POST /api/lab/orders/   — doctor
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('order_lab_test')()]
        return super().get_permissions()

    def _clinic_visit_ids(self, request):
        # TestOrder has no clinic_id — resolved through visits
        return Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)

    def get(self, request):
        qs = TestOrder.objects.filter(visit_id__in=self._clinic_visit_ids(request))
        visit_id = request.query_params.get('visit_id')
        if visit_id:
            qs = qs.filter(visit_id=visit_id)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return self.paginate(qs, TestOrderSerializer, request)

    def post(self, request):
        visit_id = request.data.get('visit_id')
        if visit_id:
            parsed, err = _parse_uuid(visit_id, 'visit_id')
            if err:
                return err
            get_object_or_404(Visit.objects.for_clinic(request.user.clinic_id), id=parsed)

        test_id = request.data.get('test_id')
        if test_id:
            parsed, err = _parse_uuid(test_id, 'test_id')
            if err:
                return err
            get_object_or_404(LabTest.objects.for_clinic(request.user.clinic_id), id=parsed, is_active=True)

        serializer = TestOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(ordered_by=request.user.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TestOrderDetailView(APIView):
    """
    GET   /api/lab/orders/<id>/   — all authenticated staff
    PATCH /api/lab/orders/<id>/   — lab_tech, admin
    """

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [HasPermission.for_permission('process_lab_order')()]
        return super().get_permissions()

    def _get_object(self, request, order_id):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        return get_object_or_404(TestOrder, id=order_id, visit_id__in=visit_ids)

    def get(self, request, order_id):
        return Response(TestOrderSerializer(self._get_object(request, order_id)).data)

    def patch(self, request, order_id):
        order = self._get_object(request, order_id)
        serializer = TestOrderUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(order, field, value)
        order.save(update_fields=list(serializer.validated_data.keys()))
        return Response(TestOrderSerializer(order).data)


class TestResultListView(PaginatedListMixin, APIView):
    """
    GET  /api/lab/results/   — all authenticated staff
    POST /api/lab/results/   — lab_tech
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('write_lab_result')()]
        return super().get_permissions()

    def _clinic_order_ids(self, request):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        return TestOrder.objects.filter(visit_id__in=visit_ids).values_list('id', flat=True)

    def get(self, request):
        qs = TestResult.objects.filter(test_order_id__in=self._clinic_order_ids(request))
        order_id = request.query_params.get('order_id')
        if order_id:
            qs = qs.filter(test_order_id=order_id)
        return self.paginate(qs, TestResultSerializer, request)

    def post(self, request):
        order_id = request.data.get('test_order_id')
        if order_id:
            parsed, err = _parse_uuid(order_id, 'test_order_id')
            if err:
                return err
            visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
            get_object_or_404(TestOrder, id=parsed, visit_id__in=visit_ids)

        serializer = TestResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(technician_id=request.user.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TestResultDetailView(APIView):
    """
    GET /api/lab/results/<id>/   — all authenticated staff
    """

    def get(self, request, result_id):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        order_ids = TestOrder.objects.filter(visit_id__in=visit_ids).values_list('id', flat=True)
        result = get_object_or_404(TestResult, id=result_id, test_order_id__in=order_ids)
        return Response(TestResultSerializer(result).data)
