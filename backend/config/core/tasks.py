"""
Celery tasks for async notification delivery.
"""
import logging

from celery import shared_task
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def deliver_notification(self, notification_id: str):
    """
    Mark a notification as delivered.
    Retries up to 5 times (60s apart) on failure.
    After max retries, marks as failed.
    """
    from notifications.models import Notification

    try:
        notif = Notification.objects.get_queryset().get(id=notification_id)

        if notif.status == 'delivered':
            return  # idempotent

        # Wire a real push/websocket/email channel here when ready.
        # For now: mark delivered immediately (in-app polling model).
        notif.status = 'delivered'
        notif.delivered_at = timezone.now()
        notif.save(update_fields=['status', 'delivered_at'])

        logger.info("Notification delivered: %s", notification_id)

    except Exception as exc:
        Notification.objects.get_queryset().filter(id=notification_id).update(
            retry_count=models.F('retry_count') + 1
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            Notification.objects.get_queryset().filter(id=notification_id).update(
                status='failed'
            )
            logger.error("Notification permanently failed: %s", notification_id)
