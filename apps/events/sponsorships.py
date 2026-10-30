"""Sponsor balances, brand synchronization and scoped access."""
from django.db.models import BigIntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.permissions import accessible_events, can_manage_event
from .models import Sponsor, SponsorProfile

BRAND_FIELDS = ("name", "website", "instagram_url", "whatsapp_number", "logo_image", "logo_preview", "logo_preview_width", "logo_preview_height")


def brand_values(instance):
    return {field: (getattr(instance, field).name if field in {"logo_image", "logo_preview"} else getattr(instance, field)) or
            (None if field in {"logo_preview_width", "logo_preview_height"} else "") for field in BRAND_FIELDS}


def ensure_profile(sponsor):
    if sponsor.profile_id is None:
        sponsor.profile = SponsorProfile.objects.create(organization_id=sponsor.event.organization_id, **brand_values(sponsor))
        sponsor.save(update_fields=["profile"])
    return sponsor.profile


def sync_profile(profile):
    Sponsor.objects.filter(profile=profile).update(**brand_values(profile))


def available_profiles(user, action="sponsors", include_archived=False):
    rows=SponsorProfile.objects.filter(organization_id__in=accessible_events(user, action).values("organization_id"))
    return rows if include_archived else rows.filter(archived_at__isnull=True)


def can_edit_profile(user, profile):
    # An event editor must not change branding on events outside their scope.
    allowed = accessible_events(user, "content")
    return (allowed.filter(organization_id=profile.organization_id).exists() and
            not profile.participations.exclude(event__in=allowed).exists())


def sponsorships_with_balances(queryset=None):
    if queryset is None:
        queryset = Sponsor.objects.all()
    return queryset.select_related("profile", "event").annotate(
        received_cents=Coalesce(Sum("payments__amount_cents", filter=Q(payments__reversed_at__isnull=True)), Value(0), output_field=BigIntegerField()),
    )


def decorate_balance(sponsor):
    if not hasattr(sponsor, "received_cents"):
        sponsor.received_cents = sponsor.payments.filter(reversed_at__isnull=True).aggregate(total=Sum("amount_cents"))["total"] or 0
    agreed = sponsor.agreed_amount_cents
    sponsor.balance_cents = None if agreed is None else agreed - sponsor.received_cents
    sponsor.is_overdue = bool(sponsor.balance_cents and sponsor.due_at and sponsor.due_at < timezone.localdate())
    if agreed is None:
        sponsor.payment_status, sponsor.payment_label = "undefined", "Valor a definir"
    elif agreed == 0:
        sponsor.payment_status, sponsor.payment_label = "support", "Apoio sem cobrança"
    elif sponsor.balance_cents == 0:
        sponsor.payment_status, sponsor.payment_label = "paid", "Quitado"
    elif sponsor.is_overdue:
        sponsor.payment_status, sponsor.payment_label = "overdue", "Em atraso"
    elif sponsor.received_cents:
        sponsor.payment_status, sponsor.payment_label = "partial", "Pago parcialmente"
    else:
        sponsor.payment_status, sponsor.payment_label = "pending", "A receber"
    sponsor.paid_percent = min(100, sponsor.received_cents * 100 // agreed) if agreed else 0
    return sponsor


def sponsor_totals(sponsors):
    sponsors=[s for s in sponsors if not s.archived_at]
    return {
        "agreed_cents": sum(s.agreed_amount_cents or 0 for s in sponsors),
        "received_cents": sum(s.received_cents for s in sponsors),
        "balance_cents": sum(s.balance_cents or 0 for s in sponsors),
        "undefined_count": sum(s.agreed_amount_cents is None for s in sponsors),
        "overdue_count": sum(s.is_overdue for s in sponsors),
    }
