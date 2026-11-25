"""Recuperação de acesso sem e-mail: o administrador gera um link de nova senha e envia pelo WhatsApp."""
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.permissions import management_required
from apps.core.utils import log_audit

from .models import User
from .password_recovery import _can_recover


def _require_owner(user):
    if not (user.is_active and user.is_superuser):
        raise PermissionDenied("Somente o administrador geral pode gerar links de acesso.")


def reset_link(user, request=None):
    path = reverse("identity:password_reset_confirm", kwargs={
        "uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": default_token_generator.make_token(user)})
    base = str(getattr(settings, "PUBLIC_SITE_URL", "") or "").rstrip("/")
    return base + path if base else request.build_absolute_uri(path)


@management_required
@never_cache
@require_http_methods(["GET", "POST"])
def access_recovery(request):
    _require_owner(request.user)
    query = (request.POST.get("q") if request.method == "POST" else request.GET.get("q", "")) or ""
    query = query.strip()[:120]
    users = []
    if query:
        users = list(User.objects.filter(Q(email__icontains=query) | Q(full_name__icontains=query) | Q(phone__icontains=query))
                     .order_by("full_name", "email")[:30])
    generated = None
    if request.method == "POST":
        target = get_object_or_404(User, pk=request.POST.get("user_id"))
        if target.pk == request.user.pk:
            raise PermissionDenied("Para trocar a sua própria senha, use a opção do seu perfil.")
        if not _can_recover(target):
            generated = {"user": target, "error": "Esta conta está desativada ou ainda não pode definir senha por link."}
        else:
            generated = {"user": target, "link": reset_link(target, request),
                         "hours": int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 259200)) // 3600}
            log_audit(actor=request.user, action="ACCESS_LINK_GENERATED", entity_type="User", entity_id=target.pk, request=request)
        users = users or [target]
    return render(request, "gestao/team/access_recovery.html", {"q": query, "users": users, "generated": generated})
