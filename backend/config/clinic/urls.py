from django.urls import path
from .views import (
    PatientListView,
    PatientDetailView,
    VisitListView,
    VisitDetailView,
    ConsultationListView,
    ConsultationDetailView,
    PrescriptionListView,
    PrescriptionDetailView,
)

urlpatterns = [
    path('patients/', PatientListView.as_view(), name='patient-list'),
    path('patients/<uuid:patient_id>/', PatientDetailView.as_view(), name='patient-detail'),
    path('visits/', VisitListView.as_view(), name='visit-list'),
    path('visits/<uuid:visit_id>/', VisitDetailView.as_view(), name='visit-detail'),
    path('consultations/', ConsultationListView.as_view(), name='consultation-list'),
    path('consultations/<uuid:consultation_id>/', ConsultationDetailView.as_view(), name='consultation-detail'),
    path('prescriptions/', PrescriptionListView.as_view(), name='prescription-list'),
    path('prescriptions/<uuid:prescription_id>/', PrescriptionDetailView.as_view(), name='prescription-detail'),
]
