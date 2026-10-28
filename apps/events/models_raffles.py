"""Preparation and local interest records, separate from paid registrations."""
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


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
        # Internal printed tickets do not enable a public quota checkout.
        return False


class RaffleSalesPoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raffle = models.ForeignKey(MotorcycleRaffle, on_delete=models.PROTECT, related_name='sales_points')
    name = models.CharField('Nome do ponto / vendedor', max_length=120)
    contact = models.CharField('Contato', max_length=150, blank=True)
    active = models.BooleanField('Ativo', default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['raffle','name'], name='unique_raffle_sales_point')]

    def __str__(self): return self.name


class RaffleTicket(models.Model):
    class Source(models.TextChoices):
        REGISTRATION = 'REGISTRATION', 'Inscrição paga'
        PAPER = 'PAPER', 'Bilhete em papel'
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
    point = models.ForeignKey(RaffleSalesPoint, on_delete=models.PROTECT, null=True, blank=True, related_name='tickets')
    buyer_name = models.CharField('Nome do comprador', max_length=150, blank=True)
    buyer_phone = models.CharField('WhatsApp', max_length=30, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['raffle','number'], name='unique_raffle_number')]


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
