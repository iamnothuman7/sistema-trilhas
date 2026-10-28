from datetime import timedelta
from django.utils import timezone
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
from .models import MercadoPagoConnection, PaymentFeePolicy, FeeStatement
from .fees import PaymentFeeForm, policy, OFFICIAL_FEE_SOURCE


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
            if action in ("save_draft", "save_verify", "connect"):
                form = MercadoPagoDraftForm(request.POST)
                valid = form.is_valid()
                if valid and action == "connect" and not form.cleaned_data.get("confirm_connection"):
                    form.add_error("confirm_connection", "Confirme a conta, o ambiente e a configuração do webhook para concluir.")
                    valid = False
                if valid:
                    saved = save_draft(form.cleaned_data, request.user)
                    form = None  # Render the current revision even if verification fails.
                    if action in ("save_verify", "connect"):
                        verified = verify_draft(saved.revision, request.user)
                        if action == "connect":
                            activate_draft({"version": verified.revision, "confirm_account": True, "confirm_environment": True,
                                "confirm_webhook": True, "confirm_sandbox": True}, request.user)
                            messages.success(request, "Conta conectada e configuração ativada. Nenhuma cobrança foi criada. Confira a entrega das notificações em Webhooks no Mercado Pago.")
                        else:
                            messages.success(request, "Conta identificada e dados salvos. Confira o recebedor e conclua a ativação abaixo.")
                    else:
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
                form=form or MercadoPagoDraftForm(initial={"version": version, "environment": draft["environment"]}),
                operation_form=MercadoPagoOperationForm(initial={"version": version}),
                activation_form=activation_form or MercadoPagoActivationForm(initial={"version": version}),
                has_draft=bool(connection and connection.draft_ciphertext),
                account_verified_at=connection.verified_at if connection else None,
                verification_current=bool(connection and connection.verified_at and connection.verified_at >= timezone.now() - timedelta(minutes=30)),
                verified_collector_id=connection.verified_collector_id if connection else "",
                draft_environment=draft["environment"],
                access_token_saved=bool(draft["access_token"]), public_key_saved=bool(draft["public_key"]),
                webhook_secret_saved=bool(draft["webhook_secret"]),
            )
        except CredentialStorageError as exc:
            context["connection_error"] = str(exc)
    response = render(request, "gestao/payments/connection.html", context, status=status)
    response["Referrer-Policy"] = "same-origin"
    return response


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def mercadopago_fees(request):
    from decimal import Decimal, InvalidOperation
    from types import SimpleNamespace
    from django.db import transaction
    from apps.core.utils import cents_to_reais, log_audit
    from .fees import quote_for
    from .models import PaymentMethod
    if not can_view_connection(request.user):
        raise PermissionDenied
    can_edit = can_manage_connection(request.user)
    if request.method == 'POST' and not can_edit:
        raise PermissionDenied
    form = None
    status = 200
    if request.method == 'POST':
        with transaction.atomic():
            config, _ = PaymentFeePolicy.objects.get_or_create(pk=1)
            config = PaymentFeePolicy.objects.select_for_update().get(pk=config.pk)
            form = PaymentFeeForm(request.POST, instance=config, prefix='fees')
            if form.is_valid():
                saved = form.save(commit=False)
                saved.updated_by = request.user
                saved.save()
                log_audit(actor=request.user, action='PAYMENT_FEE_POLICY_UPDATED', entity_type='PaymentFeePolicy', entity_id=1,
                    diff={'fields': list(form.changed_data)}, request=request)
                messages.success(request, 'Regras salvas. O repasse de tarifas vale para novas cotações. A comissão para outra conta permanece em rascunho até conectar o split.')
                return redirect('gestao:mercadopago_fees')
            status = 400
    config = policy()
    try:
        amount = Decimal(request.GET.get('valor', '120').replace(',', '.'))
        if not amount.is_finite() or amount < Decimal('0.01') or amount > 100000:
            raise ValueError
        base = int(amount.quantize(Decimal('0.01')) * 100)
    except (ValueError, InvalidOperation):
        base = 12000
    quotes = []
    sample = SimpleNamespace(pk='simulation', base_total_cents=base)
    for method in [PaymentMethod.PIX, PaymentMethod.CREDIT_CARD]:
        quote = quote_for(sample, method, configuration=config)
        provider_rate = config.pix_bps if method == PaymentMethod.PIX else config.card_bps
        fixed = config.pix_fixed_cents if method == PaymentMethod.PIX else config.card_fixed_cents
        provider_fee = (quote.total * provider_rate + 5000)//10000 + fixed
        quotes.append({'method': method, 'name': 'Pix' if method == PaymentMethod.PIX else 'Cartão à vista',
            'items': cents_to_reais(base), 'passed': quote.formatted_fee, 'total': quote.formatted_total,
            'provider': cents_to_reais(provider_fee), 'net': cents_to_reais(quote.total-provider_fee)})
    statements = list(FeeStatement.objects.filter(fee_type='CHECKOUT_PASS_THROUGH').order_by('-reconciled_at')[:20])
    for item in statements:
        item.formatted_expected = cents_to_reais(item.expected_cents)
        item.formatted_actual = cents_to_reais(item.actual_cents) if item.actual_cents is not None else 'Aguardando'
        item.has_difference = item.actual_cents is not None and item.actual_cents != item.expected_cents
    return render(request, 'gestao/payments/fees.html', {'fee_form': form or PaymentFeeForm(instance=config, prefix='fees'),
        'can_edit_connection': can_edit, 'fee_policy': config, 'fee_source': OFFICIAL_FEE_SOURCE,
        'fee_statements': statements, 'fee_quotes': quotes, 'simulation_value': format(Decimal(base)/100, '.2f')}, status=status)
