from django.urls import path

from .views import (
    AppointmentListView, AppointmentDetailView,
    AppointmentCancelView, AppointmentAffectedView, AppointmentReassignView,
    CheckInView, QueueListView, QueueDetailView,
    QueueCallView, QueueNoShowView, QueueReinsertView,
    QueueStartVisitView, QueueCompleteView,
    QueueReorderView, QueueHistoryView,
)

urlpatterns = [
    # Appointments — specific paths before parameterized
    path('appointments/', AppointmentListView.as_view()),
    path('appointments/affected/', AppointmentAffectedView.as_view()),
    path('appointments/<uuid:appointment_id>/', AppointmentDetailView.as_view()),
    path('appointments/<uuid:appointment_id>/cancel/', AppointmentCancelView.as_view()),
    path('appointments/<uuid:appointment_id>/reassign/', AppointmentReassignView.as_view()),

    # Queue
    path('checkin/', CheckInView.as_view()),
    path('', QueueListView.as_view()),
    path('reorder/', QueueReorderView.as_view()),
    path('<uuid:entry_id>/', QueueDetailView.as_view()),
    path('<uuid:entry_id>/call/', QueueCallView.as_view()),
    path('<uuid:entry_id>/no-show/', QueueNoShowView.as_view()),
    path('<uuid:entry_id>/reinsert/', QueueReinsertView.as_view()),
    path('<uuid:entry_id>/start-visit/', QueueStartVisitView.as_view()),
    path('<uuid:entry_id>/complete/', QueueCompleteView.as_view()),
    path('<uuid:entry_id>/history/', QueueHistoryView.as_view()),
]
