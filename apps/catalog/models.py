import uuid
from django.db import models
from django.utils import timezone
from apps.core.utils import cents_to_reais

class ProductKind(models.TextChoices):
    PILOTO = "PILOTO", "Piloto Completo"
    ACOMPANHANTE = "ACOMPANHANTE", "Acompanhante / Garupa"
    APOIO = "APOIO", "Equipe de Apoio / Mecânico"
    EXPEDICAO_VIP = "EXPEDICAO_VIP", "Pacote Expedição VIP"

class Product(models.Model):
    """
    Modalidade ou pacote comercial de participação.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="products")
    kind = models.CharField("Modalidade", max_length=40, choices=ProductKind.choices, default=ProductKind.PILOTO)
    name = models.CharField("Nome do Pacote", max_length=150)
    description = models.TextField("Benefícios Inclusos", blank=True, null=True)
    active = models.BooleanField("Ativo para Venda", default=True)
    includes_shirt = models.BooleanField("Inclui Camiseta Oficial", default=True)
    includes_meal = models.BooleanField("Inclui Café da Manhã / Almoço", default=True)
    includes_kit = models.BooleanField("Inclui Kit e Pulseira", default=True)
    default_benefits_json = models.JSONField("Lista de Direitos / Entitlements", default=list, blank=True)

    class Meta:
        verbose_name = "Produto / Modalidade"
        verbose_name_plural = "Produtos / Modalidades"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.event.title})"


class Lot(models.Model):
    """
    Lote de vendas por período e/ou quantidade limitada.
    Preço armazenado como inteiro em centavos (ex: R$ 180,00 = 18000).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="lots")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="lots")
    title = models.CharField("Nome do Lote", max_length=100)  # ex: 1º Lote Promocional
    price_cents = models.PositiveBigIntegerField("Preço (em centavos)", default=18000)
    opens_at = models.DateTimeField("Data e Hora de Abertura")
    closes_at = models.DateTimeField("Data e Hora de Fechamento")
    max_quantity = models.PositiveIntegerField("Quantidade Máxima de Vagas", default=100)
    current_sold = models.PositiveIntegerField("Vendidos / Comprometidos", default=0)
    priority = models.PositiveIntegerField("Ordem de Prioridade", default=1)
    active = models.BooleanField("Lote Ativo", default=True)

    class Meta:
        verbose_name = "Lote de Inscrição"
        verbose_name_plural = "Lotes de Inscrição"
        ordering = ["priority", "opens_at"]

    def __str__(self):
        return f"{self.title} - {self.product.name} (R$ {self.formatted_price})"

    @property
    def formatted_price(self):
        return cents_to_reais(self.price_cents)

    @property
    def is_open(self):
        now = timezone.now()
        return self.active and (self.opens_at <= now <= self.closes_at) and (self.current_sold < self.max_quantity)


class ProductVariant(models.Model):
    """
    Variação de produto, como tamanho de camiseta ou modelo de veículo.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    kind = models.CharField("Tipo", max_length=50, default="SHIRT_SIZE")  # SHIRT_SIZE, VEHICLE_TYPE
    code = models.CharField("Código da Variante", max_length=20)  # P, M, G, GG, XG, FEMININO_M
    label = models.CharField("Descrição para o Usuário", max_length=100)
    extra_price_cents = models.PositiveBigIntegerField("Acréscimo de Preço (centavos)", default=0)

    class Meta:
        unique_together = ("product", "kind", "code")
        verbose_name = "Variação de Produto"
        verbose_name_plural = "Variações de Produtos"

    def __str__(self):
        return f"{self.product.name} - {self.label}"


class AddOn(models.Model):
    """
    Itens extras que podem ser adquiridos (camisa avulsa, refeição extra, boné).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="addons")
    title = models.CharField("Nome do Item Adicional", max_length=150)
    description = models.TextField("Descrição", blank=True)
    variant_code = models.CharField("Tamanho / variação", max_length=30, blank=True)
    price_cents = models.PositiveBigIntegerField("Preço (centavos)", default=5000)
    max_quantity = models.PositiveIntegerField("Limite Disponível", default=100)
    active = models.BooleanField("Ativo", default=True)

    class Meta:
        verbose_name = "Item Adicional"
        verbose_name_plural = "Itens Adicionais"

    def __str__(self):
        return f"{self.title} (R$ {cents_to_reais(self.price_cents)})"

    @property
    def formatted_price(self):
        return cents_to_reais(self.price_cents)


class CouponKind(models.TextChoices):
    PERCENT = "PERCENT", "Percentual (%)"
    FIXED = "FIXED", "Valor Fixo (R$)"

class Coupon(models.Model):
    """
    Cupom de desconto com aplicação atômica e controle de uso máximo.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="coupons")
    code_upper = models.CharField("Código do Cupom", max_length=50, db_index=True)
    kind = models.CharField("Tipo de Desconto", max_length=20, choices=CouponKind.choices, default=CouponKind.FIXED)
    value_cents = models.PositiveBigIntegerField("Valor do Desconto em Centavos", default=2000)
    percent_off = models.PositiveIntegerField("Percentual de Desconto (1-100)", default=10)
    max_uses = models.PositiveIntegerField("Quantidade Máxima de Usos", default=50)
    used_count = models.PositiveIntegerField("Quantidade Já Utilizada", default=0)
    valid_from = models.DateTimeField("Válido a partir de")
    valid_until = models.DateTimeField("Válido até")
    max_per_buyer = models.PositiveIntegerField("Máximo por Comprador", default=1)
    active = models.BooleanField("Ativo", default=True)

    class Meta:
        unique_together = ("event", "code_upper")
        verbose_name = "Cupom de Desconto"
        verbose_name_plural = "Cupons de Desconto"

    def save(self, *args, **kwargs):
        self.code_upper = self.code_upper.upper().strip()
        super().save(*args, **kwargs)

    def is_valid(self):
        now = timezone.now()
        return self.active and (self.valid_from <= now <= self.valid_until) and (self.used_count < self.max_uses)


class CapacityResourceType(models.TextChoices):
    RAFFLE_TOTAL = 'RAFFLE_TOTAL', 'Números extras online do sorteio'
    ADDON_TOTAL = "ADDON_TOTAL", "Estoque de item adicional"
    EVENT_OVERALL = "EVENT_OVERALL", "Capacidade Geral do Evento"
    LOT_TOTAL = "LOT_TOTAL", "Capacidade do Lote"
    SHIRT_SIZE = "SHIRT_SIZE", "Estoque de Camisetas por Tamanho"
    HOTEL_BEDS = "HOTEL_BEDS", "Vagas de Hospedagem"

class CapacityResource(models.Model):
    """
    Recurso de capacidade sob controle de concorrência com SELECT FOR UPDATE.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="capacity_resources")
    type = models.CharField("Tipo do Recurso", max_length=50, choices=CapacityResourceType.choices)
    reference_id = models.CharField("ID de Referência (Lote/Tamanho/etc)", max_length=100, blank=True, null=True)
    capacity_total = models.PositiveIntegerField("Capacidade Total", default=100)
    reserved_count = models.PositiveIntegerField("Reservado em Checkouts Abertos", default=0)
    committed_count = models.PositiveIntegerField("Comprometido / Pago", default=0)

    class Meta:
        verbose_name = "Recurso de Capacidade"
        verbose_name_plural = "Recursos de Capacidade"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity_total__gte=0) &
                          models.Q(reserved_count__gte=0) &
                          models.Q(committed_count__gte=0),
                name="check_positive_capacity_counters"
            )
        ]

    def __str__(self):
        return f"{self.type} [{self.reference_id}] - {self.available_count} livres de {self.capacity_total}"

    @property
    def confirmed_occupancy(self):
        from apps.registrations.models import Registration
        active=0
        if self.event_id and self.type in ('EVENT_OVERALL','LOT_TOTAL'):
            registrations=Registration.objects.filter(event_id=self.event_id,commercial_state='ACTIVE',archived_at__isnull=True)
            if self.type=='LOT_TOTAL':
                from uuid import UUID
                try:lot_id=UUID(str(self.reference_id))
                except (ValueError,TypeError):return self.committed_count
                registrations=registrations.filter(source_order_line__lot_id=lot_id)
            active=registrations.count()
        return max(self.committed_count,active)

    @property
    def available_count(self):
        # Pending registrations track their checkout, but occupy no event/lot seats.
        occupied=self.confirmed_occupancy if self.type in ('EVENT_OVERALL','LOT_TOTAL') else self.reserved_count+self.committed_count
        return max(0, self.capacity_total-occupied)


class HoldStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Ativa (Checkout em andamento)"
    COMMITTED = "COMMITTED", "Confirmada (Pago)"
    EXPIRED = "EXPIRED", "Expirada por Tempo"
    RELEASED = "RELEASED", "Liberada pelo Usuário"

class CapacityHold(models.Model):
    """
    Reserva temporária atômica para evitar oversell durante a etapa de pagamento (TTL padrão: 35 min).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(CapacityResource, on_delete=models.CASCADE, related_name="holds")
    order_id = models.UUIDField("ID do Pedido", db_index=True)
    order_line_id = models.UUIDField("ID da Linha do Pedido", blank=True, null=True)
    quantity = models.PositiveIntegerField("Quantidade Reservada", default=1)
    status = models.CharField("Status da Reserva", max_length=30, choices=HoldStatus.choices, default=HoldStatus.ACTIVE)
    expires_at = models.DateTimeField("Expira em", db_index=True)
    idempotency_key = models.CharField("Chave de Idempotência", max_length=150, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Reserva Temporária de Vaga (Hold)"
        verbose_name_plural = "Reservas Temporárias de Vagas (Holds)"


class InventoryItem(models.Model):
    """
    Estoque físico real (camisas, pulseiras, medalhas, troféus).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="inventory_items")
    sku = models.CharField("Código SKU", max_length=50)
    name = models.CharField("Descrição do Item Físico", max_length=150)
    variant_code = models.CharField("Tamanho / Variação", max_length=30, blank=True, null=True)
    physical_quantity = models.IntegerField("Quantidade em Estoque Físico", default=0)

    class Meta:
        unique_together = ("event", "sku")
        verbose_name = "Item de Estoque Físico"
        verbose_name_plural = "Itens de Estoque Físico"

    def __str__(self):
        return f"{self.name} ({self.variant_code}) - Saldo: {self.physical_quantity}"


class InventoryMovement(models.Model):
    """
    Movimentação física de estoque (entrada, saída, perda, entrega).
    """
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="movements")
    source_ref = models.CharField("Referência da Origem", max_length=100)
    quantity_delta = models.IntegerField("Variação (+ / -)")
    reason = models.CharField("Motivo da Movimentação", max_length=100)
    operator = models.ForeignKey("identity.User", on_delete=models.SET_NULL, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimentação de Estoque"
        verbose_name_plural = "Movimentações de Estoque"
