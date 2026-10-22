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
        # No quota allocation, checkout or provider call exists in this draft module.
        return False


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
