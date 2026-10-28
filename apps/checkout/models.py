import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.core.utils import cents_to_reais

class OrderStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    RESERVED = "RESERVED", "Vaga Reservada (Aguardando Pagamento)"
    AWAITING_PAYMENT = "AWAITING_PAYMENT", "Cobrança Gerada (Aguardando Confirmação)"
    PARTIALLY_PAID = "PARTIALLY_PAID", "Parcialmente Pago (Sinal Recebido)"
    PAID = "PAID", "Pagamento Confirmado"
    EXPIRED = "EXPIRED", "Expirado"
    CANCELED = "CANCELED", "Cancelado"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Reembolso Parcial"
    REFUNDED = "REFUNDED", "Reembolsado Integralmente"
    PAID_NEEDS_REVIEW = "PAID_NEEDS_REVIEW", "Pago com Necessidade de Revisão (Alerta Operacional)"


class PriceQuote(models.Model):
    """
    Cotação de preço imutável calculada no servidor com validade curta (padrão: 5 min).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="quotes")
    purchaser_id = models.UUIDField(blank=True, null=True)
    request_hash = models.CharField(max_length=64, db_index=True)
    currency = models.CharField(max_length=3, default="BRL")
    gross_cents = models.PositiveBigIntegerField("Subtotal Bruto (centavos)", default=0)
    discount_cents = models.PositiveBigIntegerField("Desconto Total (centavos)", default=0)
    total_cents = models.PositiveBigIntegerField("Total a Pagar (centavos)", default=0)
    expires_at = models.DateTimeField("Validade da Cotação")
    snapshot_json = models.JSONField("Snapshot dos Itens Cotados", default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cotação de Preço"
        verbose_name_plural = "Cotações de Preços"

    def is_valid(self):
        return timezone.now() <= self.expires_at


class Order(models.Model):
    """
    Pedido comercial agregado.
    Um pedido pode conter N inscrições (compra individual ou de equipe em grupo)
    ou pedidos adicionais de produtos.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="orders")
    merchant_reference = models.CharField("Referência Única do Pedido", max_length=50, unique=True, db_index=True)

    purchaser_user = models.ForeignKey("identity.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    purchaser_name = models.CharField("Nome do Comprador", max_length=150)
    purchaser_email = models.EmailField("E-mail do Comprador", db_index=True)
    purchaser_phone = models.CharField("Telefone / WhatsApp", max_length=30)
    purchaser_document = models.CharField("CPF do Pagador", max_length=20, blank=True, null=True)

    status = models.CharField("Status do Pedido", max_length=30, choices=OrderStatus.choices, default=OrderStatus.RESERVED, db_index=True)

    currency = models.CharField("Moeda", max_length=3, default="BRL")
    gross_cents = models.PositiveBigIntegerField("Valor Bruto (centavos)", default=0)
    discount_cents = models.PositiveBigIntegerField("Desconto Aplicado (centavos)", default=0)
    total_cents = models.PositiveBigIntegerField("Valor Líquido a Pagar (centavos)", default=0)
    payment_fee_cents = models.PositiveBigIntegerField("Taxa repassada no pagamento (centavos)", default=0)

    expires_at = models.DateTimeField("Expiração da Reserva (Hold)", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pedido #{self.merchant_reference} - {self.purchaser_name} (R$ {self.formatted_total}) [{self.get_status_display()}]"

    @property
    def formatted_total(self):
        return cents_to_reais(self.total_cents)

    @property
    def applied_coupon_code(self):
        try:
            return self.applied_coupon.display_code
        except OrderCoupon.DoesNotExist:
            return ''

    @property
    def is_raffle_purchase(self):
        return self.lines.filter(raffle__isnull=False).exists()

    @property
    def base_total_cents(self):
        return self.total_cents - self.payment_fee_cents

    @property
    def formatted_base_total(self):
        return cents_to_reais(self.base_total_cents)

    @property
    def formatted_payment_fee(self):
        return cents_to_reais(self.payment_fee_cents)

    @property
    def formatted_gross(self):
        return cents_to_reais(self.gross_cents)

    @property
    def formatted_discount(self):
        return cents_to_reais(self.discount_cents)

    def is_expired(self):
        return self.status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT) and timezone.now() > self.expires_at


class OrderLine(models.Model):
    """
    Linha do pedido correspondente a 1 inscrição ou item adicional.
    Guarda o snapshot imutável do valor e do destinatário no ato da compra.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, blank=True, null=True)
    lot = models.ForeignKey("catalog.Lot", on_delete=models.PROTECT, blank=True, null=True)
    addon = models.ForeignKey("catalog.AddOn", on_delete=models.PROTECT, blank=True, null=True)
    raffle = models.ForeignKey('events.MotorcycleRaffle',on_delete=models.PROTECT,blank=True,null=True,related_name='order_lines')
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, blank=True, null=True)

    # Vínculo opcional se esta linha for um adicional comprado para uma inscrição já existente
    linked_registration_id = models.UUIDField("Inscrição Vinculada", blank=True, null=True)

    # Dados do participante destinatário informado no checkout
    intended_person_name = models.CharField("Nome do Titular", max_length=150)
    intended_person_email = models.EmailField("E-mail do Titular", blank=True, null=True)
    intended_person_phone = models.CharField("WhatsApp do Titular", max_length=30, blank=True, null=True)
    intended_person_city = models.CharField("Cidade", max_length=100, default="Tianguá")
    intended_person_state = models.CharField("UF", max_length=2, default="CE")
    intended_person_team = models.CharField("Equipe / Moto Clube", max_length=120, blank=True, null=True)
    intended_variant_code = models.CharField("Tamanho da Camiseta / Variação", max_length=30, blank=True, null=True)

    quantity = models.PositiveIntegerField("Quantidade", default=1)
    unit_cents = models.PositiveBigIntegerField("Preço Unitário (centavos)", default=0)
    allocated_discount_cents = models.PositiveBigIntegerField("Desconto Rateado (centavos)", default=0)
    net_cents = models.PositiveBigIntegerField("Preço Líquido (centavos)", default=0)

    item_snapshot_json = models.JSONField("Snapshot Completo do Item no Ato da Venda", default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Linha do Pedido"
        verbose_name_plural = "Linhas do Pedido"

    def __str__(self):
        return f"{self.intended_person_name} - {self.product.name if self.product else 'Item'} (R$ {cents_to_reais(self.net_cents)})"


class OrderCoupon(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="applied_coupon")
    coupon = models.ForeignKey("catalog.Coupon", on_delete=models.PROTECT)
    coupon_code_snapshot = models.CharField('Código do cupom na compra', max_length=50, blank=True, default='', editable=False)
    usage_released_at = models.DateTimeField('Uso devolvido por reserva sem pagamento', blank=True, null=True, editable=False)
    discount_applied_cents = models.PositiveBigIntegerField("Desconto Aplicado (centavos)")
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Cupom Aplicado no Pedido"
        verbose_name_plural = "Cupons Aplicados nos Pedidos"

    @property
    def display_code(self):
        return self.coupon_code_snapshot or self.coupon.code_upper

    def save(self, *args, **kwargs):
        previous = None if self._state.adding else type(self).objects.filter(pk=self.pk).values_list('coupon_code_snapshot', flat=True).first()
        if previous and previous != self.coupon_code_snapshot:
            raise ValidationError('O código registrado na compra é histórico e não pode ser alterado.')
        if not self.coupon_code_snapshot:
            self.coupon_code_snapshot = self.coupon.code_upper
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'coupon_code_snapshot'}
        super().save(*args, **kwargs)


class DueType(models.TextChoices):
    FULL = "FULL", "Pagamento Integral"
    DEPOSIT = "DEPOSIT", "Sinal / Entrada"
    BALANCE = "BALANCE", "Saldo Restante"

class ReceivableDue(models.Model):
    """
    Obrigações contratuais do pedido (sinal, saldo ou pagamento único).
    Não confundir com parcelamento do cartão emitido pelo intermediador.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="receivables")
    due_type = models.CharField("Tipo da Parcela Comercial", max_length=20, choices=DueType.choices, default=DueType.FULL)
    due_at = models.DateTimeField("Data de Vencimento")
    amount_cents = models.PositiveBigIntegerField("Valor Devido (centavos)")
    allocated_paid_cents = models.PositiveBigIntegerField("Valor Já Quitado (centavos)", default=0)
    status = models.CharField("Situação", max_length=20, default="PENDING")  # PENDING, PAID, OVERDUE, CANCELED

    class Meta:
        verbose_name = "Parcela a Receber"
        verbose_name_plural = "Parcelas a Receber"

    def __str__(self):
        return f"{self.get_due_type_display()} - R$ {cents_to_reais(self.amount_cents)} ({self.status})"


class CheckoutSubmission(models.Model):
    """
    Deduplicação de submissão com chave de idempotência do cliente.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="submissions")
    client_idempotency_key = models.CharField("Chave de Idempotência do Cliente", max_length=150, unique=True)
    request_hash = models.CharField("Hash do Request", max_length=64)
    result_json = models.JSONField("Resultado Cacheado", default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
