import uuid

from django.db import models
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Patient, Visit, Consultation, Prescription, PrescriptionItem
from .serializers import (
    PatientSerializer, VisitSerializer, ConsultationSerializer,
    PrescriptionSerializer, PrescriptionCreateSerializer, PrescriptionItemSerializer,
)
from users.permissions import HasPermission
from core.querysets import PaginatedListMixin
from audit.mixins import AuditLogMixin


def _parse_uuid(value, field_name):
    """Return (uuid, None) on success or (None, Response) on invalid format."""
    try:
        return uuid.UUID(str(value)), None
    except (ValueError, AttributeError):
        return None, Response(
            {field_name: "Must be a valid UUID."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class PatientListView(AuditLogMixin, PaginatedListMixin, APIView):
    """
    GET  /api/clinic/patients/        — all authenticated staff, scoped to clinic
    POST /api/clinic/patients/        — receptionist, admin
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('write_patient')()]
        return super().get_permissions()

    def get(self, request):
        qs = Patient.objects.for_clinic(request.user.clinic_id)
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(
                models.Q(full_name__icontains=search) |
                models.Q(phone__icontains=search)
            )
        return self.paginate(qs, PatientSerializer, request)

    def post(self, request):
        serializer = PatientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(clinic_id=request.user.clinic_id)
        self.log_action(request, 'create', 'patient', serializer.instance.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PatientDetailView(AuditLogMixin, APIView):
    """
    GET   /api/clinic/patients/<id>/  — all authenticated staff
    PATCH /api/clinic/patients/<id>/  — receptionist, admin
    """

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [HasPermission.for_permission('write_patient')()]
        return super().get_permissions()

    def _get_object(self, request, patient_id):
        return get_object_or_404(Patient.objects.for_clinic(request.user.clinic_id), id=patient_id)

    def get(self, request, patient_id):
        return Response(PatientSerializer(self._get_object(request, patient_id)).data)

    def patch(self, request, patient_id):
        patient = self._get_object(request, patient_id)
        serializer = PatientSerializer(patient, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.log_action(request, 'update', 'patient', patient.id)
        return Response(serializer.data)


class VisitListView(AuditLogMixin, PaginatedListMixin, APIView):
    """
    GET  /api/clinic/visits/   — all authenticated staff
    POST /api/clinic/visits/   — receptionist, admin
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('write_visit')()]
        return super().get_permissions()

    def get(self, request):
        qs = Visit.objects.for_clinic(request.user.clinic_id)
        status_filter = request.query_params.get('status')
        patient_id    = request.query_params.get('patient_id')
        date          = request.query_params.get('date')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if patient_id:
            qs = qs.filter(patient_id=patient_id)
        if date:
            qs = qs.filter(created_at__date=date)
        return self.paginate(qs, VisitSerializer, request)

    def post(self, request):
        # Ensure the patient belongs to the same clinic
        patient_id = request.data.get('patient_id')
        if patient_id:
            parsed, err = _parse_uuid(patient_id, 'patient_id')
            if err:
                return err
            get_object_or_404(Patient.objects.for_clinic(request.user.clinic_id), id=parsed)

        serializer = VisitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(clinic_id=request.user.clinic_id, created_by=request.user.id)
        self.log_action(request, 'create', 'visit', serializer.instance.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class VisitDetailView(AuditLogMixin, APIView):
    """
    GET   /api/clinic/visits/<id>/   — all authenticated staff
    PATCH /api/clinic/visits/<id>/   — receptionist, admin, doctor
    """

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [HasPermission.for_permission('update_visit')()]
        return super().get_permissions()

    def _get_object(self, request, visit_id):
        return get_object_or_404(Visit.objects.for_clinic(request.user.clinic_id), id=visit_id)

    def get(self, request, visit_id):
        return Response(VisitSerializer(self._get_object(request, visit_id)).data)

    def patch(self, request, visit_id):
        visit = self._get_object(request, visit_id)
        serializer = VisitSerializer(visit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.log_action(request, 'update', 'visit', visit.id)
        return Response(serializer.data)


class ConsultationListView(AuditLogMixin, PaginatedListMixin, APIView):
    """
    GET  /api/clinic/consultations/   — all authenticated staff
    POST /api/clinic/consultations/   — doctor
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('write_consultation')()]
        return super().get_permissions()

    def get(self, request):
        # Consultation has no clinic_id — resolved through visits
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        qs = Consultation.objects.filter(visit_id__in=visit_ids)
        visit_id = request.query_params.get('visit_id')
        if visit_id:
            qs = qs.filter(visit_id=visit_id)
        return self.paginate(qs, ConsultationSerializer, request)

    def post(self, request):
        visit_id = request.data.get('visit_id')
        if visit_id:
            parsed, err = _parse_uuid(visit_id, 'visit_id')
            if err:
                return err
            get_object_or_404(Visit.objects.for_clinic(request.user.clinic_id), id=parsed)

        serializer = ConsultationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(doctor_id=request.user.id)
        self.log_action(request, 'create', 'consultation', serializer.instance.id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ConsultationDetailView(APIView):
    """
    GET /api/clinic/consultations/<id>/  — all authenticated staff
    """

    def get(self, request, consultation_id):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        consultation = get_object_or_404(Consultation, id=consultation_id, visit_id__in=visit_ids)
        return Response(ConsultationSerializer(consultation).data)


class PrescriptionListView(AuditLogMixin, PaginatedListMixin, APIView):
    """
    GET  /api/clinic/prescriptions/              — all authenticated staff, scoped to clinic
    POST /api/clinic/prescriptions/              — doctor only
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('write_prescription')()]
        return super().get_permissions()

    def _clinic_consultation_ids(self, request):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        return Consultation.objects.filter(visit_id__in=visit_ids).values_list('id', flat=True)

    def get(self, request):
        consultation_id = request.query_params.get('consultation_id')
        qs = Prescription.objects.filter(consultation_id__in=self._clinic_consultation_ids(request))
        if consultation_id:
            qs = qs.filter(consultation_id=consultation_id)
        return self.paginate(qs, PrescriptionSerializer, request)

    def post(self, request):
        serializer = PrescriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        consultation_id = data['consultation_id']
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        get_object_or_404(Consultation, id=consultation_id, visit_id__in=visit_ids)

        prescription = Prescription.objects.create(
            consultation_id=consultation_id,
            prescribed_by=request.user.id,
            notes=data.get('notes', ''),
        )
        for item in data['items']:
            PrescriptionItem.objects.create(prescription_id=prescription.id, **item)

        self.log_action(request, 'create', 'prescription', prescription.id)
        return Response(PrescriptionSerializer(prescription).data, status=status.HTTP_201_CREATED)


class PrescriptionDetailView(APIView):
    """
    GET /api/clinic/prescriptions/<id>/  — all authenticated staff
    """

    def get(self, request, prescription_id):
        visit_ids = Visit.objects.for_clinic(request.user.clinic_id).values_list('id', flat=True)
        consultation_ids = Consultation.objects.filter(visit_id__in=visit_ids).values_list('id', flat=True)
        prescription = get_object_or_404(Prescription, id=prescription_id, consultation_id__in=consultation_ids)
        return Response(PrescriptionSerializer(prescription).data)
