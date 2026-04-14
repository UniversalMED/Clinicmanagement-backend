"""
Management command: auto_timeout_calls

Marks QueueEntry records with status='called' as 'no_show' when their
call_timeout_at has passed. Run this periodically (e.g. every minute via cron).

Example cron entry (every minute):
    * * * * * /path/to/venv/bin/python /path/to/manage.py auto_timeout_calls
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from patient_flow.models import QueueEntry, transition


class Command(BaseCommand):
    help = 'Mark called patients as no-show when their call timeout has expired.'

    def handle(self, *args, **options):
        now = timezone.now()

        # Use get_queryset() to bypass ClinicScopedManager's .all() guard —
        # this is intentionally cross-clinic (admin-level system action).
        expired = QueueEntry.objects.get_queryset().filter(
            status='called',
            call_timeout_at__lte=now,
        )

        count = 0
        errors = 0
        for entry in expired:
            try:
                with transaction.atomic():
                    transition(
                        entry, 'no_show',
                        changed_by=None,   # NULL = system action
                        reason='call_timeout_expired',
                        metadata={'call_timeout_at': entry.call_timeout_at.isoformat()},
                    )
                count += 1
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f'Failed to timeout entry {entry.id}: {exc}')
                errors += 1

        self.stdout.write(
            self.style.SUCCESS(f'Timed out {count} entries.') +
            (f' Errors: {errors}' if errors else '')
        )
