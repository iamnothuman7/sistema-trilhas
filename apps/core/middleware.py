from django.db.models import Q
from apps.events.models import Event


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
            normalized = subdomain.replace("-", "")
            event = (
                Event.objects.select_related("theme", "organization")
                .filter(
                    Q(subdomain__iexact=subdomain)
                    | Q(subdomain__iexact=normalized)
                    | Q(slug__iexact=subdomain)
                    | Q(slug__icontains=subdomain)
                )
                .first()
            )
            if not event and "trilhao" in normalized:
                event = Event.objects.select_related("theme", "organization").first()

            if event:
                request.current_event = event
                request.is_event_subdomain = True

        return self.get_response(request)
