from django.urls import path

from .views import NotificationListView, NotificationAcknowledgeView

urlpatterns = [
    path('',                             NotificationListView.as_view()),
    path('<uuid:notification_id>/acknowledge/', NotificationAcknowledgeView.as_view()),
]
