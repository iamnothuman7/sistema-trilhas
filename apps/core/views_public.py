from django.shortcuts import render
from django.utils import timezone
from apps.events.models import Event, EventStatus
from apps.events.views import event_detail
from apps.core.models import Organization

def home(request):
    """
    Se acessado via subdomínio de um evento (ex: 3trilhaotiangua.equipemorcego.nabio.pro),
    renderiza diretamente o Microsite Oficial Exclusivo do Evento na raiz (/).
    Caso contrário, exibe o Portal Institucional da Equipe Morcego.
    """
    if getattr(request, "is_event_subdomain", False) and getattr(request, "current_event", None):
        return event_detail(request, slug=request.current_event.slug)

    org = Organization.objects.first()

    # Eventos em destaque com inscrições abertas ou próximos
    featured_events = Event.objects.filter(
        status__in=[EventStatus.PUBLISHED, EventStatus.SALES_CLOSED, EventStatus.IN_PROGRESS]
    ).select_related("theme").order_by("starts_at")[:6]
    featured_events = list(featured_events)
    featured_sponsors = featured_events[0].sponsors.all().order_by("tier", "sort_order") if featured_events else []

    # Eventos passados / galeria histórica
    past_events = Event.objects.filter(
        status__in=[EventStatus.FINISHED, EventStatus.ARCHIVED]
    ).order_by("-ends_at")[:4]

    return render(request, "core/home.html", {
        "featured_events": featured_events,
        "featured_sponsors": featured_sponsors,
        "past_events": past_events,
        "org": org,
    })

def about(request):
    """Página institucional sobre a Equipe Morcego e sua história."""
    return render(request, "core/about.html")

def contact(request):
    """Página de contato e suporte aos participantes."""
    return render(request, "core/contact.html")

def privacy(request):
    """Página de transparência e privacidade LGPD."""
    return render(request, "core/privacy.html")
