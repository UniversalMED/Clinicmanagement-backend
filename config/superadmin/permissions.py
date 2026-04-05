from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Grants access only to users whose JWT carries user_role: super_admin."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'super_admin'
        )
