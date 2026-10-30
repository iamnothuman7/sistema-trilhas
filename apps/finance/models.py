import uuid
from django.db import models
from django.utils import timezone
from apps.core.utils import cents_to_reais
from apps.core.private_storage import private_document_storage

class FinancialEntryKind(models.TextChoices):
    RECEITA_SORTEIO = "RECEITA_SORTEIO", "Recebimento de Bilhete de Sorteio"
    ESTORNO_SORTEIO = "ESTORNO_SORTEIO", "Reversão de Bilhete de Sorteio"
    RECEITA_INSCRICAO = "RECEITA_INSCRICAO", "Receita de Inscrição (Mercado Pago)"
    RECEITA_ADICIONAL = "RECEITA_ADICIONAL", "Receita de Item Adicional"
    RECEITA_MANUAL = "RECEITA_MANUAL", "Receita Manual Confirmada (Dinheiro/Pix)"
    RECEITA_PATROCINIO = "RECEITA_PATROCINIO", "Recebimento de Patrocínio"
    ESTORNO_PATROCINIO = "ESTORNO_PATROCINIO", "Reversão de Recebimento de Patrocínio"
    TAXA_MERCADO_PAGO = "TAXA_MERCADO_PAGO", "Tarifa de Processamento Mercado Pago"
    REEMBOLSO = "REEMBOLSO", "Estorno / Reembolso de Inscrição"
    DESPESA = "DESPESA", "Despesa Operacional / Fornecedor"
    TRANSFERENCIA_BANCO = "TRANSFERENCIA_BANCO", "Transferência MP para Conta Bancária"

class FinancialEntry(models.Model):
    """
    Livro razão gerencial imutável (append-only ledger).
    Todas as receitas e despesas por evento são registradas aqui.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="financial_entries")
    raffle = models.ForeignKey('events.MotorcycleRaffle', on_delete=models.PROTECT, null=True, blank=True, related_name='financial_entries')
    order = models.ForeignKey("checkout.Order", on_delete=models.SET_NULL, blank=True, null=True, related_name="financial_entries")
    movement = models.ForeignKey('finance.ManualMovement', on_delete=models.PROTECT, null=True, blank=True, related_name='entries')
    kind = models.CharField("Tipo do Lançamento", max_length=40, choices=FinancialEntryKind.choices)
    amount_cents_signed = models.BigIntegerField("Valor com Sinal (+/- em centavos)")
    recognized_at = models.DateTimeField("Data de Competência / Reconhecimento", default=timezone.now)
    source_reference = models.CharField("Referência da Origem", max_length=150, db_index=True)
    posted_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, blank=True, null=True)
    description = models.CharField("Descrição do Lançamento", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lançamento Financeiro"
        verbose_name_plural = "Lançamentos Financeiros"
        ordering = ["-recognized_at"]

    def __str__(self):
        return f"[{self.get_kind_display()}] R$ {cents_to_reais(abs(self.amount_cents_signed))} ({self.event.title})"

    @property
    def formatted_amount(self):
        val = cents_to_reais(abs(self.amount_cents_signed))
        prefix = "+" if self.amount_cents_signed >= 0 else "-"
        return f"{prefix}R$ {val}"


class SponsorPaymentMethod(models.TextChoices):
    PIX = "PIX", "Pix"
    TRANSFER = "TRANSFER", "Transferência bancária"
    CASH = "CASH", "Dinheiro"
    CARD = "CARD", "Cartão / recebimento externo"
    OTHER = "OTHER", "Outro recebimento"


class SponsorPayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sponsorship = models.ForeignKey("events.Sponsor", on_delete=models.PROTECT, related_name="payments")
    amount_cents = models.PositiveBigIntegerField()
    received_on = models.DateField()
    method = models.CharField(max_length=20, choices=SponsorPaymentMethod.choices)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, null=True, related_name="sponsor_receipts")
    created_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.UUIDField(unique=True)
    submission_hash = models.CharField(max_length=64)
    ledger_entry = models.OneToOneField(FinancialEntry, on_delete=models.PROTECT, related_name="sponsor_receipt")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="sponsor_reversals")
    reversal_reason = models.CharField(max_length=500, blank=True)
    reversal_entry = models.OneToOneField(FinancialEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="sponsor_reversal")

    class Meta:
        ordering = ["-received_on", "-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount_cents__gt=0), name="sponsor_receipt_positive")]


class Vendor(models.Model):
    """
    Fornecedor cadastrado (ex: confecção de camisas, troféus, buffet, som).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("core.Organization", on_delete=models.CASCADE, related_name="vendors")
    name = models.CharField("Nome da Empresa / Fornecedor", max_length=150)
    category = models.CharField("Categoria", max_length=100, default="Confecção de Camisetas / Kits")
    contact_name = models.CharField("Pessoa de Contato", max_length=100, blank=True, null=True)
    contact_phone = models.CharField("WhatsApp / Telefone", max_length=30, blank=True, null=True)
    contact_email = models.EmailField("E-mail", blank=True, null=True)
    pix_key = models.CharField("Chave Pix", max_length=100, blank=True, null=True)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "Fornecedor"
        verbose_name_plural = "Fornecedores"

    def __str__(self):
        return f"{self.name} ({self.category})"


class ExpenseCategory(models.TextChoices):
    KITS_CAMISETAS = "KITS_CAMISETAS", "Confecção de Camisetas e Kits"
    ALIMENTACAO = "ALIMENTACAO", "Alimentação, Café da Manhã e Almoço"
    ESTRUTURA = "ESTRUTURA", "Estrutura, Som, Pódio e Tendas"
    PREMIACOES = "PREMIACOES", "Troféus e Medalhas"
    RESGATE = "RESGATE", "Combustível, Carro de Apoio e Resgate"
    MARKETING = "MARKETING", "Marketing, Mídia e Cobertura Fotográfica"
    TAXAS_LICENCAS = "TAXAS_LICENCAS", "Taxas e Licenças"
    OUTROS = "OUTROS", "Outras Despesas Operacionais"

class ExpenseStatus(models.TextChoices):
    PENDING = "PENDING", "A pagar"
    PAID = "PAID", "Paga"
    CANCELED = "CANCELED", "Cancelada"

class Expense(models.Model):
    """
    Despesa operacional registrada para apuração do resultado líquido real da edição.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="expenses")
    raffle = models.ForeignKey('events.MotorcycleRaffle', on_delete=models.PROTECT, null=True, blank=True, related_name='expenses')
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, blank=True, null=True, related_name="expenses")
    category = models.CharField("Categoria de Custo", max_length=40, choices=ExpenseCategory.choices, default=ExpenseCategory.KITS_CAMISETAS)
    description = models.CharField("Descrição do Gasto", max_length=200)
    amount_cents = models.PositiveBigIntegerField("Valor da Despesa (centavos)")
    due_at = models.DateField("Data de Vencimento")
    paid_at = models.DateField("Data do Pagamento", blank=True, null=True)
    status = models.CharField("Situação", max_length=20, choices=ExpenseStatus.choices, default=ExpenseStatus.PAID)
    idempotency_key = models.UUIDField("Identificação do lançamento", unique=True, null=True, blank=True, editable=False)
    submission_hash = models.CharField(max_length=64, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    proof_asset = models.FileField("Comprovante / Recibo", storage=private_document_storage, upload_to="expenses/receipts/", blank=True, null=True)
    notes = models.TextField("Observações", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Despesa do Evento"
        verbose_name_plural = "Despesas do Evento"
        ordering = ["-due_at"]

    def __str__(self):
        return f"{self.description} - R$ {cents_to_reais(self.amount_cents)} ({self.get_status_display()})"

    @property
    def formatted_amount(self):
        return cents_to_reais(self.amount_cents)


class ManualMovement(models.Model):
    """Editable source document; its ledger entries remain immutable."""
    class Kind(models.TextChoices):
        INCOME = 'INCOME', 'Entrada avulsa recebida'
        TRANSFER = 'TRANSFER', 'Transferência entre contas próprias'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey('events.Event', on_delete=models.PROTECT, related_name='manual_movements')
    kind = models.CharField('Tipo', max_length=20, choices=Kind.choices)
    description = models.CharField('Descrição', max_length=200)
    amount_cents = models.PositiveBigIntegerField()
    occurred_on = models.DateField('Data da movimentação')
    method = models.CharField('Forma de recebimento / transferência', max_length=20, choices=SponsorPaymentMethod.choices)
    reference = models.CharField('Referência ou contas de origem e destino', max_length=150, blank=True)
    notes = models.TextField('Observações', blank=True)
    proof_asset = models.FileField('Comprovante', storage=private_document_storage, upload_to='finance/movements/', blank=True)
    revision = models.PositiveIntegerField(default=0, editable=False)
    request_key = models.UUIDField(unique=True, editable=False)
    submission_hash = models.CharField(max_length=64, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey('identity.User', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurred_on', '-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(amount_cents__gt=0), name='manual_movement_positive')]

    @property
    def formatted_amount(self):
        return cents_to_reais(self.amount_cents)
