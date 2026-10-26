from django.shortcuts import render
from apps.events.public_catalog import upcoming_events, past_events
from apps.events.views import event_detail
from apps.core.models import Organization
from apps.events.social import social_metadata

def home(request):
    """
    Se acessado via subdomínio de um evento (ex: 3trilhaotiangua.equipemorcego.nabio.pro),
    renderiza diretamente o Microsite Oficial Exclusivo do Evento na raiz (/).
    Caso contrário, exibe o Portal Institucional da Equipe Morcego.
    """
    if getattr(request, "is_event_subdomain", False) and getattr(request, "current_event", None):
        return event_detail(request, slug=request.current_event.slug)

    org = Organization.objects.first()
    featured = list(upcoming_events()[:6])

    return render(request, "core/home.html", {
        "featured_events": featured,
        "social": social_metadata(request, featured[0]) if featured else None,
        "past_events": past_events()[:4],
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
