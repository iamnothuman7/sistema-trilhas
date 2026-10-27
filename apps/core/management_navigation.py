"""Safe, shared event navigation for every management workspace."""
from urllib.parse import urlencode
from uuid import UUID

from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .permissions import accessible_events, management_required, require_event_access

# Destinations only accept an event, never a record belonging to the old event.
MODULES = {
    "dashboard": ("view", False), "usage_guide": ("view", False),
    "event_list": ("events", False), "event_detail": ("events", True),
    "event_edit": ("events", True), "event_appearance": ("content", True),
    "registration_list": ("registrations", True), "credenciamento": ("checkin", True),
    "catalog": ("catalog", True), "finance": ("finance", True),
    "reports": ("finance", True), "ranking_management": ("registrations", True),
    "frames": ("content", True), "team_frame_editor": ("content", True),
    "raffle_management": ("events", True), "sponsor_bank": ("sponsors", False),
    "mercadopago_connection": ("view", False), "audit_log": ("audit", False),
}
PARENTS = {
    "event_create": "event_list", "catalog_create": "catalog", "catalog_edit": "catalog",
    "lot_remove": "catalog", "registration_detail": "registration_list",
    "manual_registration_create": "registration_list", "manual_registration_payment": "registration_list",
    "official_frame_preview": "frames",
    **{name: "sponsor_bank" for name in (
        "sponsor_list", "sponsor_create", "sponsor_edit", "sponsor_detail", "sponsor_link",
        "sponsor_agreement", "sponsor_receipt", "sponsor_reversal",
        "sponsor_profile_create", "sponsor_profile_edit",
    )},
}
ALL_SCOPES = {"sponsor_bank", "audit_log"}


def event_switcher_context(request, event):
    current = request.resolver_match.url_name if request.resolver_match else "dashboard"
    module = PARENTS.get(current, current)
    module = module if module in MODULES else "dashboard"
    events = list(accessible_events(request.user, MODULES[module][0]).order_by("-starts_at", "pk"))
    all_selected = bool(getattr(request, "management_all_events", False))
    note = "Ao trocar, esta área passa a mostrar os dados do evento escolhido."
    if module == "mercadopago_connection":
        note = "A conexão Mercado Pago é compartilhada pelos eventos. A seleção organiza as outras áreas."
    elif module == "event_list":
        note = "Aqui você encontra todos os eventos. A seleção define o evento das outras áreas."
    elif current in {"sponsor_profile_create", "sponsor_profile_edit"}:
        note = "O cadastro da marca é compartilhado. Cada evento tem sua exibição e seu financeiro."
    return {"management_switch_module": module, "management_event_choices": events,
            "management_all_option": module in ALL_SCOPES, "management_all_selected": all_selected,
            "management_scope_note": note}


@management_required
@never_cache
@require_GET
def switch_management_event(request):
    module = request.GET.get("module", "dashboard")
    if module not in MODULES:
        raise Http404("Área não encontrada.")
    selected = request.GET.get("event_id", "")
    if selected == "all" and module in ALL_SCOPES:
        return redirect(reverse(f"gestao:{module}") + "?event_id=all")
    try:
        event_id = UUID(selected)
    except (ValueError, TypeError):
        raise Http404("Evento não encontrado.")
    action, scoped = MODULES[module]
    event = require_event_access(request, event_id, action)
    request.session["management_event_id"] = str(event.pk)
    if scoped:
        return redirect(f"gestao:{module}", event_id=event.pk)
    return redirect(reverse(f"gestao:{module}") + "?" + urlencode({"event_id": event.pk}))
