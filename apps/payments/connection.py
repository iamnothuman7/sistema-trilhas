"""Permission and transition rules for the central Mercado Pago connection."""
import hashlib
import hmac
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.core.utils import log_audit
from apps.identity.models import RoleChoices
from apps.core.permissions import active_memberships
from .gateway import MercadoPagoGateway, GatewayError
from .gateway_config import (
    API_BASE, configured_webhook_url, environment_configuration,
    encrypt_configuration, decrypt_configuration,
)
from .models import MercadoPagoConnection, ChargeAttempt


class ConnectionConflict(Exception):
    pass


def can_manage_connection(user):
    return bool(user.is_authenticated and user.is_active and (
        user.is_superuser or active_memberships(user).filter(role_code=RoleChoices.OWNER, event__isnull=True).exists()))


def can_view_connection(user):
    return can_manage_connection(user) or active_memberships(user, "finance").exists()


@sensitive_variables()
def draft_values(connection):
    if connection and connection.draft_ciphertext:
        values = decrypt_configuration(connection.draft_ciphertext)
    elif connection and connection.active_ciphertext:
        values = decrypt_configuration(connection.active_ciphertext)
    else:
        values = environment_configuration()
    values["webhook_url"] = configured_webhook_url()
    values["api_base"] = API_BASE
    return values


def _locked(version):
    connection, _ = MercadoPagoConnection.objects.get_or_create(pk=1)
    connection = MercadoPagoConnection.objects.select_for_update().get(pk=connection.pk)
    if connection.revision != version:
        raise ConnectionConflict("Esta configuração mudou em outra aba. Reabra os dados atuais antes de continuar.")
    return connection


def _audit(connection, user, action, **extra):
    log_audit(actor=user, action=action, entity_type="MercadoPagoConnection", entity_id=connection.pk,
              diff={"revision": connection.revision, **extra})


@sensitive_variables()
@transaction.atomic
def save_draft(cleaned, user):
    connection = _locked(cleaned["version"])
    values = draft_values(connection)
    values.update(environment=cleaned["environment"], collector_id=cleaned["collector_id"])
    for name in ("access_token", "public_key", "webhook_secret"):
        if cleaned[name]:
            values[name] = cleaned[name]
    connection.draft_ciphertext = encrypt_configuration(values)
    connection.verified_at = None
    connection.verified_draft_digest = ""
    connection.verified_collector_id = ""
    connection.revision += 1
    connection.updated_by = user
    connection.save()
    _audit(connection, user, "MP_DRAFT_SAVED", environment=values["environment"], collector_id=values["collector_id"])
    return connection


@sensitive_variables()
def verify_draft(version, user):
    connection = MercadoPagoConnection.objects.filter(pk=1).first()
    if not connection or connection.revision != version or not connection.draft_ciphertext:
        raise ConnectionConflict("Salve o rascunho atual antes de testar a conexão.")
    values = decrypt_configuration(connection.draft_ciphertext)
    if values["webhook_url"] != configured_webhook_url():
        raise ConnectionConflict("A URL de notificações do servidor mudou. Salve o rascunho novamente antes de testar.")
    gateway = MercadoPagoGateway(configuration=values)
    if gateway.configuration_issues(card=True):
        raise GatewayError("INCOMPLETE_DRAFT", "Complete Access Token, Public Key, assinatura do webhook e ID recebedor antes de testar.")
    error = None
    try:
        # Fixed official host and path, no redirects, no charge, no returned account PII persisted.
        account = gateway.get_account()
        collector_id = str(account.get("id", ""))
        if not collector_id.isdecimal() or collector_id != values["collector_id"]:
            error = GatewayError("ACCOUNT_MISMATCH", "O token pertence a outra conta ou não retornou um ID válido. Confira o ID recebedor e as credenciais da Equipe Morcego.")
    except GatewayError as exc:
        error = GatewayError(exc.code, "Não foi possível conferir a conta no Mercado Pago. Verifique o token e tente novamente. Nenhuma cobrança foi criada.")
    with transaction.atomic():
        current = _locked(version)
        current.verified_at = None if error else timezone.now()
        current.verified_draft_digest = "" if error else hashlib.sha256(current.draft_ciphertext.encode()).hexdigest()
        current.verified_collector_id = "" if error else collector_id
        current.revision += 1
        current.updated_by = user
        current.save()
        _audit(current, user, "MP_ACCOUNT_VERIFIED" if not error else "MP_ACCOUNT_CHECK_FAILED",
               collector_id=current.verified_collector_id, code=error.code if error else "OK")
    if error:
        raise error
    return current


@sensitive_variables()
@transaction.atomic
def activate_draft(cleaned, user):
    connection = _locked(cleaned["version"])
    digest = hashlib.sha256(connection.draft_ciphertext.encode()).hexdigest()
    if not connection.verified_at or connection.verified_at < timezone.now() - timedelta(minutes=30) or not hmac.compare_digest(digest, connection.verified_draft_digest):
        raise ConnectionConflict("Teste novamente este rascunho antes de ativar. A conferência da conta vale por 30 minutos.")
    values = decrypt_configuration(connection.draft_ciphertext)
    gateway = MercadoPagoGateway(configuration=values)
    if gateway.configuration_issues(card=True) or connection.verified_collector_id != values["collector_id"] or values["webhook_url"] != configured_webhook_url():
        raise ConnectionConflict("A configuração está incompleta ou não corresponde à conta conferida.")
    if not all(cleaned.get(key) for key in ("confirm_account", "confirm_environment", "confirm_webhook")):
        raise ConnectionConflict("Confirme conta, ambiente e configuração do webhook antes de ativar.")
    if values["environment"] == "production" and not cleaned.get("confirm_sandbox"):
        raise ConnectionConflict("Conclua e confirme a homologação isolada antes de liberar pagamentos reais.")
    previous = decrypt_configuration(connection.active_ciphertext) if connection.active_ciphertext else environment_configuration()
    # Checkout locks this same singleton before Order and reloads the gateway while holding it.
    # Thus activation cannot race with creation of the first ChargeAttempt.
    previous_complete = not MercadoPagoGateway(configuration=previous).configuration_issues(card=True)
    changed_identity = any(previous.get(key) != values[key] for key in ("collector_id", "environment", "public_key", "webhook_secret", "webhook_url"))
    if ChargeAttempt.objects.filter(provider="mercadopago").exists() and (not previous_complete or changed_identity):
        raise ConnectionConflict("Há tentativas de pagamento anteriores. A troca de conta, ambiente, aplicação ou assinatura do webhook exige manutenção coordenada para preservar cobranças e notificações em andamento. O painel permite renovar o Access Token da mesma configuração.")
    connection.active_ciphertext = connection.draft_ciphertext
    connection.active_environment = values["environment"]
    connection.active_collector_id = values["collector_id"]
    connection.activated_at = timezone.now()
    connection.revision += 1
    connection.updated_by = user
    connection.save()
    _audit(connection, user, "MP_CONNECTION_ACTIVATED", environment=values["environment"], collector_id=values["collector_id"],
           sandbox_confirmed=bool(cleaned.get("confirm_sandbox")))
    return connection
