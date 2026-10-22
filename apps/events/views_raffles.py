from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.permissions import can_manage_event, management_required
from apps.core.utils import log_audit
from .forms_raffles import MotorcycleRaffleForm, RaffleInterestForm
from .models import Event, EventStatus
from .models_raffles import MotorcycleRaffle, RaffleInterest, RAFFLE_INTEREST_CONSENT
from .views import get_event_resilient


INTEREST_EVENT_STATUSES = {EventStatus.PUBLISHED, EventStatus.SALES_CLOSED, EventStatus.IN_PROGRESS}


def _public_context(request, event, form=None):
    configured = MotorcycleRaffle.objects.filter(event=event).first()
    preview = bool(configured and not configured.is_public and can_manage_event(request.user, event, "events"))
    raffle = configured if configured and (configured.is_public or preview) else None
    collecting = bool(raffle and raffle.is_public and raffle.interest_enabled and event.status in INTEREST_EVENT_STATUSES)
    registered = bool(raffle and request.user.is_authenticated and RaffleInterest.objects.filter(raffle=raffle, user=request.user).exists())
    return {"event": event, "raffle": raffle, "raffle_preview": preview,
            "interest_open": collecting, "interest_registered": registered,
            "interest_form": form or RaffleInterestForm()}


@require_GET
def raffle_detail(request, slug):
    event = get_event_resilient(slug, request)
    return render(request, "events/raffle_detail.html", _public_context(request, event))


@require_POST
@login_required(login_url="identity:login")
def raffle_interest(request, slug):
    if not request.user.is_active:
        raise PermissionDenied
    event = get_event_resilient(slug, request)
    form = RaffleInterestForm(request.POST)
    with transaction.atomic():
        raffle = get_object_or_404(MotorcycleRaffle.objects.select_for_update(), event=event, is_public=True, interest_enabled=True)
        if event.status not in INTEREST_EVENT_STATUSES:
            raise PermissionDenied("O registro de interesse não está disponível para este evento.")
        if not form.is_valid():
            return render(request, "events/raffle_detail.html", _public_context(request, event, form), status=400)
        interest, created = RaffleInterest.objects.get_or_create(
            raffle=raffle, user=request.user, defaults={"consent_text": RAFFLE_INTEREST_CONSENT},
        )
        if created:
            log_audit(actor=request.user, event_id=event.pk, action="RAFFLE_INTEREST_REGISTERED",
                      entity_type="RaffleInterest", entity_id=interest.pk, request=request)
    messages.success(request, "Interesse registrado. Nenhuma cota foi comprada ou reservada." if created else "Seu interesse já está registrado. Nenhuma cota foi comprada ou reservada.")
    return redirect("events:raffle", slug=event.slug)


@management_required
@require_http_methods(["GET", "POST"])
def raffle_management(request, event_id):
    event = request.management_event
    try:
        event_timezone = ZoneInfo(event.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        event_timezone = timezone.get_current_timezone()
    with timezone.override(event_timezone):
        # Serializing configuration per event prevents concurrent first saves
        # from creating competing one-to-one raffle records.
        with transaction.atomic():
            if request.method == "POST":
                Event.objects.select_for_update().get(pk=event.pk)
            raffle = MotorcycleRaffle.objects.filter(event=event).first() or MotorcycleRaffle(event=event)
            form = MotorcycleRaffleForm(request.POST if request.method == "POST" else None, instance=raffle)
            if request.method == "POST" and form.is_valid():
                creating = raffle.pk is None or raffle._state.adding
                saved = form.save(commit=False)
                saved.event = event
                saved.save()
                log_audit(actor=request.user, event_id=event.pk,
                          action="RAFFLE_CREATED" if creating else "RAFFLE_UPDATED",
                          entity_type="MotorcycleRaffle", entity_id=saved.pk,
                          diff={"fields": list(form.changed_data)}, request=request)
                messages.success(request, "Configuração salva. A compra de cotas continua indisponível.")
                return redirect("gestao:raffle_management", event_id=event.pk)
        interests = RaffleInterest.objects.filter(raffle=raffle).select_related("user") if not raffle._state.adding else RaffleInterest.objects.none()
        return render(request, "gestao/raffles/manage.html", {
            "event": event, "raffle": raffle, "form": form,
            "interest_count": interests.count(), "interests_page": Paginator(interests, 20).get_page(request.GET.get("page")),
        }, status=400 if request.method == "POST" else 200)
