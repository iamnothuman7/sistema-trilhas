"""Event-scoped sponsor management; no destructive actions."""
import logging

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.permissions import management_required
from apps.core.utils import log_audit
from .forms import SponsorForm
from .models import Sponsor

logger = logging.getLogger(__name__)


@management_required
@require_GET
def sponsor_list(request, event_id):
    event = request.management_event
    return render(request, "gestao/sponsors/list.html", {"event": event, "sponsors": event.sponsors.all()})


def _sponsor_form(request, sponsor):
    event = request.management_event
    creating = sponsor.pk is None
    previous_name = sponsor.logo_image.name
    previous_storage = sponsor.logo_image.storage
    has_upload = bool(request.FILES.get("logo_image"))
    form = SponsorForm(request.POST or None, request.FILES or None, instance=sponsor)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.event = event
        try:
            with transaction.atomic():
                saved.save()
                log_audit(
                    actor=request.user, event_id=event.pk,
                    action="SPONSOR_CREATED" if creating else "SPONSOR_UPDATED",
                    entity_type="Sponsor", entity_id=saved.pk,
                    diff={"fields": list(form.changed_data)}, request=request,
                )
        except Exception:
            # Database rollback does not undo storage.save(). Remove only the
            # distinct file just uploaded; never touch the previous reference.
            uploaded = saved.logo_image
            if (has_upload and uploaded and uploaded._committed
                    and (uploaded.storage is not previous_storage or uploaded.name != previous_name)):
                try:
                    uploaded.storage.delete(uploaded.name)
                except Exception:
                    logger.exception("Could not remove a failed sponsor upload from storage.")
            raise
        messages.success(request, "Patrocinador cadastrado." if creating else "Patrocinador atualizado.")
        return redirect("gestao:sponsor_list", event_id=event.pk)
    return render(request, "gestao/sponsors/form.html", {
        "event": event, "sponsor": sponsor, "form": form, "creating": creating,
    }, status=400 if request.method == "POST" else 200)


@management_required
@require_http_methods(["GET", "POST"])
def sponsor_create(request, event_id):
    return _sponsor_form(request, Sponsor(event=request.management_event))


@management_required
@require_http_methods(["GET", "POST"])
def sponsor_edit(request, event_id, sponsor_id):
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event)
    return _sponsor_form(request, sponsor)
