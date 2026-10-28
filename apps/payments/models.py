import uuid
from django.db import models
from django.utils import timezone
from apps.core.utils import cents_to_reais
from apps.core.private_storage import private_document_storage


class MercadoPagoConnection(models.Model):
    """Central, encrypted credentials. Draft changes never replace the active snapshot."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=0)
    draft_ciphertext = models.TextField(blank=True, default="")
    active_ciphertext = models.TextField(blank=True, default="")
    verified_draft_digest = models.CharField(max_length=64, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_collector_id = models.CharField(max_length=30, blank=True, default="")
    active_environment = models.CharField(max_length=20, blank=True, default="")
    active_collector_id = models.CharField(max_length=30, blank=True, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey("identity.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="mp_connection_updates")

    class Meta:
        verbose_name = "Conexão central Mercado Pago"
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="mp_connection_singleton")]

    def __str__(self):
        return "Conexão Mercado Pago da Equipe Morcego"


class ChargeState(models.TextChoices):
    NEW = "NEW", "Nova Tentativa"
    SENT = "SENT", "Enviado ao Provedor"
    PENDING = "PENDING", "Pendente de Pagamento (Aguardando Piloto)"
    SUCCEEDED = "SUCCEEDED", "Pagamento Confirmado no Mercado Pago"
    FAILED = "FAILED", "Recusado / Falhou"
    UNKNOWN = "UNKNOWN", "Status Indeterminado (Em Conciliação)"
    CANCELED = "CANCELED", "Cancelado"


class PaymentFeePolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField("Repassar tarifas ao comprador", default=False)
    pix_pass_through = models.BooleanField('Comprador paga a tarifa Pix', default=True)
    card_pass_through = models.BooleanField('Comprador paga a tarifa do cartão', default=True)
    pix_bps = models.PositiveIntegerField(default=99)
    card_bps = models.PositiveIntegerField(default=498)
    pix_fixed_cents = models.PositiveIntegerField(default=0)
    card_fixed_cents = models.PositiveIntegerField(default=0)
    max_installments = models.PositiveIntegerField(default=1)
    installment_rates = models.JSONField(default=dict, blank=True)
    commission_requested = models.BooleanField('Preparar comissão opcional para outra conta', default=False)
    commission_bps = models.PositiveIntegerField(default=0)
    commission_fixed_cents = models.PositiveIntegerField(default=0)
    commission_payer = models.CharField('Quem pagaria a comissão', max_length=10, choices=[('BUYER', 'Comprador'), ('TEAM', 'Equipe')], default='TEAM')
    commission_recipient = models.CharField('Nome da empresa que receberia a comissão', max_length=120, blank=True)
    commission_account_id = models.CharField('ID da conta Mercado Pago de destino pretendida', max_length=30, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="payment_fee_policy_singleton")]

class PaymentMethod(models.TextChoices):
    PIX = "pix", "Pix Instantâneo"
    CREDIT_CARD = "credit_card", "Cartão de Crédito"
    MANUAL = "manual", "Lançamento Manual / Dinheiro"

class ChargeAttempt(models.Model):
    """
    Tentativa individual de cobrança executada junto ao Mercado Pago (Payments API).
    Idempotência garantida pela chave única.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("checkout.Order", on_delete=models.CASCADE, related_name="charge_attempts")
    receivable_due = models.ForeignKey("checkout.ReceivableDue", on_delete=models.SET_NULL, blank=True, null=True, related_name="charge_attempts")
    provider = models.CharField("Provedor", max_length=50, default="mercadopago")
    provider_order_id = models.CharField("ID da Ordem no MP", max_length=100, blank=True, null=True, db_index=True)
    provider_payment_ref = models.CharField("ID do Pagamento no MP", max_length=100, blank=True, null=True, db_index=True)
    idempotency_key = models.CharField("Chave de Idempotência", max_length=150, unique=True, db_index=True)

    payment_method = models.CharField("Meio de Pagamento", max_length=30, choices=PaymentMethod.choices, default=PaymentMethod.PIX)
    state = models.CharField("Situação", max_length=30, choices=ChargeState.choices, default=ChargeState.NEW, db_index=True)
    amount_cents = models.PositiveBigIntegerField("Valor da Tentativa (centavos)")
    passed_fee_cents = models.PositiveBigIntegerField(default=0)
    fee_snapshot = models.JSONField(default=dict, blank=True)

    # Dados específicos do Pix
    pix_qr_code = models.TextField("QR Code Imagem (Base64)", blank=True, null=True)
    pix_copy_paste = models.TextField("Código Pix Copia e Cola", blank=True, null=True)
    pix_expiration = models.DateTimeField("Expiração do QR Pix", blank=True, null=True)

    # Dados do Cartão
    card_last_four = models.CharField("Últimos 4 Dígitos", max_length=4, blank=True, null=True)
    card_brand = models.CharField("Bandeira", max_length=30, blank=True, null=True)
    card_installments = models.PositiveIntegerField("Parcelas", default=1)

    error_code = models.CharField("Código de Erro do Gateway", max_length=100, blank=True, null=True)
    error_message = models.TextField("Mensagem de Erro", blank=True, null=True)

    sent_at = models.DateTimeField("Enviado em", default=timezone.now)
    resolved_at = models.DateTimeField("Resolvido / Confirmado em", blank=True, null=True)
    last_checked_at = models.DateTimeField("Última consulta pelo comprador", blank=True, null=True)

    class Meta:
        verbose_name = "Tentativa de Pagamento"
        verbose_name_plural = "Tentativas de Pagamentos"
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Cobrança {self.payment_method.upper()} - R$ {cents_to_reais(self.amount_cents)} [{self.get_state_display()}]"


class ProviderEvent(models.Model):
    """
    Registro imutável de webhooks recebidos do Mercado Pago para auditoria e deduplicação.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField("Provedor", max_length=50, default="mercadopago")
    provider_event_id = models.CharField("ID do Evento no Provedor", max_length=150, blank=True, null=True, db_index=True)
    deduplication_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    resource_id = models.CharField("ID do Recurso", max_length=150, blank=True, null=True, db_index=True)
    event_type = models.CharField("Tipo do Evento", max_length=100)
    signature_valid = models.BooleanField("Assinatura Válida", default=False)
    payload_hash = models.CharField("Hash SHA-256 do Payload", max_length=64, db_index=True)
    raw_payload = models.JSONField("Payload Completo", default=dict)
    observed_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField("Processado em", blank=True, null=True)

    class Meta:
        verbose_name = "Evento de Webhook Recebido"
        verbose_name_plural = "Eventos de Webhook Recebidos"
        ordering = ["-observed_at"]


class PaymentTransaction(models.Model):
    """Validated provider snapshot. Locks prevent repeated ledger/fulfilment effects."""
    provider_payment_id = models.CharField(max_length=100, unique=True)
    charge_attempt = models.ForeignKey(ChargeAttempt, on_delete=models.PROTECT, related_name="transactions")
    provider_status = models.CharField(max_length=40)
    provider_updated_at = models.DateTimeField()
    amount_cents = models.PositiveBigIntegerField()
    posted_revenue_cents = models.PositiveBigIntegerField(default=0)
    posted_refund_cents = models.PositiveBigIntegerField(default=0)
    posted_fee_cents = models.PositiveBigIntegerField(default=0)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentAllocation(models.Model):
    """
    Alocação atômica do valor recebido entre as linhas do pedido ou obrigações.
    Garante rastreabilidade exata do que foi pago.
    """
    charge_attempt = models.ForeignKey(ChargeAttempt, on_delete=models.PROTECT, related_name="allocations")
    order_line = models.ForeignKey("checkout.OrderLine", on_delete=models.PROTECT, blank=True, null=True, related_name="allocations")
    receivable_due = models.ForeignKey("checkout.ReceivableDue", on_delete=models.PROTECT, blank=True, null=True, related_name="allocations")
    amount_cents = models.PositiveBigIntegerField("Valor Alocado (centavos)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Alocação de Pagamento"
        verbose_name_plural = "Alocações de Pagamento"


class RefundState(models.TextChoices):
    REQUESTED = "REQUESTED", "Solicitado"
    AUTHORIZED = "AUTHORIZED", "Autorizado pela Gestão"
    PROCESSING = "PROCESSING", "Em Processamento no Gateway"
    SUCCEEDED = "SUCCEEDED", "Reembolso Concluído"
    FAILED = "FAILED", "Falhou / Recusado"
    MANUAL_COMPLETED = "MANUAL_COMPLETED", "Concluído Manualmente via Pix"

class Refund(models.Model):
    """
    Devoluções financeiras totais ou parciais auditadas.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("checkout.Order", on_delete=models.PROTECT, related_name="refunds")
    line = models.ForeignKey("checkout.OrderLine", on_delete=models.PROTECT, blank=True, null=True, related_name="refunds")
    charge_attempt = models.ForeignKey(ChargeAttempt, on_delete=models.PROTECT, blank=True, null=True)
    request_key = models.CharField("Chave de Idempotência da Devolução", max_length=150, unique=True)
    amount_cents = models.PositiveBigIntegerField("Valor Devolvido (centavos)")
    state = models.CharField("Status", max_length=30, choices=RefundState.choices, default=RefundState.REQUESTED)
    provider_ref = models.CharField("Referência no Mercado Pago", max_length=100, blank=True, null=True)
    reason_code = models.CharField("Código do Motivo", max_length=50)
    reason_notes = models.TextField("Justificativa", blank=True, null=True)
    approved_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField("Concluído em", blank=True, null=True)

    class Meta:
        verbose_name = "Reembolso"
        verbose_name_plural = "Reembolsos"


class ManualPaymentEvidence(models.Model):
    """
    Comprovante de pagamento manual em dinheiro ou Pix direto autorizado.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("checkout.Order", on_delete=models.CASCADE, related_name="manual_evidences")
    method = models.CharField("Método Declarado", max_length=50, default="PIX_MANUAL")
    amount_cents = models.PositiveBigIntegerField("Valor (centavos)")
    proof_asset = models.FileField("Arquivo do Comprovante", storage=private_document_storage, upload_to="evidences/", blank=True, null=True)
    verified_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="verified_payments")
    verified_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField("Anotações da Conferência")

    class Meta:
        verbose_name = "Comprovante de Pagamento Manual"
        verbose_name_plural = "Comprovantes de Pagamentos Manuais"


class FeeStatement(models.Model):
    """
    Apuração da tarifa do intermediador: prevista vs real cobrada.
    """
    provider_order_id = models.CharField("ID da Ordem MP", max_length=100, db_index=True)
    fee_type = models.CharField("Tipo da Tarifa", max_length=50, default="GATEWAY_PROCESSING")
    expected_cents = models.PositiveIntegerField("Tarifa Estimada (centavos)")
    actual_cents = models.PositiveIntegerField("Tarifa Real Cobrada (centavos)", blank=True, null=True)
    source_payload_hash = models.CharField("Hash do Extrato", max_length=64, blank=True, null=True)
    reconciled_at = models.DateTimeField("Reconciliado em", blank=True, null=True)

    class Meta:
        verbose_name = "Demonstrativo de Tarifa"
        verbose_name_plural = "Demonstrativos de Tarifas"
