from django.http import Http404
from django.utils.cache import patch_vary_headers
from apps.events.models import Event, EventStatus
from apps.core.permissions import can_manage_event


class HtmxNavigationMiddleware:
    """Keep login and successful POST redirects as complete navigations."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request") == "true":
            if response.status_code in (301, 302, 303, 307, 308) and response.get("Location"):
                target = response["Location"]
                response.status_code = 200
                response.content = b""
                del response["Location"]
                response["HX-Redirect"] = target
                response["Cache-Control"] = "no-store"
        patch_vary_headers(response, ["HX-Request"])
        return response


class EventSubdomainMiddleware:
    """
    Detecta subdomínios de eventos (ex: 3trilhaotiangua.equipemorcego.nabio.pro)
    e injeta `request.current_event` e `request.is_event_subdomain = True`.
    """
    BASE_DOMAINS = (
        ".equipemorcego.nabio.pro",
        ".localhost",
        ".127.0.0.1.nip.io",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_event = None
        request.is_event_subdomain = False

        host = request.get_host().split(":")[0].lower().strip()
        subdomain = None

        for base in self.BASE_DOMAINS:
            if host.endswith(base):
                prefix = host[: -len(base)].strip(".")
                if prefix and prefix not in ("www", "admin", "gestao", "api"):
                    subdomain = prefix
                break

        if subdomain:
            events = Event.objects.select_related("theme", "organization")
            event = events.filter(subdomain__iexact=subdomain).first() or events.filter(slug__iexact=subdomain).first()
            if not event or (event.status == EventStatus.DRAFT and not can_manage_event(request.user, event, "events")):
                raise Http404("Evento não encontrado neste endereço.")
            request.current_event = event
            request.is_event_subdomain = True

        return self.get_response(request)
