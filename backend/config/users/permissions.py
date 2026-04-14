"""
Permission system — single source of truth for role-based access control.

ROLE_PERMISSIONS is the canonical permission matrix.
To audit who can do what, read this file only.

Read access (GET endpoints) is granted to all authenticated users by default
via Django REST Framework's IsAuthenticated — it does not need to appear here.
Only write/mutate actions need explicit grants.

Usage in views:
    from users.permissions import HasPermission

    class MyView(APIView):
        def get_permissions(self):
            if self.request.method == 'POST':
                return [HasPermission.for_permission('write_patient')()]
            return super().get_permissions()
"""

from rest_framework.permissions import BasePermission

# ---------------------------------------------------------------------------
# Permission matrix — edit this to change who can do what.
# ---------------------------------------------------------------------------
ROLE_PERMISSIONS: dict[str, list[str]] = {
    'admin': [
        'manage_users',           # list users, create users, assign roles
        'manage_lab_catalogue',   # create and update lab test types
        'write_patient',          # create and update patients
        'write_visit',            # create visits
        'update_visit',           # update visit status / assigned doctor
        'process_lab_order',      # update lab order status, assign technician
        'view_audit_log',         # read audit trail for the clinic
        'manage_billing',         # create invoices, add/remove items, finalize
        'void_invoice',           # void a finalized invoice
        'manage_appointments',    # book, update, cancel appointments
        'reassign_appointment',   # mark doctor absent, reassign appointments
        'manage_queue',           # check-in, call, no-show, reinsert
        'start_visit_from_queue', # start and complete visits via queue
        'reorder_queue',          # bulk reorder the waiting queue
    ],
    'doctor': [
        'update_visit',           # update visit status / assigned doctor
        'write_consultation',     # create consultations
        'order_lab_test',         # create lab test orders
        'write_prescription',     # create prescriptions
        'start_visit_from_queue', # start and complete visits via queue
    ],
    'lab_tech': [
        'process_lab_order',      # update lab order status, assign technician
        'write_lab_result',       # record test results
    ],
    'receptionist': [
        'write_patient',          # create and update patients
        'write_visit',            # create visits
        'update_visit',           # update visit status / assigned doctor
        'manage_billing',         # create invoices, add/remove items, finalize
        'manage_appointments',    # book, update, cancel appointments
        'manage_queue',           # check-in, call, no-show, reinsert
    ],
}


# ---------------------------------------------------------------------------
# DRF permission classes
# ---------------------------------------------------------------------------

class HasPermission(BasePermission):
    """
    Checks ROLE_PERMISSIONS to decide if the authenticated user's role
    grants the required permission.

    Usage:
        HasPermission.for_permission('write_patient')
    """
    required_permission: str = ''

    @classmethod
    def for_permission(cls, permission: str) -> type:
        return type('HasPermission', (cls,), {'required_permission': permission})

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        role_permissions = ROLE_PERMISSIONS.get(request.user.role, [])
        return self.required_permission in role_permissions


class IsAdminOrSelf(BasePermission):
    """
    Object-level permission for profile updates.
    Admins can update any profile in their clinic.
    Non-admins can only update their own profile.

    Kept separate from ROLE_PERMISSIONS because it requires object-level
    context (the target profile's id) that a flat permission string cannot
    express.
    """

    def has_permission(self, request, view) -> bool:
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj) -> bool:
        return request.user.role == 'admin' or obj.id == request.user.id


# ---------------------------------------------------------------------------
# Kept for reference — no longer used directly in views.
# Use HasPermission.for_permission(...) instead.
# ---------------------------------------------------------------------------

class HasRole(BasePermission):
    """Legacy inline role gate — superseded by HasPermission."""
    required_roles: tuple = ()

    @classmethod
    def for_roles(cls, *roles):
        return type('HasRole', (cls,), {'required_roles': roles})

    def has_permission(self, request, view) -> bool:
        return (
            request.user.is_authenticated
            and request.user.role in self.required_roles
        )
