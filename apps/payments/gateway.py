"""Mercado Pago Payments API. Credentials and card data are never logged."""
import hashlib
import hmac
import re
import time
from decimal import Decimal
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class GatewayError(Exception):
    def __init__(self, code, message, *, uncertain=False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class MercadoPagoGateway:
    """Pix and tokenized cards use the same /v1/payments contract."""
    def __init__(self, configuration=None):
        from .gateway_config import load_gateway_configuration, GatewayConfiguration
        loaded = GatewayConfiguration(configuration, source="draft") if configuration is not None else load_gateway_configuration()
        for key in ("access_token", "public_key", "webhook_secret", "collector_id", "webhook_url", "environment", "api_base"):
            setattr(self, key, str(loaded.values.get(key, "")).strip())
        self.configuration_source = loaded.source
        self.api_base = self.api_base.rstrip("/")
        self.timeout = (5, 20)

    def configuration_issues(self, *, card=False):
        issues = []
        if not self.access_token or self.access_token.startswith(("TEST-000000", "APP_USR-000000")):
            issues.append("MP_ACCESS_TOKEN")
        if not self.webhook_secret or self.webhook_secret in {"test_webhook_secret_morcego", "defina_o_secret_configurado_no_webhook_mp"}:
            issues.append("MP_WEBHOOK_SECRET")
        if not re.fullmatch(r"[1-9][0-9]*", self.collector_id):
            issues.append("MP_COLLECTOR_ID")
        url = urlparse(self.webhook_url)
        if url.scheme != "https" or not url.netloc or url.username or url.fragment:
            issues.append("MP_WEBHOOK_URL (HTTPS)")
        if self.environment not in ("sandbox", "production"):
            issues.append("MP_ENVIRONMENT")
        if self.api_base != "https://api.mercadopago.com":
            issues.append("MP_API_BASE")
        if card and (not self.public_key or self.public_key.startswith(("TEST-000000", "APP_USR-000000"))):
            issues.append("MP_PUBLIC_KEY")
        return issues

    def require_configuration(self, *, card=False):
        if self.configuration_issues(card=card):
            raise GatewayError("NOT_CONFIGURED", "O pagamento online ainda não está disponível. Fale com a organização.")

    def _headers(self, key=None):
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        if key:
            headers["X-Idempotency-Key"] = str(key)
        return headers

    @sensitive_variables()
    def _request(self, method, path, *, payload=None, key=None, params=None):
        if not self.access_token or self.api_base != "https://api.mercadopago.com":
            raise GatewayError("NOT_CONFIGURED", "A integração de pagamentos não está configurada.")
        try:
            response = requests.request(method, f"{self.api_base}{path}", json=payload, params=params,
                                        headers=self._headers(key), timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise GatewayError("CONNECTION_ERROR", "A confirmação está demorando. Não faça outro pagamento enquanto verificamos.", uncertain=True) from exc
        if response.status_code not in (200, 201):
            uncertain = response.status_code >= 500 or response.status_code in (408, 409, 429)
            raise GatewayError(f"HTTP_{response.status_code}",
                "Não foi possível confirmar a operação. Aguarde a verificação." if uncertain else
                "O Mercado Pago não aceitou esta solicitação. Confira os dados ou fale com a organização.", uncertain=uncertain)
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise GatewayError("INVALID_RESPONSE", "Aguardando a confirmação do Mercado Pago.", uncertain=True) from exc
        if not isinstance(data, dict):
            raise GatewayError("INVALID_RESPONSE", "Aguardando a confirmação do Mercado Pago.", uncertain=True)
        return data

    def _base_payload(self, charge):
        order = charge.order
        from apps.core.utils import cents_to_reais
        breakdown = f'Itens R$ {cents_to_reais(charge.amount_cents-charge.passed_fee_cents)} + taxa R$ {cents_to_reais(charge.passed_fee_cents)} = R$ {cents_to_reais(charge.amount_cents)}'
        description=(f'Números extras do sorteio — {order.event.title[:75]}' if getattr(order,'is_raffle_purchase',False) else order.event.title[:100])
        return {"transaction_amount": float(Decimal(charge.amount_cents) / 100),
                "description": f"{description} - {order.merchant_reference} | {breakdown}"[:250],
                "external_reference": order.merchant_reference,
                "notification_url": self.webhook_url,
                "metadata": {"order_id": str(order.id), "charge_attempt_id": str(charge.id),
                    "items_cents": charge.amount_cents-charge.passed_fee_cents, "payment_fee_cents": charge.passed_fee_cents},
                "payer": {"email": order.purchaser_email}}

    def create_pix_payment(self, charge):
        self.require_configuration()
        payload = self._base_payload(charge)
        payload["payment_method_id"] = "pix"
        # Persisted before the request, so retries retain exactly the same expiration.
        payload["date_of_expiration"] = charge.pix_expiration.isoformat(timespec="milliseconds")
        names = charge.order.purchaser_name.split(maxsplit=1)
        if names:
            payload["payer"]["first_name"] = names[0]
        if len(names) > 1:
            payload["payer"]["last_name"] = names[1]
        document = re.sub(r"\D", "", charge.order.purchaser_document or "")
        if len(document) in (11, 14):
            payload["payer"]["identification"] = {"type": "CPF" if len(document) == 11 else "CNPJ", "number": document}
        return self._request("POST", "/v1/payments", payload=payload, key=charge.idempotency_key)

    @sensitive_variables()
    def create_card_payment(self, charge, *, token, payment_method_id, installments, issuer_id="", identification_type="", identification_number=""):
        self.require_configuration(card=True)
        if not token or len(token) > 256 or not re.fullmatch(r"[\w-]{1,50}", payment_method_id or "") or not 1 <= installments <= 12:
            raise GatewayError("INVALID_CARD_FORM", "Confira os dados do cartão e tente novamente.")
        payload = self._base_payload(charge)
        payload.update(token=token, payment_method_id=payment_method_id, installments=installments)
        if issuer_id:
            if not issuer_id.isdigit():
                raise GatewayError("INVALID_ISSUER", "Confira o banco emissor do cartão.")
            payload["issuer_id"] = issuer_id
        if identification_type in ("CPF", "CNPJ"):
            payload["payer"]["identification"] = {"type": identification_type, "number": re.sub(r"\D", "", identification_number)}
        return self._request("POST", "/v1/payments", payload=payload, key=charge.idempotency_key)

    def get_payment(self, payment_id):
        self.require_configuration()
        if not re.fullmatch(r"[0-9]{1,30}", str(payment_id)):
            raise GatewayError("INVALID_PAYMENT_ID", "Identificador de pagamento inválido.")
        return self._request("GET", f"/v1/payments/{payment_id}")

    def find_payments(self, charge):
        """Recover an ambiguous POST without creating another charge."""
        self.require_configuration()
        result = self._request("GET", "/v1/payments/search", params={"external_reference": charge.order.merchant_reference, "sort": "date_created", "criteria": "desc", "limit": 100})
        return [payment for payment in result.get("results", []) if
                str((payment.get("metadata") or {}).get("charge_attempt_id", "")) == str(charge.id)]

    def get_account(self):
        return self._request("GET", "/users/me")

    def verify_webhook_signature(self, x_signature, x_request_id, data_id):
        if not self.webhook_secret or self.webhook_secret == "test_webhook_secret_morcego" or not x_signature or not x_request_id or not data_id:
            return False
        parts = {}
        for part in x_signature.split(","):
            key, separator, value = part.strip().partition("=")
            if separator:
                parts[key.strip().lower()] = value.strip()
        ts, digest = parts.get("ts", ""), parts.get("v1", "")
        # Bound input before integer/float conversion. Both Unix seconds and
        # milliseconds occur in provider notifications; Unicode digits and
        # unbounded integers must fail authentication, never raise a 500.
        if not re.fullmatch(r"(?:[0-9]{1,10}|[0-9]{13})", ts) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            return False
        timestamp = int(ts) / (1000 if len(ts) == 13 else 1)
        tolerance = getattr(settings, "MP_WEBHOOK_MAX_AGE_SECONDS", 900)
        if tolerance and abs(time.time() - timestamp) > tolerance:
            return False
        manifest = f"id:{str(data_id).lower()};request-id:{x_request_id};ts:{ts};"
        expected = hmac.new(self.webhook_secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest.lower(), expected)
