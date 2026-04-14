"""
Lab event emitter.

Call emit_lab_event() after any TestOrder status change.
It resolves recipients, creates Notification rows (deduped via get_or_create),
then queues async delivery for each new notification.
"""
import logging

from users.models import Profile

logger = logging.getLogger(__name__)

EVENT_LAB_TEST_REQUESTED  = 'lab_test_requested'
EVENT_LAB_TEST_STARTED    = 'lab_test_started'
EVENT_LAB_TEST_COMPLETED  = 'lab_test_completed'

# Map TestOrder.status → event type
STATUS_TO_EVENT = {
    'pending':     EVENT_LAB_TEST_REQUESTED,
    'in_progress': EVENT_LAB_TEST_STARTED,
    'completed':   EVENT_LAB_TEST_COMPLETED,
}


def emit_lab_event(event_type: str, test_order, clinic_id, triggered_by_id, test_name=''):
    """
    Create Notification rows for all relevant recipients and queue delivery.
    Silently skips on any error so a notification failure never breaks the
    primary request.
    """
    try:
        from notifications.models import Notification
        from core.tasks import deliver_notification

        payload = {
            'test_order_id': str(test_order.id),
            'test_name':     test_name,
            'visit_id':      str(test_order.visit_id),
            'ordered_by':    str(test_order.ordered_by),
            'status':        test_order.status,
            'triggered_by':  str(triggered_by_id),
        }

        for recipient_id in _resolve_recipients(event_type, test_order, clinic_id):
            notif, created = Notification.objects.get_queryset().get_or_create(
                recipient_id=recipient_id,
                event_type=event_type,
                entity_id=test_order.id,
                defaults={
                    'clinic_id':   clinic_id,
                    'entity_type': 'test_order',
                    'payload':     payload,
                    'status':      'pending',
                },
            )
            if created:
                deliver_notification.delay(str(notif.id))

    except Exception:
        logger.exception(
            "emit_lab_event failed — event_type=%s test_order=%s",
            event_type, test_order.id,
        )


def _resolve_recipients(event_type: str, test_order, clinic_id):
    if event_type == EVENT_LAB_TEST_REQUESTED:
        # Notify all lab techs in the clinic
        return list(
            Profile.objects
            .for_clinic(clinic_id)
            .filter(role='lab_tech')
            .values_list('id', flat=True)
        )
    elif event_type in (EVENT_LAB_TEST_STARTED, EVENT_LAB_TEST_COMPLETED):
        # Notify the ordering doctor
        return [test_order.ordered_by]
    return []
