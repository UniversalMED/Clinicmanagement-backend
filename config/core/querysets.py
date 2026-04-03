"""
Query safety primitives.

ClinicScopedManager
  - Adds a for_clinic(clinic_id) shortcut to any model with a clinic_id field.
  - Makes the scoping intent explicit at the call site.
  - Overrides all() to raise an error — no model may be queried without a
    clinic scope. This prevents accidental data leaks if a new view forgets
    to add the filter.

PaginatedListMixin
  - Adds paginate() to any APIView subclass.
  - Uses ClinicPagination (max 100 rows per page, default 25).
  - Call self.paginate(queryset, serializer_class, request) in any list view.
"""

from django.db import models
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class ClinicPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


# ---------------------------------------------------------------------------
# Clinic-scoped manager
# ---------------------------------------------------------------------------

class ClinicScopedQuerySet(models.QuerySet):
    def for_clinic(self, clinic_id):
        return self.filter(clinic_id=clinic_id)


class ClinicScopedManager(models.Manager):
    """
    Drop-in replacement for models.Manager on any model with a clinic_id field.

    Usage in views:
        qs = Patient.objects.for_clinic(request.user.clinic_id)

    Calling .all() without a clinic scope raises RuntimeError to catch
    accidental unscoped queries at development/test time.
    """

    def get_queryset(self):
        return ClinicScopedQuerySet(self.model, using=self._db)

    def for_clinic(self, clinic_id):
        return self.get_queryset().for_clinic(clinic_id)

    def all(self):
        raise RuntimeError(
            f"{self.model.__name__}.objects.all() is not allowed. "
            "Use .for_clinic(clinic_id) to scope the query. "
            "If you genuinely need an unscoped query (e.g. admin commands), "
            "call .get_queryset() directly."
        )


# ---------------------------------------------------------------------------
# Pagination mixin for APIView
# ---------------------------------------------------------------------------

class PaginatedListMixin:
    """
    Adds paginated list support to APIView subclasses.

    Usage:
        class MyListView(PaginatedListMixin, APIView):
            def get(self, request):
                qs = MyModel.objects.for_clinic(request.user.clinic_id)
                return self.paginate(qs, MySerializer, request)
    """
    pagination_class = ClinicPagination

    @property
    def paginator(self):
        if not hasattr(self, '_paginator'):
            self._paginator = self.pagination_class()
        return self._paginator

    def paginate(self, queryset, serializer_class, request):
        page = self.paginator.paginate_queryset(queryset, request, view=self)
        data = serializer_class(page, many=True).data
        return self.paginator.get_paginated_response(data)
