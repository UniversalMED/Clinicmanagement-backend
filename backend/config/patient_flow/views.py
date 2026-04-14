from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max
from django.shortcuts import get_object_or_404

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.mixins import AuditLogMixin
from clinic.models import Patient, Visit
from core.querysets import PaginatedListMixin
from users.permissions import HasPermission

from .models import (
    Appointment, QueueEntry, QueueStateAudit,
    VALID_TRANSITIONS, transition,
)
from .services import (
    QueueError, move_to_waiting, compact_waiting_positions,
    call_patient, mark_no_show, reinsert_patient,
    start_visit, complete_visit,
)
from .serializers import (
    AppointmentSerializer, CreateAppointmentSerializer,
    UpdateAppointmentSerializer, CancelAppointmentSerializer,
    AppointmentAffectedSerializer, AppointmentReassignSerializer,
    QueueEntrySerializer, CheckInSerializer,
    NoShowSerializer, ReinsertSerializer,
    ReorderSerializer, QueueStateAuditSerializer,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_queue_entry(clinic_id, entry_id, required_status=None):
    """Fetch a clinic-scoped QueueEntry, optionally asserting its status."""
    qs = QueueEntry.objects.for_clinic(clinic_id)
    if required_status:
        qs = qs.filter(status=required_status)
    return get_object_or_404(qs, id=entry_id)




# ---------------------------------------------------------------------------
# Appointment views
# ---------------------------------------------------------------------------

class AppointmentListView(AuditLogMixin, PaginatedListMixin, APIView):
    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasPermission.for_permission('manage_appointments')()]
        return super().get_permissions()

    def get(self, request):
        qs = Appointment.objects.for_clinic(request.user.clinic_id)

        # Filters
        appt_status = request.query_params.get('status')
        if appt_status:
            qs = qs.filter(status=appt_status)

        appt_date = request.query_params.get('date')
        if appt_date:
            qs = qs.filter(scheduled_at__date=appt_date)

        doctor_id = request.query_params.get('doctor_id')
        if doctor_id:
            qs = qs.filter(doctor_id=doctor_id)

        return self.paginate(qs, AppointmentSerializer, request)

    def post(self, request):
        ser = CreateAppointmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # Verify patient belongs to this clinic
        get_object_or_404(Patient.objects.for_clinic(request.user.clinic_id), id=d['patient_id'])

        appt = Appointment.objects.create(
            clinic_id=request.user.clinic_id,
            patient_id=d['patient_id'],
            doctor_id=d.get('doctor_id'),
            scheduled_at=d['scheduled_at'],
            duration_minutes=d.get('duration_minutes', 30),
            type=d['type'],
            notes=d.get('notes', ''),
        )
        self.log_action(request, 'create', 'appointment', appt.id)
        return Response(AppointmentSerializer(appt).data, status=status.HTTP_201_CREATED)


class AppointmentDetailView(AuditLogMixin, APIView):
    def get_permissions(self):
        if self.request.method in ('PATCH', 'PUT'):
            return [HasPermission.for_permission('manage_appointments')()]
        return super().get_permissions()

    def _get_appt(self, request, appointment_id):
        return get_object_or_404(
            Appointment.objects.for_clinic(request.user.clinic_id),
            id=appointment_id,
        )

    def get(self, request, appointment_id):
        return Response(AppointmentSerializer(self._get_appt(request, appointment_id)).data)

    def patch(self, request, appointment_id):
        appt = self._get_appt(request, appointment_id)
        if appt.status in ('cancelled', 'rescheduled'):
            return Response(
                {'detail': 'Cannot update a cancelled or rescheduled appointment.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = UpdateAppointmentSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        for field, value in d.items():
            setattr(appt, field, value)

        if 'scheduled_at' in d:
            appt.status = 'rescheduled'

        appt.save()
        self.log_action(request, 'update', 'appointment', appt.id)
        return Response(AppointmentSerializer(appt).data)


class AppointmentCancelView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('manage_appointments')()]

    def post(self, request, appointment_id):
        appt = get_object_or_404(
            Appointment.objects.for_clinic(request.user.clinic_id),
            id=appointment_id,
        )
        if appt.status == 'cancelled':
            return Response({'detail': 'Appointment is already cancelled.'}, status=status.HTTP_400_BAD_REQUEST)

        ser = CancelAppointmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        appt.status = 'cancelled'
        appt.cancelled_at = timezone.now()
        appt.cancelled_by = request.user.id
        appt.cancel_reason = ser.validated_data['cancel_reason']
        appt.save()

        self.log_action(request, 'update', 'appointment', appt.id)
        return Response(AppointmentSerializer(appt).data)


class AppointmentAffectedView(AuditLogMixin, APIView):
    """
    Mark all active appointments for a doctor on a given date as 'affected'
    (e.g. due to doctor absence). Does NOT auto-move patients.
    Staff must resolve each affected appointment individually.
    """
    def get_permissions(self):
        return [HasPermission.for_permission('reassign_appointment')()]

    def post(self, request):
        ser = AppointmentAffectedSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        affected = Appointment.objects.for_clinic(request.user.clinic_id).filter(
            doctor_id=d['doctor_id'],
            scheduled_at__date=d['date'],
            status='active',
        )
        count = affected.update(status='affected')
        self.log_action(request, 'update', 'appointment', d['doctor_id'])
        return Response({'affected_count': count, 'reason': d['reason']})


class AppointmentReassignView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('reassign_appointment')()]

    def post(self, request, appointment_id):
        appt = get_object_or_404(
            Appointment.objects.for_clinic(request.user.clinic_id),
            id=appointment_id,
        )
        ser = AppointmentReassignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        new_doctor_id = ser.validated_data['new_doctor_id']
        appt.doctor_id = new_doctor_id
        if appt.status == 'affected':
            appt.status = 'active'
        appt.save()

        # Also update any active queue entry linked to this appointment
        QueueEntry.objects.for_clinic(request.user.clinic_id).filter(
            appointment_id=appt.id,
        ).exclude(status__in=['completed', 'no_show']).update(
            assigned_doctor_id=new_doctor_id
        )

        self.log_action(request, 'update', 'appointment', appt.id)
        return Response(AppointmentSerializer(appt).data)


# ---------------------------------------------------------------------------
# Queue views
# ---------------------------------------------------------------------------

class CheckInView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('manage_queue')()]

    def post(self, request):
        ser = CheckInSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        clinic_id = request.user.clinic_id
        appointment_id = d.get('appointment_id')
        patient_id = d.get('patient_id')
        appointment = None
        assigned_doctor_id = None
        scheduled_at = None
        is_late = False

        if appointment_id:
            appointment = get_object_or_404(
                Appointment.objects.for_clinic(clinic_id),
                id=appointment_id,
                status='active',
            )
            patient_id = appointment.patient_id
            assigned_doctor_id = appointment.doctor_id
            scheduled_at = appointment.scheduled_at

            grace_mins = getattr(settings, 'QUEUE_GRACE_PERIOD_MINUTES', 15)
            is_late = timezone.now() > (appointment.scheduled_at + timedelta(minutes=grace_mins))
        else:
            # Walk-in — verify patient belongs to this clinic
            get_object_or_404(Patient.objects.for_clinic(clinic_id), id=patient_id)

        # Guard: one active entry per patient
        duplicate = QueueEntry.objects.for_clinic(clinic_id).filter(
            patient_id=patient_id,
        ).exclude(status__in=['completed', 'no_show']).first()
        if duplicate:
            return Response(
                {'detail': 'Patient already has an active queue entry.'},
                status=status.HTTP_409_CONFLICT,
            )

        grace_period_ends_at = None
        if appointment:
            grace_mins = getattr(settings, 'QUEUE_GRACE_PERIOD_MINUTES', 15)
            grace_period_ends_at = appointment.scheduled_at + timedelta(minutes=grace_mins)

        with transaction.atomic():
            entry = QueueEntry.objects.create(
                clinic_id=clinic_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                status='checked_in',
                entry_type='appointment' if appointment else 'walk_in',
                assigned_doctor_id=assigned_doctor_id,
                scheduled_at=scheduled_at,
                checked_in_at=timezone.now(),
                grace_period_ends_at=grace_period_ends_at,
            )
            QueueStateAudit.objects.create(
                queue_entry_id=entry.id,
                clinic_id=clinic_id,
                patient_id=patient_id,
                previous_status=None,
                new_status='checked_in',
                changed_by=request.user.id,
                change_reason='patient_checked_in',
                metadata={
                    'entry_type': entry.entry_type,
                    'appointment_id': str(appointment_id) if appointment_id else None,
                },
            )
            move_to_waiting(entry, request.user.id, is_late)

        self.log_action(request, 'create', 'queue_entry', entry.id)
        return Response(QueueEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class QueueListView(PaginatedListMixin, APIView):
    def get(self, request):
        qs = QueueEntry.objects.for_clinic(request.user.clinic_id)

        status_filter = request.query_params.get('status', 'waiting')
        if status_filter:
            statuses = [s.strip() for s in status_filter.split(',')]
            qs = qs.filter(status__in=statuses)

        doctor_id = request.query_params.get('doctor_id')
        if doctor_id:
            qs = qs.filter(assigned_doctor_id=doctor_id)

        # FCFS: order by queue_position (waiting), then by checked_in_at (first-come-first-served)
        qs = qs.order_by('queue_position', 'checked_in_at', 'created_at')

        return self.paginate(qs, QueueEntrySerializer, request)


class QueueDetailView(APIView):
    def get(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id)
        return Response(QueueEntrySerializer(entry).data)


class QueueCallView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('manage_queue')()]

    def post(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id, required_status='waiting')
        call_patient(entry, request.user.id)
        self.log_action(request, 'update', 'queue_entry', entry.id)
        return Response(QueueEntrySerializer(entry).data)


class QueueNoShowView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('manage_queue')()]

    def post(self, request, entry_id):
        entry = get_object_or_404(
            QueueEntry.objects.for_clinic(request.user.clinic_id).filter(
                status__in=['waiting', 'called']
            ),
            id=entry_id,
        )
        ser = NoShowSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        mark_no_show(entry, request.user.id, ser.validated_data['reason'])
        self.log_action(request, 'update', 'queue_entry', entry.id)
        return Response(QueueEntrySerializer(entry).data)


class QueueReinsertView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('manage_queue')()]

    def post(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id, required_status='no_show')
        ser = ReinsertSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reinsert_patient(entry, request.user.id, ser.validated_data['reason'])
        self.log_action(request, 'update', 'queue_entry', entry.id)
        return Response(QueueEntrySerializer(entry).data)


class QueueStartVisitView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('start_visit_from_queue')()]

    def post(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id, required_status='called')
        entry, visit = start_visit(entry, request.user.clinic_id, request.user.id)
        self.log_action(request, 'create', 'visit', visit.id)
        return Response({
            'queue_entry': QueueEntrySerializer(entry).data,
            'visit_id': str(visit.id),
        }, status=status.HTTP_201_CREATED)


class QueueCompleteView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('start_visit_from_queue')()]

    def post(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id, required_status='in_progress')
        complete_visit(entry, request.user.clinic_id, request.user.id)
        self.log_action(request, 'update', 'queue_entry', entry.id)
        return Response(QueueEntrySerializer(entry).data)


class QueueReorderView(AuditLogMixin, APIView):
    def get_permissions(self):
        return [HasPermission.for_permission('reorder_queue')()]

    def post(self, request):
        ser = ReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        positions = ser.validated_data['positions']

        clinic_id = request.user.clinic_id
        entry_ids = [str(item['id']) for item in positions]

        # Fetch and validate all entries are waiting and in this clinic
        entries_qs = QueueEntry.objects.for_clinic(clinic_id).filter(
            id__in=entry_ids,
            status='waiting',
        )
        entries_map = {str(e.id): e for e in entries_qs}

        if len(entries_map) != len(entry_ids):
            return Response(
                {'detail': 'One or more entries not found or not in waiting status.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for item in positions:
                entry = entries_map[str(item['id'])]
                old_pos = entry.queue_position
                new_pos = item['queue_position']
                if old_pos != new_pos:
                    QueueStateAudit.objects.create(
                        queue_entry_id=entry.id,
                        clinic_id=clinic_id,
                        patient_id=entry.patient_id,
                        previous_status='waiting',
                        new_status='waiting',
                        changed_by=request.user.id,
                        change_reason='staff_reorder',
                        metadata={'old_position': old_pos, 'new_position': new_pos},
                    )
                    entry.queue_position = new_pos
                    entry.save()

        # Bulk action — individual audit records written per entry via QueueStateAudit above
        updated_queue = QueueEntry.objects.for_clinic(clinic_id).filter(
            status='waiting'
        ).order_by('queue_position')
        return Response(QueueEntrySerializer(updated_queue, many=True).data)


class QueueHistoryView(APIView):
    def get(self, request, entry_id):
        entry = _get_queue_entry(request.user.clinic_id, entry_id)
        history = QueueStateAudit.objects.filter(
            queue_entry_id=entry_id,
            clinic_id=request.user.clinic_id,
        ).order_by('created_at')
        return Response(QueueStateAuditSerializer(history, many=True).data)
