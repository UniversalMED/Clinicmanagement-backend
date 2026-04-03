import requests as http_requests
from django.shortcuts import get_object_or_404
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Profile
from .serializers import ProfileSerializer, AssignRoleSerializer, UpdateProfileSerializer, CreateUserSerializer
from .permissions import HasPermission, IsAdminOrSelf
from core.querysets import PaginatedListMixin


class CurrentUserView(APIView):
    """GET /api/users/me/ — returns the authenticated user's profile."""

    def get(self, request):
        # request.user is a JWTUser (no DB hit during auth).
        # This is the one endpoint that explicitly fetches the Profile row.
        profile = get_object_or_404(Profile.objects.get_queryset(), id=request.user.id)
        return Response(ProfileSerializer(profile).data)


class UserListView(PaginatedListMixin, APIView):
    """
    GET  /api/users/?role=<role>  — admin only, scoped to clinic
    POST /api/users/              — admin only, creates auth user + profile
    """
    permission_classes = [HasPermission.for_permission('manage_users')]

    def get(self, request):
        qs = Profile.objects.for_clinic(request.user.clinic_id)
        role = request.query_params.get('role')
        if role:
            qs = qs.filter(role=role)
        return self.paginate(qs, ProfileSerializer, request)

    def post(self, request):
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            return Response(
                {'detail': 'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to create users.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        service_key = settings.SUPABASE_SERVICE_ROLE_KEY.strip()

        # Step 1 — create the auth user via Supabase Admin API
        resp = http_requests.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers={
                'apikey': service_key,
                'Authorization': f'Bearer {service_key}',
                'Content-Type': 'application/json',
            },
            json={
                'email': data['email'],
                'password': data['password'],
                'email_confirm': True,
            },
            timeout=10,
        )

        if resp.status_code != 200:
            error = resp.json().get('msg') or resp.json().get('message') or resp.text
            return Response({'detail': f'Supabase error: {error}'}, status=status.HTTP_400_BAD_REQUEST)

        auth_user_id = resp.json()['id']

        # Step 2 — create the profile row; if this fails, delete the auth user to avoid orphans
        try:
            profile = Profile.objects.get_queryset().create(
                id=auth_user_id,
                clinic_id=request.user.clinic_id,
                full_name=data['full_name'],
                role=data['role'],
            )
            profile.refresh_from_db()
        except Exception as db_exc:
            http_requests.delete(
                f"{settings.SUPABASE_URL}/auth/v1/admin/users/{auth_user_id}",
                headers={
                    'apikey': service_key,
                    'Authorization': f'Bearer {service_key}',
                },
                timeout=10,
            )
            raise db_exc

        return Response(ProfileSerializer(profile).data, status=status.HTTP_201_CREATED)


class AssignRoleView(APIView):
    """
    PATCH /api/users/<user_id>/role/
    Admin only. Scoped to the admin's clinic to prevent cross-clinic role assignment.
    """
    permission_classes = [HasPermission.for_permission('manage_users')]

    def patch(self, request, user_id):
        profile = get_object_or_404(
            Profile.objects.for_clinic(request.user.clinic_id), id=user_id
        )
        serializer = AssignRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile.role = serializer.validated_data['role']
        profile.save(update_fields=['role'])
        return Response(ProfileSerializer(profile).data)


class UpdateUserView(APIView):
    """
    PATCH /api/users/<user_id>/
    Admins can update any user in their clinic; users can only update themselves.
    """
    permission_classes = [IsAdminOrSelf]

    def patch(self, request, user_id):
        profile = get_object_or_404(
            Profile.objects.for_clinic(request.user.clinic_id), id=user_id
        )
        self.check_object_permissions(request, profile)
        serializer = UpdateProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(serializer.instance).data)
