"""Load an explicitly activated snapshot; never fall back after decryption failure."""
import base64
import json
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
