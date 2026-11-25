"""Preparation and local interest records, separate from paid registrations."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


RAFFLE_INTEREST_CONSENT = (
    "Quero registrar meu interesse e autorizo a organização a entrar em contato "
    "sobre este sorteio usando os dados da minha conta. Entendo que isso não compra "
    "nem reserva uma cota."
)


class MotorcycleRaffle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField("events.Event", on_delete=models.CASCADE, related_name="motorcycle_raffle")
    title = models.CharField("Título", max_length=160, default="Sorteio de moto")
    motorcycle_model = models.CharField("Moto / modelo", max_length=200, blank=True)
    description = models.TextField("Apresentação", blank=True)
    primary_image = models.ImageField('Foto / arte principal da moto', upload_to='raffles/', blank=True)
    secondary_image = models.ImageField('Foto / arte da segunda moto', upload_to='raffles/', blank=True)
    image_style = models.CharField('Apresentação das imagens', max_length=10, default='CUTOUT', choices=[('CUTOUT','Arte sem fundo — destaque livre'),('PHOTO','Fotografia — painel com fundo')])
    authorization_reference = models.CharField('Referência da autorização / enquadramento', max_length=200, blank=True)
    provider_review_reference = models.CharField('Protocolo de análise da operação no Mercado Pago', max_length=200, blank=True)
    online_sales_enabled = models.BooleanField('Ativar venda online de números extras',default=False)
    operation_review_confirmed = models.BooleanField('Conferi a autorização/enquadramento e a aceitação desta operação pelo Mercado Pago',default=False)
    quota_price_cents = models.PositiveIntegerField("Valor previsto da cota em centavos", blank=True, null=True, validators=[MinValueValidator(1)])
    quota_quantity = models.PositiveIntegerField("Quantidade prevista de cotas", blank=True, null=True, validators=[MinValueValidator(1)])
    draw_at = models.DateTimeField("Data prevista", blank=True, null=True)
    rules_text = models.TextField("Regras informadas pela organização", blank=True)
    is_public = models.BooleanField("Publicar apresentação", default=False)
    interest_enabled = models.BooleanField("Permitir registro de interesse", default=False)
    motorcycle_count = models.PositiveSmallIntegerField("Quantidade de motos", default=1)
    registration_tickets_enabled = models.BooleanField("Gerar um número para cada inscrição paga", default=True)
    paper_start = models.PositiveIntegerField("Primeiro número em papel", default=2000)
    paper_end = models.PositiveIntegerField("Último número em papel", default=3000)
    package_prices = models.JSONField("Pacotes com desconto", default=list, blank=True,
        help_text="Lista de pacotes {qty, cents}. O preço avulso continua valendo para o que não fechar um pacote.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sorteio de moto em preparação"
        verbose_name_plural = "Sorteios de moto"
        constraints = [
            models.CheckConstraint(condition=models.Q(quota_price_cents__isnull=True) | models.Q(quota_price_cents__gt=0), name="raffle_price_positive_or_null"),
            models.CheckConstraint(condition=models.Q(quota_quantity__isnull=True) | models.Q(quota_quantity__gt=0), name="raffle_quantity_positive_or_null"),
        ]

    def __str__(self):
        return f"{self.title} — {self.event.title}"

    @property
    def purchase_available(self):
        return bool(not hasattr(self,'result') and self.online_sales_enabled and not self.sales_pending_requirements and
            self.draw_at>timezone.now()+timedelta(minutes=35) and
            self.event.status in ['PUBLISHED','SALES_CLOSED','IN_PROGRESS'] and self.event.ends_at>timezone.now())

    @property
    def sales_pending_requirements(self):
        pending=[]
        if hasattr(self,'result'):pending.append('O resultado deste sorteio já foi registrado')
        for field,label in [('quota_price_cents','Preço do número'),('quota_quantity','Limite de números vendidos online'),
            ('rules_text','Regulamento'),('authorization_reference','Número da autorização do sorteio'),
            ('provider_review_reference','Protocolo de aprovação no Mercado Pago'),('operation_review_confirmed','Conferência da autorização e do Mercado Pago'),('is_public','Publicar apresentação')]:
            if not getattr(self,field):pending.append(label)
        if not self.draw_at or self.draw_at<=timezone.now():pending.append('Data futura do sorteio')
        return pending

    @property
    def paper_count(self):
        return self.paper_end - self.paper_start + 1


class RaffleSalesPoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raffle = models.ForeignKey(MotorcycleRaffle, on_delete=models.PROTECT, related_name='sales_points')
    name = models.CharField('Nome do vendedor', max_length=120)
    contact = models.CharField('Contato', max_length=150, blank=True)
    active = models.BooleanField('Ativo', default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['raffle','name'], name='unique_raffle_sales_point')]

    def __str__(self): return self.name


class RaffleTicket(models.Model):
    class Source(models.TextChoices):
        REGISTRATION = 'REGISTRATION', 'Inscrição paga'
        PAPER = 'PAPER', 'Bilhete em papel'
        ONLINE = 'ONLINE', 'Número extra online'
        SELLER = 'SELLER', 'Venda por vendedor'
    class State(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Disponível'
        DISTRIBUTED = 'DISTRIBUTED', 'Entregue ao ponto'
        SOLD = 'SOLD', 'Confirmado'
        VOID = 'VOID', 'Cancelado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raffle = models.ForeignKey(MotorcycleRaffle, on_delete=models.PROTECT, related_name='tickets')
    number = models.PositiveIntegerField('Número')
    source = models.CharField(max_length=20, choices=Source.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.AVAILABLE)
    registration = models.OneToOneField('registrations.Registration', on_delete=models.PROTECT, null=True, blank=True, related_name='raffle_ticket')
    beneficiary_registration = models.ForeignKey('registrations.Registration',on_delete=models.PROTECT,null=True,blank=True,related_name='extra_raffle_tickets')
    order_line = models.ForeignKey('checkout.OrderLine',on_delete=models.PROTECT,null=True,blank=True,related_name='raffle_tickets')
    point = models.ForeignKey(RaffleSalesPoint, on_delete=models.PROTECT, null=True, blank=True, related_name='tickets')
    buyer_name = models.CharField('Nome do comprador', max_length=150, blank=True)
    buyer_phone = models.CharField('WhatsApp', max_length=30, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    sale_reference = models.CharField('Venda', max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['raffle','number'], name='unique_raffle_number')]


class RaffleSellerSettlement(models.Model):
    """Dinheiro que o vendedor entregou à organização. Só aqui a venda presencial entra no caixa."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller = models.ForeignKey(RaffleSalesPoint, on_delete=models.PROTECT, related_name='settlements')
    amount_cents = models.PositiveIntegerField('Valor repassado', validators=[MinValueValidator(1)])
    received_on = models.DateField('Recebido em')
    method = models.CharField('Forma', max_length=20, default='PIX',
        choices=[('PIX', 'Pix'), ('CASH', 'Dinheiro'), ('TRANSFER', 'Transferência'), ('OTHER', 'Outro')])
    notes = models.CharField('Observação', max_length=300, blank=True)
    ledger_entry = models.OneToOneField('finance.FinancialEntry', on_delete=models.PROTECT, related_name='raffle_settlement')
    request_key = models.CharField(max_length=64, unique=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_on', '-created_at']


class RaffleInterest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raffle = models.ForeignKey(MotorcycleRaffle, on_delete=models.CASCADE, related_name="interests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="raffle_interests")
    consent_text = models.TextField("Aceite registrado")
    created_at = models.DateTimeField("Interesse registrado em", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Interesse no sorteio"
        verbose_name_plural = "Interesses nos sorteios"
        constraints = [models.UniqueConstraint(fields=["raffle", "user"], name="unique_raffle_interest_per_user")]

    def __str__(self):
        return f"{self.user} — {self.raffle.title}"


class RaffleResult(models.Model):
    raffle=models.OneToOneField(MotorcycleRaffle,on_delete=models.PROTECT,related_name='result')
    winners=models.JSONField(default=list)
    eligible_snapshot=models.JSONField(default=list)
    snapshot_sha256=models.CharField(max_length=64)
    method_notes=models.TextField()
    recorded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    recorded_at=models.DateTimeField(auto_now_add=True)
