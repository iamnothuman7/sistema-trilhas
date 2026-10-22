import hmac
import hashlib
import json
import logging
import uuid
from decimal import Decimal
from datetime import timedelta
import requests
from django.conf import settings
from django.utils import timezone
from apps.core.utils import cents_to_decimal

logger = logging.getLogger(__name__)

class MercadoPagoGateway:
    """
    Cliente para integração com Mercado Pago Checkout Transparente / Orders API.
    Documentação oficial: https://www.mercadopago.com.br/developers/pt/docs/checkout-api-orders/overview
    """
    def __init__(self):
        self.access_token = getattr(settings, "MP_ACCESS_TOKEN", "")
        self.public_key = getattr(settings, "MP_PUBLIC_KEY", "")
        self.webhook_secret = getattr(settings, "MP_WEBHOOK_SECRET", "")
        self.api_base = getattr(settings, "MP_API_BASE", "https://api.mercadopago.com")
        self.environment = getattr(settings, "MP_ENVIRONMENT", "sandbox")
        self.timeout = 12

    def _headers(self, idempotency_key=None):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "EquipeMorcego-SistemaTrilhas/1.0",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = str(idempotency_key)
        return headers

    def create_pix_order(self, order, receivable_due=None, idempotency_key=None):
        """
        Gera cobrança via Pix usando Mercado Pago.
        Retorna dicionário padronizado com provider_order_id, qr_code_base64, copy_paste e expiração.
        """
        amount_cents = receivable_due.amount_cents if receivable_due else order.total_cents
        amount_decimal = float(cents_to_decimal(amount_cents))
        key = idempotency_key or str(uuid.uuid4())
        expires_at = timezone.now() + timedelta(minutes=settings.PIX_EXPIRES_MINUTES)

        # Se as credenciais forem fictícias ou estiver em teste sem token real, gerar mockup oficial
        if not self.access_token or self.access_token.startswith("TEST-000000"):
            mock_order_id = f"MP-MOCK-ORDER-{uuid.uuid4().hex[:12].upper()}"
            mock_copy_paste = (
                f"00020126580014br.gov.bcb.pix0136{uuid.uuid4()}"
                f"520400005303986540{amount_decimal:.2f}5802BR5914EQUIPE MORCEGO6008TIANGUA"
                f"62070503***6304{uuid.uuid4().hex[:4].upper()}"
            )
            # Gerar imagem QR Code em memória
            try:
                import qrcode
                import io
                import base64
                qr = qrcode.QRCode(box_size=6, border=2)
                qr.add_data(mock_copy_paste)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            except Exception as e:
                logger.warning(f"Erro ao gerar QR code em imagem: {e}")
                qr_base64 = None

            return {
                "success": True,
                "provider_order_id": mock_order_id,
                "provider_payment_ref": f"PAY-{uuid.uuid4().hex[:8].upper()}",
                "qr_code_base64": qr_base64,
                "copy_paste": mock_copy_paste,
                "expires_at": expires_at,
                "state": "PENDING",
                "raw_response": {"mock": True, "order_id": mock_order_id},
            }

        payload = {
            "external_reference": order.merchant_reference,
            "title": f"Inscrição {order.event.title} - Pedido #{order.merchant_reference}",
            "description": f"Equipe Morcego - Pedido {order.merchant_reference}",
            "notification_url": f"{getattr(settings, 'SITE_URL', 'https://equipemorcego.com.br')}/integracoes/mercadopago/webhook/",
            "total_amount": amount_decimal,
            "items": [
                {
                    "title": f"Inscrição {order.event.title}",
                    "unit_price": amount_decimal,
                    "quantity": 1,
                    "currency_id": "BRL"
                }
            ],
            "payer": {
                "email": order.purchaser_email,
                "first_name": order.purchaser_name.split()[0],
                "last_name": " ".join(order.purchaser_name.split()[1:]) if len(order.purchaser_name.split()) > 1 else "Participante",
            },
            "payment_methods": {
                "excluded_payment_types": [{"id": "ticket"}, {"id": "atm"}]
            }
        }

        try:
            url = f"{self.api_base}/v1/orders"
            response = requests.post(url, json=payload, headers=self._headers(key), timeout=self.timeout)
            if response.status_code in (200, 201):
                data = response.json()
                qr_data = data.get("point_of_interaction", {}).get("transaction_data", {})
                return {
                    "success": True,
                    "provider_order_id": str(data.get("id")),
                    "provider_payment_ref": str(data.get("payments", [{}])[0].get("id") if data.get("payments") else ""),
                    "qr_code_base64": qr_data.get("qr_code_base64"),
                    "copy_paste": qr_data.get("qr_code"),
                    "expires_at": expires_at,
                    "state": "PENDING",
                    "raw_response": data,
                }
            else:
                logger.error(f"Erro Mercado Pago Orders API ({response.status_code}): {response.text}")
                return {
                    "success": False,
                    "error_code": str(response.status_code),
                    "error_message": response.text,
                    "state": "FAILED"
                }
        except requests.RequestException as e:
            logger.exception("Timeout ou falha de conexão com Mercado Pago")
            return {
                "success": False,
                "error_code": "CONNECTION_TIMEOUT",
                "error_message": str(e),
                "state": "UNKNOWN"
            }

    def create_card_charge(self, order, token, installments=1, payment_method_id="master", issuer_id=None, idempotency_key=None):
        """
        Processa pagamento com cartão de crédito via token seguro obtido pelo Card Payment Brick.
        """
        amount_cents = order.total_cents
        amount_decimal = float(cents_to_decimal(amount_cents))
        key = idempotency_key or str(uuid.uuid4())

        if not self.access_token or self.access_token.startswith("TEST-000000"):
            # Mock mode para ambiente de desenvolvimento
            return {
                "success": True,
                "provider_order_id": f"MP-CARD-{uuid.uuid4().hex[:10].upper()}",
                "provider_payment_ref": f"PAY-CARD-{uuid.uuid4().hex[:8].upper()}",
                "state": "SUCCEEDED",
                "card_brand": payment_method_id,
                "card_last_four": "1234",
                "card_installments": installments,
                "raw_response": {"status": "approved", "mock": True}
            }

        payload = {
            "transaction_amount": amount_decimal,
            "token": token,
            "description": f"Inscrição {order.event.title} - #{order.merchant_reference}",
            "installments": int(installments),
            "payment_method_id": payment_method_id,
            "payer": {
                "email": order.purchaser_email,
            },
            "external_reference": order.merchant_reference,
        }
        if issuer_id:
            payload["issuer_id"] = issuer_id

        try:
            url = f"{self.api_base}/v1/payments"
            response = requests.post(url, json=payload, headers=self._headers(key), timeout=self.timeout)
            if response.status_code in (200, 201):
                data = response.json()
                status = data.get("status")
                is_approved = status == "approved"
                state = "SUCCEEDED" if is_approved else ("PENDING" if status == "in_process" else "FAILED")
                card_info = data.get("card", {})
                return {
                    "success": is_approved,
                    "provider_order_id": str(data.get("order", {}).get("id") or data.get("id")),
                    "provider_payment_ref": str(data.get("id")),
                    "state": state,
                    "card_brand": data.get("payment_method_id", "cartao"),
                    "card_last_four": card_info.get("last_four_digits", "0000"),
                    "card_installments": data.get("installments", installments),
                    "raw_response": data,
                }
            else:
                return {
                    "success": False,
                    "error_code": str(response.status_code),
                    "error_message": response.text,
                    "state": "FAILED"
                }
        except requests.RequestException as e:
            logger.exception("Falha na chamada de cartão com Mercado Pago")
            return {
                "success": False,
                "error_code": "CONNECTION_TIMEOUT",
                "error_message": str(e),
                "state": "UNKNOWN"
            }

    def verify_webhook_signature(self, x_signature: str, x_request_id: str, data_id: str) -> bool:
        """
        Verifica a assinatura criptográfica do Webhook do Mercado Pago (HMAC SHA-256).
        Formato oficial: id:<data.id>;request-id:<x-request-id>;ts:<ts>;
        """
        if not self.webhook_secret:
            # Em desenvolvimento local sem secret configurado
            return True

        if not x_signature:
            return False

        try:
            parts = dict(item.split("=") for item in x_signature.split(",") if "=" in item)
            ts = parts.get("ts")
            v1_hash = parts.get("v1")
            if not ts or not v1_hash:
                return False

            manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
            expected_hash = hmac.new(
                self.webhook_secret.encode("utf-8"),
                manifest.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(v1_hash, expected_hash)
        except Exception as e:
            logger.warning(f"Erro ao verificar assinatura HMAC de webhook: {e}")
            return False

    def get_order_status(self, provider_order_id: str) -> dict:
        """
        Consulta estado de uma ordem diretamente no Mercado Pago.
        GET /v1/orders/{id}
        """
        if not self.access_token or self.access_token.startswith("TEST-000000") or "MOCK" in str(provider_order_id):
            return {
                "status": "closed",
                "payment_status": "approved",
                "total_amount": 180.00,
                "mock": True
            }

        url = f"{self.api_base}/v1/orders/{provider_order_id}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        return {}
