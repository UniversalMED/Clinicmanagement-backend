from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/users/', include('users.urls')),
    path('api/clinic/', include('clinic.urls')),
    path('api/lab/', include('lab.urls')),
    path('api/audit/', include('audit.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/queue/', include('patient_flow.urls')),
    path('api/notifications/', include('notifications.urls')),
    path('api/superadmin/',   include('superadmin.urls')),
]
