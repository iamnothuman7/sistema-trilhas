import hashlib
import json
import logging
import re

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .gateway import MercadoPagoGateway, GatewayError
from .models import ProviderEvent
from .services import reconcile_payment, PaymentValidationError

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def mercadopago_webhook(request):
    """The notification only selects a resource; only its authenticated API state settles it."""
    gateway = MercadoPagoGateway()
    if gateway.configuration_issues():
        return JsonResponse({"error": "integration_unavailable"}, status=503)
    payment_id = request.GET.get("data.id", "")
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    if not re.fullmatch(r"[0-9]{1,30}", payment_id) or not gateway.verify_webhook_signature(signature, request_id, payment_id):
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
        payment = gateway.get_payment(payment_id)
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
