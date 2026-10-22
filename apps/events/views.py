from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from .models import Event, EventStatus, EventScheduleItem, Route, Sponsor, LegalDocumentVersion
from apps.catalog.models import Product, Lot
from apps.mediaframes.models import FrameTemplate

def event_list(request):
    """Catálogo público de todos os eventos da Equipe Morcego."""
    active_events = Event.objects.filter(
        status__in=[EventStatus.PUBLISHED, EventStatus.SALES_CLOSED, EventStatus.IN_PROGRESS]
    ).select_related("theme").order_by("starts_at")

    past_events = Event.objects.filter(
        status__in=[EventStatus.FINISHED, EventStatus.ARCHIVED]
    ).order_by("-ends_at")

    return render(request, "events/list.html", {
        "active_events": active_events,
        "past_events": past_events,
    })

def event_detail(request, slug):
    """Página principal e oficial do evento."""
    event = get_object_or_404(
        Event.objects.select_related("theme", "organization"),
        slug=slug
    )

    now = timezone.now()
    products = Product.objects.filter(event=event, active=True).prefetch_related("lots", "variants")
    active_lots = Lot.objects.filter(event=event, active=True).order_by("priority", "opens_at")

    schedule = event.schedule_items.all()[:5]
    routes = event.routes.filter(public_flag=True)
    sponsors = event.sponsors.all().order_by("tier", "sort_order")

    return render(request, "events/detail.html", {
        "event": event,
        "products": products,
        "active_lots": active_lots,
        "schedule": schedule,
        "routes": routes,
        "sponsors": sponsors,
        "now": now,
    })

def event_schedule(request, slug):
    """Cronograma completo do evento."""
    event = get_object_or_404(Event.objects.select_related("theme"), slug=slug)
    schedule = event.schedule_items.all().order_by("starts_at")
    return render(request, "events/schedule.html", {"event": event, "schedule": schedule})

def event_route(request, slug):
    """Percurso e detalhes técnicos da trilha."""
    event = get_object_or_404(Event.objects.select_related("theme"), slug=slug)
    routes = event.routes.filter(public_flag=True).prefetch_related("checkpoints")
    return render(request, "events/route.html", {"event": event, "routes": routes})

def event_rules(request, slug):
    """Regulamento oficial e termos de responsabilidade."""
    event = get_object_or_404(Event.objects.select_related("theme"), slug=slug)
    docs = event.legal_documents.all().order_by("-published_at")
    return render(request, "events/rules.html", {"event": event, "docs": docs})

def event_sponsors(request, slug):
    """Patrocinadores e parceiros oficiais."""
    event = get_object_or_404(Event.objects.select_related("theme"), slug=slug)
    sponsors = event.sponsors.all().order_by("tier", "sort_order")
    return render(request, "events/sponsors.html", {"event": event, "sponsors": sponsors})

def event_frames(request, slug):
    """
    Gerador interativo de Molduras Digitais (Canvas 2D).
    Processamento 100% no navegador do piloto, sem tráfego de fotos pessoais para o servidor.
    """
    event = get_object_or_404(Event.objects.select_related("theme"), slug=slug)
    templates = FrameTemplate.objects.filter(event=event, is_active=True).order_by("-created_at")

    return render(request, "events/frames.html", {
        "event": event,
        "templates": templates,
    })
