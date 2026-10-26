from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from .rankings import get_event_rankings
from apps.core.permissions import can_manage_event
from .models import Event, EventStatus, EventScheduleItem, Route, Sponsor, LegalDocumentVersion
from apps.catalog.offers import registration_catalog
from apps.payments.gateway import MercadoPagoGateway
from .public_catalog import upcoming_events, past_events
from .social import social_metadata
from apps.mediaframes.access import available_pilot_art
from apps.mediaframes.models import FrameTemplate
from apps.mediaframes.events_config import get_event_versions, select_version

def get_event_resilient(slug, request):
    event = get_object_or_404(Event.objects.select_related("theme", "organization", "motorcycle_raffle"), slug=slug)
    if event.status == EventStatus.DRAFT and not (
        can_manage_event(request.user, event, "events") or can_manage_event(request.user, event, "content")
    ):
        raise Http404("Evento não publicado.")
    return event

def event_list(request):
    """Catálogo público de todos os eventos da Equipe Morcego."""
    return render(request, "events/list.html", {
        "active_events": upcoming_events(),
        "past_events": past_events(),
    })

@never_cache
def event_detail(request, slug):
    """Página principal e oficial do evento."""
    event = get_event_resilient(slug, request)

    now = timezone.now()
    products, active_lots, registration_offers = registration_catalog(event)

    schedule = event.schedule_items.all()[:5]
    routes = event.routes.filter(public_flag=True)
    sponsors = event.sponsors.filter(is_public=True).order_by("tier", "sort_order")
    versions = get_event_versions(event)
    selected = select_version(request.GET.get("versao"), versions)

    return render(request, "events/detail.html", {
        "event": event,
        "products": products,
        "active_lots": active_lots,
        "registration_offers": registration_offers,
        "payments_available": not MercadoPagoGateway().configuration_issues(),
        "schedule": schedule,
        "routes": routes,
        "sponsors": sponsors,
        "now": now,
        "event_versions": versions,
        "selected_version": selected,
        "ranking": get_event_rankings(event),
        "art_available": bool(available_pilot_art(request.user, event=event)),
        "social": social_metadata(request, event, selected),
    })


@require_GET
def event_ranking_snapshot(request, slug):
    event = get_event_resilient(slug, request)
    response = JsonResponse(get_event_rankings(event))
    response["Cache-Control"] = "no-store"
    return response

def event_schedule(request, slug):
    """Cronograma completo do evento."""
    event = get_event_resilient(slug, request)
    schedule = event.schedule_items.all().order_by("starts_at")
    return render(request, "events/schedule.html", {"event": event, "schedule": schedule})

def event_route(request, slug):
    """Percurso e detalhes técnicos da trilha."""
    event = get_event_resilient(slug, request)
    routes = event.routes.filter(public_flag=True).prefetch_related("checkpoints")
    return render(request, "events/route.html", {"event": event, "routes": routes})

def event_rules(request, slug):
    """Regulamento oficial e termos de responsabilidade."""
    event = get_event_resilient(slug, request)
    docs = event.legal_documents.all().order_by("-published_at")
    return render(request, "events/rules.html", {"event": event, "docs": docs})

def event_sponsors(request, slug):
    """Patrocinadores e parceiros oficiais."""
    event = get_event_resilient(slug, request)
    sponsors = event.sponsors.filter(is_public=True).order_by("tier", "sort_order")
    return render(request, "events/sponsors.html", {"event": event, "sponsors": sponsors})

@never_cache
def event_frames(request, slug):
    """
    Gerador interativo de Molduras Digitais 3D Pop-Out (Estilo Flávio Folia / NaBio).
    """
    event = get_event_resilient(slug, request)
    art_registrations = available_pilot_art(request.user, event=event)
    selected_registration = next((reg for reg in art_registrations if str(reg.pk) == request.GET.get("inscricao")), None)
    selected_registration = selected_registration or (art_registrations[0] if art_registrations else None)
    templates = FrameTemplate.objects.filter(event=event, is_active=True).order_by("-created_at")
    versions = get_event_versions(event)
    selected = select_version(request.GET.get("versao"), versions)

    return render(request, "events/frames.html", {
        "event": event,
        "templates": templates,
        "art_registrations": art_registrations,
        "selected_registration": selected_registration,
        "after_purchase": bool(selected_registration and request.GET.get("origem") == "compra"),
        "social": social_metadata(request, event, selected),
        "event_versions": versions,
        "selected_version": selected,
    })
