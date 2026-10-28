"""Load an explicitly activated snapshot; never fall back after decryption failure."""
import base64
import json
import hashlib
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

API_BASE = "https://api.mercadopago.com"
DEFAULT_WEBHOOK_URL = "https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/"
CONFIG_KEYS = ("access_token", "public_key", "webhook_secret", "collector_id", "webhook_url", "environment", "api_base")


class CredentialStorageError(Exception):
    pass


@dataclass(repr=False)
class GatewayConfiguration:
    values: dict = field(repr=False)
    source: str = "environment"


@sensitive_variables()
def _fernet():
    material = getattr(settings, "MP_CREDENTIALS_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"trilhas-mp-config-v1",
               info=b"mercado-pago-stored-credentials").derive(material.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


@sensitive_variables()
def encrypt_configuration(values):
    try:
        payload = {key: str(values.get(key, "")) for key in CONFIG_KEYS}
        return _fernet().encrypt(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    except (ValueError, TypeError, AttributeError) as exc:
        raise CredentialStorageError("Não foi possível proteger a configuração. Consulte a administração do servidor.") from None


@sensitive_variables()
def decrypt_configuration(ciphertext):
    try:
        value = json.loads(_fernet().decrypt(ciphertext.encode("ascii")))
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) for key in CONFIG_KEYS):
            raise ValueError
        return {key: value[key] for key in CONFIG_KEYS}
    except (InvalidToken, ValueError, TypeError, UnicodeError, AttributeError):
        raise CredentialStorageError("A chave do servidor não consegue abrir a configuração salva. Restaure a chave correta antes de continuar.") from None


@sensitive_variables()
def environment_configuration():
    return {key: str(getattr(settings, "MP_" + key.upper(), "sandbox" if key == "environment" else API_BASE if key == "api_base" else "")).strip()
            for key in CONFIG_KEYS}


def configured_webhook_url():
    # Server configuration only: never use an untrusted Host header or a user-submitted URL.
    return str(getattr(settings, "MP_WEBHOOK_URL", "") or DEFAULT_WEBHOOK_URL).strip()


@sensitive_variables()
def load_gateway_configuration():
    from .models import MercadoPagoConnection
    connection = MercadoPagoConnection.objects.filter(pk=1).only("active_ciphertext").first()
    if not connection or not connection.active_ciphertext:
        return GatewayConfiguration(environment_configuration())
    try:
        values = decrypt_configuration(connection.active_ciphertext)
    except CredentialStorageError:
        # Falling back to environment here could silently redirect money to another account.
        return GatewayConfiguration({key: "" for key in CONFIG_KEYS}, source="unavailable")
    return GatewayConfiguration(values, source="panel")


def deployment_environment():
    """Server-controlled separation: the panel cannot turn homologation into production."""
    value = str(getattr(settings, "MP_ENVIRONMENT", "sandbox")).strip()
    return value if value in ("sandbox", "production") else ""


@sensitive_variables()
def configuration_identity(values):
    # Token rotation preserves the account/application identity. Signature rotation
    # retains its predecessor so delayed notifications can still authenticate.
    payload = {key: values[key] for key in CONFIG_KEYS if key != "access_token"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def checkout_identity(gateway):
    return hashlib.sha256(json.dumps({"collector_id": gateway.collector_id, "environment": gateway.environment,
        "public_key": gateway.public_key}, sort_keys=True).encode()).hexdigest()


@sensitive_variables()
def retain_configuration(values):
    """Call under the central connection lock before exposing a new active account."""
    from .models import MercadoPagoAccountConfiguration
    account, created = MercadoPagoAccountConfiguration.objects.get_or_create(
        identity_digest=configuration_identity(values), defaults={
            "credentials_ciphertext": encrypt_configuration(values),
            "collector_id": values["collector_id"], "environment": values["environment"],
        })
    if not created:
        previous = decrypt_configuration(account.credentials_ciphertext)
        if previous["access_token"] != values["access_token"]:
            account.credentials_ciphertext = encrypt_configuration(values)
            account.save(update_fields=["credentials_ciphertext", "updated_at"])
    return account


@sensitive_variables()
def charge_gateway(charge):
    """Legacy unbound attempts use the current account until atomically pinned on switch."""
    from .gateway import MercadoPagoGateway
    from .models import ChargeAttempt, MercadoPagoAccountConfiguration
    # Read the active configuration before the FK. Activation pins all legacy
    # attempts before publishing the new account in the same transaction. This
    # read order avoids a null-FK/just-switched-active-account race without
    # reversing the checkout lock order (Connection -> Order -> Charge).
    current = load_gateway_configuration()
    # An object fetched before activation may still have a stale null FK even
    # though activation pinned it in the database while this request was in flight.
    account_id = ChargeAttempt.objects.values_list("account_configuration_id", flat=True).get(pk=charge.pk)
    if not account_id:
        return MercadoPagoGateway(configuration=current.values)
    try:
        ciphertext = MercadoPagoAccountConfiguration.objects.only("credentials_ciphertext").get(pk=account_id).credentials_ciphertext
        values = decrypt_configuration(ciphertext)
    except CredentialStorageError:
        values = {key: "" for key in CONFIG_KEYS}
    return MercadoPagoGateway(configuration=values)


@sensitive_variables()
def webhook_gateways(payment_id):
    """Prefer the pinned payment account; include historical signature rotations."""
    from .gateway import MercadoPagoGateway
    from .models import ChargeAttempt, MercadoPagoAccountConfiguration
    known = ChargeAttempt.objects.filter(provider="mercadopago", provider_payment_ref=payment_id).select_related("account_configuration").first()
    candidates = [charge_gateway(known)] if known else []
    candidates.append(MercadoPagoGateway())
    for account in MercadoPagoAccountConfiguration.objects.filter(environment=deployment_environment()).order_by("-updated_at"):
        try:
            candidates.append(MercadoPagoGateway(configuration=decrypt_configuration(account.credentials_ciphertext)))
        except CredentialStorageError:
            continue
    seen = set()
    for gateway in candidates:
        identity = (gateway.collector_id, gateway.environment, gateway.webhook_secret, gateway.access_token)
        if identity in seen or gateway.environment != deployment_environment() or gateway.configuration_issues():
            continue
        seen.add(identity)
        yield gateway
