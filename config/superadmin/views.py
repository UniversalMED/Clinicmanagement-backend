import logging

import requests as http_requests
from django.conf import settings
from django.db import transaction
from django.utils.text import slugify
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.querysets import PaginatedListMixin
from users.models import Profile
from .models import Clinic
from .permissions import IsSuperAdmin
from .serializers import ClinicSerializer, OnboardClinicSerializer

logger = logging.getLogger(__name__)


class ClinicListView(PaginatedListMixin, APIView):
    """
    GET /api/superadmin/clinics/
    Returns all clinic tenants. Super admin only.
    """
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        qs = Clinic.objects.all()
        is_active = request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        return self.paginate(qs, ClinicSerializer, request)


class ClinicOnboardView(APIView):
    """
    POST /api/superadmin/clinics/onboard/

    Atomically:
      1. Creates a Clinic row.
      2. Creates a Supabase auth user for the initial admin.
      3. Creates a Profile row linking the user to the clinic as 'admin'.

    If step 2 or 3 fail, the whole operation is rolled back cleanly:
    - DB transaction rolls back the Clinic row.
    - Supabase user is deleted if the Profile insert fails.
    """
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        serializer = OnboardClinicSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            return Response(
                {'detail': 'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        service_key = settings.SUPABASE_SERVICE_ROLE_KEY.strip()

        try:
            clinic, profile = self._onboard(data, service_key)
        except _SupabaseError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'clinic':  ClinicSerializer(clinic).data,
            'admin': {
                'id':        str(profile.id),
                'email':     data['admin_email'],
                'full_name': profile.full_name,
                'role':      profile.role,
            },
        }, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def _onboard(self, data, service_key):
        # Step 1 — create clinic row (inside transaction)
        clinic = Clinic.objects.create(
            name=data['clinic_name'],
            slug=slugify(data['clinic_name']),
        )

        # Step 2 — create Supabase auth user (outside transaction atomicity,
        # but we handle rollback manually below)
        auth_user_id = self._create_supabase_user(
            email=data['admin_email'],
            password=data['admin_password'],
            service_key=service_key,
        )

        # Step 3 — create profile row (inside transaction)
        try:
            profile = Profile.objects.get_queryset().create(
                id=auth_user_id,
                clinic_id=clinic.id,
                full_name=data['admin_full_name'],
                role='admin',
            )
        except Exception as exc:
            # Profile insert failed — delete the Supabase user to avoid orphan,
            # then re-raise so @transaction.atomic rolls back the Clinic row.
            self._delete_supabase_user(auth_user_id, service_key)
            raise exc

        return clinic, profile

    # ------------------------------------------------------------------
    # Supabase helpers
    # ------------------------------------------------------------------

    def _create_supabase_user(self, email, password, service_key):
        resp = http_requests.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers={
                'apikey': service_key,
                'Authorization': f'Bearer {service_key}',
                'Content-Type': 'application/json',
            },
            json={
                'email': email,
                'password': password,
                'email_confirm': True,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            error = resp.json().get('msg') or resp.json().get('message') or resp.text
            raise _SupabaseError(f"Could not create Supabase user: {error}")
        return resp.json()['id']

    def _delete_supabase_user(self, user_id, service_key):
        try:
            http_requests.delete(
                f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers={
                    'apikey': service_key,
                    'Authorization': f'Bearer {service_key}',
                },
                timeout=10,
            )
        except Exception:
            logger.exception("Failed to delete orphaned Supabase user %s", user_id)


class _SupabaseError(Exception):
    pass
