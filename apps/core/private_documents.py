"""Permission-scoped downloads; no public URLs, content rendering or shared cache."""
from pathlib import Path

from django import forms
from django.contrib import admin
from django.db import models
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .permissions import management_required
from .utils import log_audit


@never_cache
@management_required
@require_safe
def private_document_download(request, event_id, kind, document_id):
    from apps.finance.models import Expense
    from apps.payments.models import ManualPaymentEvidence, Refund

    if kind == "despesa":
        document = get_object_or_404(Expense, pk=document_id, event_id=event_id)
    elif kind == "pagamento":
        document = get_object_or_404(ManualPaymentEvidence, pk=document_id, order__event_id=event_id)
    elif kind == 'reembolso':
        document = get_object_or_404(Refund, pk=document_id, order__event_id=event_id)
    else:
        raise Http404
    asset = document.proof_asset
    if not asset:
        raise Http404
    try:
        stream = asset.open("rb")
    except (FileNotFoundError, IsADirectoryError):
        raise Http404 from None
    response = FileResponse(stream, as_attachment=True, filename=Path(asset.name).name,
                            content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Referrer-Policy"] = "no-referrer"
    log_audit(actor=request.user, event_id=event_id, action="PRIVATE_DOCUMENT_DOWNLOADED",
              entity_type=type(document).__name__, entity_id=document.pk, request=request)
    return response


class PrivateDocumentAdminMixin:
    # ClearableFileInput calls FieldFile.url even for displaying its initial value.
    # Keep uploads editable, and expose the existing file only via our checked view.
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), "private_document_link")

    @admin.display(description="Comprovante protegido")
    def private_document_link(self, obj):
        if not obj or not obj.proof_asset:
            return "Nenhum arquivo anexado."
        event_id = obj.event_id if hasattr(obj, "event_id") else obj.order.event_id
        kind = "despesa" if hasattr(obj, "event_id") else ('reembolso' if type(obj).__name__=='Refund' else "pagamento")
        return format_html('<a href="{}">Baixar comprovante</a>', reverse(
            "gestao:private_document_download",
            kwargs={"event_id": event_id, "kind": kind, "document_id": obj.pk},
        ))
