from django.http import Http404
from urllib.parse import urlsplit
from django.utils.cache import patch_vary_headers
from apps.events.models import Event, EventStatus
from apps.core.permissions import can_manage_event


class HtmxNavigationMiddleware:
    """Keep management redirects inside HTMX; authentication stays a full navigation."""
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
                destination = urlsplit(target)
                internal = (request.path.startswith('/gestao/') and target.startswith('/gestao/')
                            and not destination.netloc and not destination.scheme)
                response["HX-Location" if internal else "HX-Redirect"] = target
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


import hashlib
from apps.core.models import PageViewRecord
from django.utils import timezone


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def categorize_path(path):
    p = path.rstrip("/") + "/"
    if p == "/":
        return "Página Inicial (Site)"
    if p.startswith("/checkout/") or p.startswith("/inscricao/"):
        return "Inscrições & Checkout"
    if p.startswith("/sorteio/") or p.startswith("/rifa/"):
        return "Sorteio da Moto"
    if p.startswith("/regulamento/") or p.startswith("/termos/"):
        return "Regulamento e Termos"
    if p.startswith("/patrocinadores/"):
        return "Mural de Patrocinadores"
    if p.startswith("/credenciamento/"):
        return "Scanner / Credenciamento"
    if p.startswith("/minha-conta/"):
        return "Área do Participante"
    if p.startswith("/login/"):
        return "Tela de Login"
    if p.startswith("/gestao/metricas/"):
        return "Gestão: Métricas de Acesso"
    if p.startswith("/gestao/eventos/") and "/inscricoes/" in p:
        return "Gestão: Inscrições"
    if p.startswith("/gestao/eventos/") and "/financeiro/" in p:
        return "Gestão: Financeiro"
    if p.startswith("/gestao/eventos/") and "/patrocinadores/" in p:
        return "Gestão: Patrocinadores"
    if p.startswith("/gestao/eventos/") and "/sorteio/" in p:
        return "Gestão: Sorteio"
    if p.startswith("/gestao/eventos/") and "/camisetas/" in p:
        return "Gestão: Camisetas / Estoque"
    if p.startswith("/gestao/eventos/") and "/credenciamento/" in p:
        return "Gestão: Credenciamento"
    if p.startswith("/gestao/eventos/") and "/equipe/" in p:
        return "Gestão: Equipe & Acessos"
    if p.startswith("/gestao/"):
        return "Gestão: Visão Geral"
    return path[:80]


def parse_device_info(user_agent):
    ua = user_agent.lower() if user_agent else ""
    if any(k in ua for k in ["ipad", "tablet", "playbook", "silk"]):
        device = "TABLET"
    elif any(k in ua for k in ["mobile", "iphone", "ipod", "android", "blackberry", "webos"]):
        device = "MOBILE"
    else:
        device = "DESKTOP"

    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        os_name = "iOS"
    elif "android" in ua:
        os_name = "Android"
    elif "windows" in ua:
        os_name = "Windows"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Outro"

    if "instagram" in ua:
        browser = "Instagram In-App"
    elif "whatsapp" in ua:
        browser = "WhatsApp In-App"
    elif "edg" in ua:
        browser = "Edge"
    elif "chrome" in ua and "crios" not in ua:
        browser = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "firefox" in ua or "fxios" in ua:
        browser = "Firefox"
    else:
        browser = "Navegador Web"

    return device, os_name, browser


def parse_referrer_source(request):
    ref = request.META.get("HTTP_REFERER", "").lower()
    utm = request.GET.get("utm_source", "").lower() or request.GET.get("src", "").lower() or request.GET.get("ref", "").lower()

    if "instagram" in utm or "instagram.com" in ref:
        return "Instagram"
    if "whatsapp" in utm or "whatsapp.com" in ref or "wa.me" in ref:
        return "WhatsApp"
    if "facebook" in utm or "facebook.com" in ref or "fb.com" in ref:
        return "Facebook"
    if "google" in utm or "google.com" in ref or "google.com.br" in ref:
        return "Google"
    if "youtube" in utm or "youtube.com" in ref:
        return "YouTube"
    if ref and not any(host in ref for host in ["equipemorcego", "localhost", "127.0.0.1"]):
        return "Site Externo"
    return "Direto / Link"


class TrafficAnalyticsMiddleware:
    """
    Rastreamento assíncrono/leve de visualizações e abas em tempo real.
    Totalmente silencioso e não bloqueia nenhuma requisição.
    """
    IGNORE_PREFIXES = (
        "/static/", "/media/", "/favicon", "/robots.txt",
        "/apple-touch-icon", "/health", "/api/", "/manifest.json"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.method == "GET" and response.status_code == 200:
            path = request.path
            if not any(path.startswith(prefix) for prefix in self.IGNORE_PREFIXES):
                content_type = response.get("Content-Type", "")
                if "text/html" in content_type:
                    try:
                        ip = get_client_ip(request)
                        ua = request.META.get("HTTP_USER_AGENT", "")
                        device, os_name, browser = parse_device_info(ua)
                        referrer = parse_referrer_source(request)
                        page_name = categorize_path(path)

                        today = timezone.now().strftime("%Y-%m-%d")
                        visitor_seed = f"{ip}:{ua}:{today}"
                        visitor_hash = hashlib.sha256(visitor_seed.encode("utf-8")).hexdigest()[:24]

                        is_staff = request.user.is_authenticated and (
                            request.user.is_staff or path.startswith("/gestao/")
                        )
                        event_id = getattr(getattr(request, "current_event", None), "id", None)

                        PageViewRecord.objects.create(
                            path=path,
                            page_name=page_name,
                            visitor_hash=visitor_hash,
                            session_id=request.session.session_key if hasattr(request, "session") else None,
                            device_type=device,
                            os_name=os_name,
                            browser=browser,
                            referrer_source=referrer,
                            referrer_url=request.META.get("HTTP_REFERER", "")[:500],
                            ip_address=ip if ip and len(ip) <= 45 else None,
                            is_staff_view=is_staff,
                            event_id=event_id,
                        )
                    except Exception:
                        pass

        return response
