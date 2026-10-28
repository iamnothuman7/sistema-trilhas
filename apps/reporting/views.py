from urllib.parse import urlencode
from functools import partial

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from apps.core.permissions import require_event_access, can_manage_event
from apps.core.utils import log_audit
from .exports import export_financial_csv, export_orders_csv, export_participants_csv, export_summary_csv
from .exports import AGGREGATE_REPORTS, export_aggregate_csv, export_expenses_csv, export_checkins_csv, export_benefits_csv, export_deliveries_csv, export_bundle
from .forms import DateRangeForm
from .selectors import get_event_report
from .exports import export_sponsorships_csv, export_sponsor_receipts_csv, export_addons_csv, export_raffle_csv


@login_required(login_url="identity:login")
@require_GET
@never_cache
def event_reports(request, event_id):
    event = require_event_access(request, event_id, action="finance")
    request.management_event = event
    form = DateRangeForm(request.GET)
    valid = form.is_valid()
    can_export_participants = can_manage_event(request.user, event, "registrations")
    context = {"event": event, "date_form": form, "report": None, "filter_query": "", "can_export_participants": can_export_participants}
    if not valid:
        return render(request, "gestao/reporting/overview.html", context, status=400)
    start, end = form.cleaned_data["date_start"], form.cleaned_data["date_end"]
    context["filter_query"] = urlencode({key: value.isoformat() for key, value in form.cleaned_data.items() if value})
    export = request.GET.get("export")
    exporters = {"participants": export_participants_csv, "financial": export_financial_csv, "orders": export_orders_csv}
    exporters.update(expenses=export_expenses_csv, checkins=export_checkins_csv, benefits=export_benefits_csv, deliveries=export_deliveries_csv)
    exporters.update(sponsorships=export_sponsorships_csv, sponsor_receipts=export_sponsor_receipts_csv)
    exporters['extras'] = export_addons_csv
    exporters['raffle'] = export_raffle_csv
    exporters.update({key: partial(export_aggregate_csv, section=key) for key in AGGREGATE_REPORTS})
    allowed = {key: value for key, value in exporters.items() if
        (key != 'raffle' or can_manage_event(request.user, event, 'events')) and
        (key != 'participants' or can_export_participants) and
        (key not in {'checkins', 'benefits', 'deliveries'} or can_manage_event(request.user, event, 'checkin'))}
    if export and export not in {*exporters, "summary", "all"}:
        return HttpResponseBadRequest("Tipo de relatório inválido.")
    if export in exporters and export not in allowed:
        return HttpResponseForbidden("Sua conta não tem permissão para exportar estes dados individuais nesta edição.")
    if export:
        log_audit(actor=request.user, event_id=event.pk, action="EXPORT_EVENT_REPORT", entity_type="Event", entity_id=event.pk,
                  diff={"report": export, "date_start": str(start or ""), "date_end": str(end or "")}, request=request)
        if export in {'summary', 'all'}:
            report = get_event_report(event, start, end)
            if export == 'summary': return export_summary_csv(event, report, start, end)
            allowed.update({key: partial(export_aggregate_csv, section=key, report=report) for key in AGGREGATE_REPORTS})
            allowed['summary'] = lambda selected_event, start, end: export_summary_csv(selected_event, report, start, end)
            return export_bundle(event, allowed, start, end)
        return allowed[export](event, start, end)
    labels = {'participants': 'Participantes', 'financial': 'Extrato financeiro', 'orders': 'Pedidos', 'expenses': 'Despesas',
        'checkins': 'Credenciamentos', 'benefits': 'Benefícios e kits', 'deliveries': 'Histórico de entregas', **{key: value[0] for key, value in AGGREGATE_REPORTS.items()}}
    labels.update(sponsorships='Patrocínios — saldos atuais (todos os acordos)', sponsor_receipts='Recebimentos de patrocinadores')
    labels['extras'] = 'Produtos extras — estoque atual'
    labels['raffle'] = 'Sorteio — números, pontos e recebimentos'
    context['export_options'] = [{'key': key, 'label': labels[key]} for key in allowed]
    context["report"] = get_event_report(event, start, end)
    response = render(request, "gestao/reporting/overview.html", context)
    response["Cache-Control"] = "private, no-store"
    return response
