from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from apps.core.rate_limits import rate_limit_request
from .authentication import normalize_login_identifier


class AuthenticationRateLimitMiddleware(MiddlewareMixin):
    """Protect both public and Django-admin password entry with one shared budget."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST" or not request.resolver_match:
            return None
        route = request.resolver_match.view_name
        if route not in ("identity:login", "admin:login"):
            return None
        field = "username" if route == "admin:login" else "email"
        identity = normalize_login_identifier(request.POST.get(field, ""))
        decision = rate_limit_request(request, "login", ip_limit=40, account=identity, account_limit=10)
        if decision.allowed:
            return None
        message = "Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente."
        if route == "identity:login":
            from .views import safe_next
            messages.error(request, message)
            response = render(request, "identity/login.html", {"next_url": safe_next(request)}, status=429)
        else:
            response = HttpResponse(message, status=429, content_type="text/plain; charset=utf-8")
        response["Retry-After"] = str(decision.retry_after)
        response["Cache-Control"] = "private, no-store"
        return response
