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
    deployment_environment, retain_configuration,
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
    if values["environment"] != cleaned["environment"] or cleaned.get("operation_mode") == "replace":
        # A blank field must never carry test credentials into a real-money setup.
        for name in ("access_token", "public_key", "webhook_secret", "collector_id"):
            values[name] = ""
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
    if values["environment"] != deployment_environment():
        raise ConnectionConflict("Este servidor está separado para " + ("pagamentos reais" if deployment_environment() == "production" else "homologação") + ". Use o painel do ambiente correspondente; trocar a seleção não transforma credenciais reais em credenciais de teste.")
    if values["webhook_url"] != configured_webhook_url():
        raise ConnectionConflict("A URL de notificações do servidor mudou. Salve o rascunho novamente antes de testar.")
    gateway = MercadoPagoGateway(configuration=values)
    issues = [issue for issue in gateway.configuration_issues(card=True) if issue != "MP_COLLECTOR_ID"]
    if issues:
        labels = {"MP_ACCESS_TOKEN": "Access Token", "MP_PUBLIC_KEY": "Public Key", "MP_WEBHOOK_SECRET": "assinatura secreta do webhook"}
        missing = ", ".join(labels.get(issue, "configuração do servidor") for issue in issues)
        raise GatewayError("INCOMPLETE_DRAFT", f"Falta preencher: {missing}. Cole as credenciais da mesma aplicação e salve novamente.")
    error = None
    try:
        # Fixed official host and path, no redirects, no charge, no returned account PII persisted.
        account = gateway.get_account()
        collector_id = str(account.get("id", ""))
        tags = account.get("tags")
        test_account = isinstance(tags, list) and "test_user" in tags
        test_token = values["access_token"].startswith("TEST-")
        test_public = values["public_key"].startswith("TEST-")
        app_pair = all(values[key].startswith("APP_USR-") for key in ("access_token", "public_key"))
        if test_token != test_public:
            error = GatewayError("CREDENTIAL_PAIR_MISMATCH", "A Public Key e o Access Token pertencem a ambientes diferentes. Copie o par completo da mesma aplicação. Nenhuma cobrança foi criada.")
        elif values["environment"] == "sandbox" and not ((test_token and test_public) or (app_pair and test_account)):
            error = GatewayError("ACCOUNT_ENVIRONMENT_MISMATCH", "Não foi possível comprovar credenciais de teste. Use o par TEST da aplicação ou o par APP_USR de um usuário de teste identificado pelo Mercado Pago. Nenhuma cobrança foi criada.")
        elif values["environment"] == "production" and (not app_pair or test_account):
            error = GatewayError("ACCOUNT_ENVIRONMENT_MISMATCH", "Esta credencial não corresponde a uma conta real em produção. Copie o par de produção da conta recebedora. Nenhuma cobrança foi criada.")
        if not collector_id.isascii() or not collector_id.isdecimal() or len(collector_id) > 30 or collector_id.startswith("0"):
            error = GatewayError("INVALID_ACCOUNT", "O Mercado Pago não retornou uma conta válida. Confira o Access Token da aplicação.")
        elif values["collector_id"] and collector_id != values["collector_id"]:
            error = GatewayError("ACCOUNT_MISMATCH", "O ID informado corresponde a outra conta. Ele não é o número da aplicação. Deixe o ID opcional vazio e clique em Salvar e conferir conta para identificar o recebedor automaticamente.")
    except GatewayError as exc:
        reasons = {
            "HTTP_401": "O Access Token foi recusado. Copie o token completo em Credenciais de produção da aplicação da Equipe Morcego.",
            "HTTP_403": "O Mercado Pago negou acesso à conta. Confira se as credenciais estão ativadas e se pertencem à aplicação Checkout Transparente via Payments.",
            "HTTP_429": "O Mercado Pago limitou as consultas. Aguarde alguns minutos e use Testar conta sem cobrar.",
            "CONNECTION_ERROR": "Não foi possível alcançar o Mercado Pago. As chaves continuam salvas; tente conferir a conta novamente.",
        }
        error = GatewayError(exc.code, reasons.get(exc.code, "O Mercado Pago não confirmou a conta agora. Confira o token e tente novamente.") + " Nenhuma cobrança foi criada.")
    with transaction.atomic():
        current = _locked(version)
        if not error and not values["collector_id"]:
            values["collector_id"] = collector_id
            current.draft_ciphertext = encrypt_configuration(values)
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
    if values["environment"] != deployment_environment():
        raise ConnectionConflict("O ambiente desta conta não corresponde ao ambiente do servidor.")
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
    unbound = ChargeAttempt.objects.filter(provider="mercadopago", account_configuration__isnull=True)
    if unbound.exists():
        if not previous_complete or previous["environment"] != values["environment"]:
            raise ConnectionConflict("Existem cobranças antigas sem uma configuração recuperável. Restaure a configuração anterior neste mesmo ambiente antes de trocar a conta.")
        historical = retain_configuration(previous)
        unbound.update(account_configuration=historical)
    account = retain_configuration(values)
    token_rotation = all(previous.get(key) == values[key] for key in ("collector_id", "environment", "public_key", "webhook_secret", "webhook_url"))
    # Configurations with an older webhook secret still share this token's
    # account/application, including when switching back to a previous account.
    # Preserve each secret while updating that application's verified token.
    from .models import MercadoPagoAccountConfiguration
    for historical in MercadoPagoAccountConfiguration.objects.filter(collector_id=values["collector_id"], environment=values["environment"]):
        historical_values = decrypt_configuration(historical.credentials_ciphertext)
        if historical_values["public_key"] == values["public_key"] and historical_values["access_token"] != values["access_token"]:
            historical_values["access_token"] = values["access_token"]
            historical.credentials_ciphertext = encrypt_configuration(historical_values)
            historical.save(update_fields=["credentials_ciphertext", "updated_at"])
    connection.active_ciphertext = connection.draft_ciphertext
    connection.active_environment = values["environment"]
    connection.active_collector_id = values["collector_id"]
    connection.activated_at = timezone.now()
    connection.revision += 1
    connection.updated_by = user
    connection.save()
    _audit(connection, user, "MP_CONNECTION_ACTIVATED", environment=values["environment"], collector_id=values["collector_id"],
           sandbox_confirmed=bool(cleaned.get("confirm_sandbox")), operation="TOKEN_ROTATION" if token_rotation else "ACCOUNT_OR_APPLICATION_SWITCH", account_snapshot=str(account.pk))
    return connection
