from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from apps.core.permissions import require_event_access, can_manage_event
from apps.core.utils import log_audit
from .exports import export_financial_csv, export_orders_csv, export_participants_csv, export_summary_csv
from .forms import DateRangeForm
from .selectors import get_event_report


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
    if export and export not in {*exporters, "summary"}:
        return HttpResponseBadRequest("Tipo de relatório inválido.")
    if export == "participants" and not can_export_participants:
        return HttpResponseForbidden("A exportação de participantes exige permissão de inscrições nesta edição.")
    if export:
        log_audit(actor=request.user, event_id=event.pk, action="EXPORT_EVENT_REPORT", entity_type="Event", entity_id=event.pk,
                  diff={"report": export, "date_start": str(start or ""), "date_end": str(end or "")}, request=request)
        if export == "summary":
            return export_summary_csv(event, get_event_report(event, start, end), start, end)
        return exporters[export](event, start, end)
    context["report"] = get_event_report(event, start, end)
    response = render(request, "gestao/reporting/overview.html", context)
    response["Cache-Control"] = "private, no-store"
    return response
