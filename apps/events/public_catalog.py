"""Public calendar shared by the institutional site and the event directory."""
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from .models import Event, EventStatus


VISIBLE_ACTIVE_STATUSES = (
    EventStatus.PUBLISHED, EventStatus.SALES_CLOSED, EventStatus.IN_PROGRESS,
)


def upcoming_events():
    now = timezone.now()
    return (
        Event.objects.filter(status__in=VISIBLE_ACTIVE_STATUSES, ends_at__gte=now)
        .select_related("theme")
        .annotate(calendar_priority=Case(
            When(starts_at__lte=now, then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        ))
        .order_by("calendar_priority", "starts_at", "title")
    )


def past_events():
    return (
        Event.objects.filter(
            Q(status__in=(EventStatus.FINISHED, EventStatus.ARCHIVED))
            | Q(status__in=VISIBLE_ACTIVE_STATUSES, ends_at__lt=timezone.now())
        ).select_related("theme").order_by("-ends_at", "title")
    )
