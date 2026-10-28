import hashlib
import json
import logging
import re

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .gateway import GatewayError, webhook_signature_parts
from .models import ProviderEvent
from .services import reconcile_payment, PaymentValidationError

logger = logging.getLogger(__name__)


@csrf_exempt
@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def mercadopago_webhook(request):
    """The notification only selects a resource; only its authenticated API state settles it."""
    if request.method in ("GET", "HEAD"):
        response = render(request, "payments/webhook_info.html")
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
    from .gateway_config import webhook_gateways
    payment_id = request.GET.get("data.id", "")
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    if not re.fullmatch(r"[0-9]{1,30}", payment_id) or not request_id or len(request_id) > 512 or webhook_signature_parts(signature) is None:
        return JsonResponse({"error": "invalid_signature"}, status=401)
    gateways = list(webhook_gateways(payment_id))
    if not gateways:
        return JsonResponse({"error": "integration_unavailable"}, status=503)
    authenticated = [gateway for gateway in gateways if gateway.verify_webhook_signature(signature, request_id, payment_id)]
    if not authenticated:
        return JsonResponse({"error": "invalid_signature"}, status=401)
    if len(request.body) > 65536:
        return JsonResponse({"error": "invalid_notification"}, status=400)
    try:
        payload = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "invalid_notification"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_notification"}, status=400)
    event_type = str(payload.get("type") or request.GET.get("type") or "payment")[:100]
    if event_type != "payment":
        return JsonResponse({"status": "ignored"})
    body_data = payload.get("data") or {}
    if not isinstance(body_data, dict) or (body_data.get("id") and str(body_data["id"]) != payment_id):
        return JsonResponse({"error": "resource_mismatch"}, status=400)
    # These values are all covered by the signature. Body event IDs alone are not trusted.
    dedup_key = hashlib.sha256(f"{payment_id}|{request_id}|{signature}".encode()).hexdigest()
    event, _ = ProviderEvent.objects.get_or_create(deduplication_key=dedup_key, defaults={
        "provider": "mercadopago", "provider_event_id": str(payload.get("id") or "")[:150],
        "resource_id": payment_id, "event_type": event_type, "signature_valid": True,
        "payload_hash": hashlib.sha256(request.body).hexdigest(),
        "raw_payload": {"id": str(payload.get("id") or "")[:150], "type": event_type,
                        "action": str(payload.get("action") or "")[:100], "data": {"id": payment_id}},
    })
    if event.processed_at:
        return JsonResponse({"status": "already_processed"})
    try:
        payment = None
        last_error = None
        for gateway in authenticated:
            try:
                candidate = gateway.get_payment(payment_id)
            except GatewayError as exc:
                last_error = exc
                continue
            if str(candidate.get("collector_id", "")) == gateway.collector_id and candidate.get("live_mode") is (gateway.environment == "production"):
                payment = candidate
                break
        if payment is None:
            if last_error:
                raise last_error
            raise PaymentValidationError("account_mismatch")
        if str(payment.get("id", "")) != payment_id:
            raise PaymentValidationError("resource_mismatch")
        with transaction.atomic():
            event = ProviderEvent.objects.select_for_update().get(pk=event.pk)
            if not event.processed_at:
                reconcile_payment(payment)
                event.processed_at = timezone.now()
                event.save(update_fields=["processed_at"])
    except GatewayError:
        return JsonResponse({"error": "provider_unavailable"}, status=503)
    except PaymentValidationError as exc:
        # Only a non-sensitive reason code is logged, never the payload or credentials.
        logger.warning("Mercado Pago reconciliation refused: %s", str(exc))
        return JsonResponse({"error": "payment_not_reconciled"}, status=422)
    return JsonResponse({"status": "processed"})
