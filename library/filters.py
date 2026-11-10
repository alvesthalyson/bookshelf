from django.db.models import F
from rest_framework.filters import OrderingFilter


class NullsLastOrderingFilter(OrderingFilter):
    """PostgreSQL puts NULLs FIRST when sorting DESC. Push them to the end instead."""

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset
        fields = []
        for name in ordering:
            if name.startswith("-"):
                fields.append(F(name[1:]).desc(nulls_last=True))
            else:
                fields.append(F(name).asc(nulls_last=True))
        return queryset.order_by(*fields)
