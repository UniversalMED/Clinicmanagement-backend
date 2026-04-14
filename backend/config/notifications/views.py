from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.querysets import PaginatedListMixin
from .models import Notification
from .serializers import NotificationSerializer


class NotificationListView(PaginatedListMixin, APIView):
    """
    GET /api/notifications/
    Returns pending notifications for the authenticated user.
    Accepts ?status=pending|delivered|failed (default: pending).
    """

    def get(self, request):
        status_filter = request.query_params.get('status', 'pending')
        qs = (
            Notification.objects
            .for_clinic(request.user.clinic_id)
            .filter(recipient_id=request.user.id, status=status_filter)
        )
        return self.paginate(qs, NotificationSerializer, request)


class NotificationAcknowledgeView(APIView):
    """
    POST /api/notifications/<id>/acknowledge/
    Marks a notification as delivered. Only the recipient may acknowledge.
    """

    def post(self, request, notification_id):
        notif = get_object_or_404(
            Notification.objects.for_clinic(request.user.clinic_id),
            id=notification_id,
            recipient_id=request.user.id,
        )
        if notif.status == 'delivered':
            return Response(NotificationSerializer(notif).data)

        notif.status = 'delivered'
        notif.delivered_at = timezone.now()
        notif.save(update_fields=['status', 'delivered_at'])
        return Response(NotificationSerializer(notif).data)
