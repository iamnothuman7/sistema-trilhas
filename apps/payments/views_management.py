from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from .connection import can_manage_connection, can_view_connection, draft_values, save_draft, verify_draft, activate_draft, ConnectionConflict
from .forms_connection import MercadoPagoDraftForm, MercadoPagoOperationForm, MercadoPagoActivationForm
from .gateway import MercadoPagoGateway, GatewayError
from .gateway_config import CredentialStorageError, configured_webhook_url
from .models import MercadoPagoConnection


@login_required
@never_cache
@sensitive_post_parameters("access_token", "public_key", "webhook_secret")
@sensitive_variables()
@require_http_methods(["GET", "POST"])
def mercadopago_connection(request):
    if not can_view_connection(request.user):
        raise PermissionDenied("Acesso restrito à administração ou equipe financeira autorizada.")
    can_edit = can_manage_connection(request.user)
    if request.method == "POST" and not can_edit:
        raise PermissionDenied("Somente o proprietário global ou superusuário pode alterar a conta recebedora.")
    form = None
    activation_form = None
    error = ""
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "save_draft":
                form = MercadoPagoDraftForm(request.POST)
                if form.is_valid():
                    save_draft(form.cleaned_data, request.user)
                    messages.success(request, "Rascunho salvo com proteção. A configuração em uso não mudou.")
                    return redirect("gestao:mercadopago_connection")
                status = 400
            elif action in ("verify", "activate"):
                operation = MercadoPagoActivationForm(request.POST) if action == "activate" else MercadoPagoOperationForm(request.POST)
                if not operation.is_valid():
                    if action == "activate":
                        activation_form = operation
                    error = "Revise as confirmações obrigatórias e reabra a página se a versão estiver inválida."
                    status = 400
                else:
                    if action == "verify":
                        verify_draft(operation.cleaned_data["version"], request.user)
                        messages.success(request, "ID da conta recebedora conferido. Nenhuma cobrança foi criada; webhook, Public Key e homologação ainda precisam da conferência indicada abaixo.")
                    else:
                        activate_draft(operation.cleaned_data, request.user)
                        messages.success(request, "Configuração ativada para o ambiente escolhido. A ativação não cria cobrança nem confirma uma transação.")
                    return redirect("gestao:mercadopago_connection")
            else:
                error, status = "Operação inválida.", 400
        except (ConnectionConflict, CredentialStorageError, GatewayError) as exc:
            error, status = str(exc), 409
    connection = MercadoPagoConnection.objects.filter(pk=1).first()
    gateway = MercadoPagoGateway()
    version = connection.revision if connection else 0
    context = {
        "can_edit_connection": can_edit, "connection_error": error,
        "connection_source": gateway.configuration_source,
        "connection_complete": not gateway.configuration_issues(card=True),
        "active_environment": gateway.environment, "active_collector_id": gateway.collector_id,
        "activated_at": connection.activated_at if connection else None,
        "webhook_url": configured_webhook_url(),
    }
    if can_edit:
        try:
            draft = draft_values(connection)
            context.update(
                form=form or MercadoPagoDraftForm(initial={"version": version, "environment": draft["environment"], "collector_id": draft["collector_id"]}),
                operation_form=MercadoPagoOperationForm(initial={"version": version}),
                activation_form=activation_form or MercadoPagoActivationForm(initial={"version": version}),
                has_draft=bool(connection and connection.draft_ciphertext),
                account_verified_at=connection.verified_at if connection else None,
                verified_collector_id=connection.verified_collector_id if connection else "",
                draft_environment=draft["environment"],
                access_token_saved=bool(draft["access_token"]), public_key_saved=bool(draft["public_key"]),
                webhook_secret_saved=bool(draft["webhook_secret"]),
            )
        except CredentialStorageError as exc:
            context["connection_error"] = str(exc)
    response = render(request, "gestao/payments/connection.html", context, status=status)
    response["Referrer-Policy"] = "no-referrer"
    return response
