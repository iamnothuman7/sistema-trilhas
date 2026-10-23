"""Permission-scoped, audited spelling corrections for aggregate rankings."""
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.utils import log_audit
from apps.core.permissions import management_required
from .forms_rankings import RankingAliasForm
from .models import Event
from .rankings import alias_model, confirmed_registration_groups, display_name, get_event_rankings, group_key


def _snapshot(alias):
    return {"source_name": alias.source_name, "canonical_name": alias.canonical_name, "state": getattr(alias, "state", "")}


@never_cache
@management_required
@require_http_methods(["GET", "POST"])
def ranking_management(request, event_id):
    event = request.management_event
    forms = {
        "team": RankingAliasForm(event=event, kind="team", prefix="team"),
        "city": RankingAliasForm(event=event, kind="city", prefix="city"),
    }
    response_status = 200
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action not in {"save_team", "save_city", "delete_team", "delete_city"}:
            return HttpResponseBadRequest("Operação de ranking inválida.")
        operation, kind = action.split("_")
        with transaction.atomic():
            Event.objects.select_for_update().get(pk=event.pk)
            model = alias_model(kind)
            if operation == "delete":
                try:
                    alias_id = int(request.POST.get("alias_id", ""))
                except (ValueError, TypeError):
                    return HttpResponseBadRequest("Correção inválida.")
                alias = get_object_or_404(model, event=event, pk=alias_id)
                alias_pk, before = alias.pk, _snapshot(alias)
                alias.delete()
                log_audit(actor=request.user, event_id=event.pk, action="DELETE_RANKING_ALIAS", entity_type=model.__name__, entity_id=alias_pk, diff={"before": before, "after": {}}, request=request)
                messages.success(request, "Correção removida. O ranking voltou a usar os nomes das inscrições.")
                return redirect("gestao:ranking_management", event_id=event.pk)
            form = RankingAliasForm(request.POST, event=event, kind=kind, prefix=kind)
            forms[kind] = form
            if form.is_valid():
                values = form.cleaned_data
                lookup = {"event": event, "source_key": group_key(values["source_name"])}
                if kind == "city":
                    lookup["state"] = values["state"]
                alias = model.objects.filter(**lookup).first()
                before = _snapshot(alias) if alias else {}
                alias = alias or model(**lookup)
                alias.source_name, alias.canonical_name = values["source_name"], values["canonical_name"]
                alias.full_clean()
                after = _snapshot(alias)
                if before != after:
                    alias.save()
                    log_audit(actor=request.user, event_id=event.pk, action="SAVE_RANKING_ALIAS", entity_type=model.__name__, entity_id=alias.pk, diff={"before": before, "after": after}, reason=values.get("reason", ""), request=request)
                messages.success(request, "Correção salva. O ranking será atualizado com esse nome.")
                return redirect("gestao:ranking_management", event_id=event.pk)
            response_status = 400
    raw_groups = confirmed_registration_groups(event)
    return render(request, "gestao/rankings/manage.html", {
        "event": event, "team_form": forms["team"], "city_form": forms["city"],
        "team_aliases": event.team_aliases.all(), "city_aliases": event.city_aliases.all(),
        "ranking": get_event_rankings(event, limit=None),
        "raw_team_names": sorted({display_name(row["team_name"]) for row in raw_groups if row["team_name"]}),
        "raw_city_names": sorted({display_name(row["person__city"]) for row in raw_groups if row["person__city"]}),
    }, status=response_status)
