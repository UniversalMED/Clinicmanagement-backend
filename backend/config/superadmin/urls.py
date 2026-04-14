from django.urls import path

from .views import ClinicListView, ClinicOnboardView

urlpatterns = [
    path('clinics/',          ClinicListView.as_view()),
    path('clinics/onboard/',  ClinicOnboardView.as_view()),
]
