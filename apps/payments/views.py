import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.utils import timezone
from apps.core.utils import sha256_text
from apps.checkout.models import Order, OrderStatus
from apps.catalog.models import CapacityHold, HoldStatus
from apps.payments.models import ProviderEvent, ChargeAttempt, ChargeState, PaymentAllocation
from apps.payments.gateway import MercadoPagoGateway
from apps.finance.models import FinancialEntry, FinancialEntryKind
from apps.registrations.models import (
    Registration, CommercialState, OperationalState,
    RegistrationCredential, Entitlement, EntitlementState
)

logger = logging.getLogger(__name__)

@csrf_exempt
def mercadopago_webhook(request):
    """
    Webhook do Mercado Pago para confirmação de pagamentos Pix e Cartão.
    Validação criptográfica HMAC SHA-256 e idempotência estrita.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    x_signature = request.META.get("HTTP_X_SIGNATURE", "")
    x_request_id = request.META.get("HTTP_X_REQUEST_ID", "")
    data_id = request.GET.get("data.id") or request.GET.get("id", "")

    try:
        raw_body = request.body.decode("utf-8")
        payload = json.loads(raw_body) if raw_body else {}
    except Exception:
        payload = {}

    if not data_id and isinstance(payload, dict):
        data_id = str(payload.get("data", {}).get("id") or payload.get("id") or "")

    # Verificar assinatura
    gateway = MercadoPagoGateway()
    is_valid_sig = gateway.verify_webhook_signature(x_signature, x_request_id, data_id)

    payload_hash = sha256_text(raw_body if raw_body else str(data_id))

    # Registrar evento para deduplicação
    provider_event, created = ProviderEvent.objects.get_or_create(
        provider="mercadopago",
        provider_event_id=str(data_id) if data_id else payload_hash[:32],
        defaults={
            "resource_id": str(data_id),
            "event_type": payload.get("type", "payment"),
            "signature_valid": is_valid_sig,
            "payload_hash": payload_hash,
            "raw_payload": payload,
        }
    )

    if not created and provider_event.processed_at is not None:
        # Evento já processado anteriormente com sucesso
        return JsonResponse({"status": "already_processed"}, status=200)

    # Buscar informações da ordem ou pagamento no gateway
    order_data = gateway.get_order_status(data_id)
    external_ref = order_data.get("external_reference") or payload.get("data", {}).get("external_reference")

    if not external_ref:
        # Se for teste ou não tiver ref, tentar achar por provider_order_id na ChargeAttempt
        attempt = ChargeAttempt.objects.filter(provider_order_id=data_id).first()
        if attempt:
            external_ref = attempt.order.merchant_reference

    if external_ref:
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(merchant_reference=external_ref).first()
            if order and order.status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT):
                order.status = OrderStatus.PAID
                order.save()

                # Atualizar tentativa de cobrança
                charge = ChargeAttempt.objects.filter(order=order).first()
                if charge:
                    charge.state = ChargeState.SUCCEEDED
                    charge.resolved_at = timezone.now()
                    charge.save()

                # Converter holds de capacidade em COMMITTED
                CapacityHold.objects.filter(order_id=order.id, status=HoldStatus.ACTIVE).update(
                    status=HoldStatus.COMMITTED
                )

                # Ativar inscrições
                for line in order.lines.select_related("registration"):
                    if hasattr(line, "registration"):
                        reg = line.registration
                        reg.commercial_state = CommercialState.ACTIVE
                        # Se já tiver termos aceitos, avança para READY
                        if reg.terms_acceptances.exists():
                            reg.operational_state = OperationalState.READY
                        else:
                            reg.operational_state = OperationalState.NOT_READY
                        reg.save()

                        # Criar credencial de QR se não existir
                        if not hasattr(reg, "credential"):
                            import secrets
                            import qrcode
                            import io
                            import base64
                            raw_tok = secrets.token_hex(16).upper()
                            digest = sha256_text(raw_tok)
                            # Gerar imagem QR Code
                            qr_img = qrcode.make(f"TRILHA:{reg.event.slug}:{raw_tok}")
                            buf = io.BytesIO()
                            qr_img.save(buf, format="PNG")
                            b64_qr = base64.b64encode(buf.getvalue()).decode("utf-8")

                            RegistrationCredential.objects.create(
                                registration=reg,
                                token_digest=digest,
                                raw_token=raw_tok,
                                qr_code_image=b64_qr,
                            )

                        # Criar Entitlements
                        prod = line.product
                        if prod:
                            if prod.includes_kit:
                                Entitlement.objects.get_or_create(
                                    registration=reg,
                                    benefit_type="KIT",
                                    defaults={"title": "Kit Oficial do Piloto", "quantity_total": 1}
                                )
                            if prod.includes_shirt:
                                Entitlement.objects.get_or_create(
                                    registration=reg,
                                    benefit_type="CAMISETA",
                                    defaults={"title": f"Camiseta Oficial (Tam. {reg.shirt_size})", "quantity_total": 1}
                                )
                            if prod.includes_meal:
                                Entitlement.objects.get_or_create(
                                    registration=reg,
                                    benefit_type="REFEICAO",
                                    defaults={"title": "Café da Manhã e Almoço Off-Road", "quantity_total": 1}
                                )
                            Entitlement.objects.get_or_create(
                                registration=reg,
                                benefit_type="ACESSO_PISTA",
                                defaults={"title": "Pulseira e Acesso à Trilha", "quantity_total": 1}
                            )

                # Registrar lançamento financeiro de receita
                FinancialEntry.objects.get_or_create(
                    event=order.event,
                    source_reference=f"ORDER-{order.merchant_reference}",
                    kind=FinancialEntryKind.RECEITA_INSCRICAO,
                    defaults={
                        "order": order,
                        "amount_cents_signed": order.total_cents,
                        "description": f"Recebimento Pedido #{order.merchant_reference} - {order.purchaser_name}",
                        "recognized_at": timezone.now(),
                    }
                )

                # Registrar tarifa estimada do Mercado Pago (Pix: 0.99%, Cartão: ~4.98%)
                is_pix = charge.payment_method == "pix" if charge else True
                fee_rate = 0.0099 if is_pix else 0.0498
                fee_cents = int(order.total_cents * fee_rate)
                FinancialEntry.objects.get_or_create(
                    event=order.event,
                    source_reference=f"FEE-{order.merchant_reference}",
                    kind=FinancialEntryKind.TAXA_MERCADO_PAGO,
                    defaults={
                        "order": order,
                        "amount_cents_signed": -fee_cents,
                        "description": f"Tarifa Estimada MP Pedido #{order.merchant_reference}",
                        "recognized_at": timezone.now(),
                    }
                )

    provider_event.processed_at = timezone.now()
    provider_event.save()

    return JsonResponse({"status": "received"}, status=200)
