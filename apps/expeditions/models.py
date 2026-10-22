import uuid
from django.db import models
from django.utils import timezone

class ExpeditionDay(models.Model):
    """
    Etapa diária de uma expedição off-road de múltiplos dias.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="expedition_days")
    day_number = models.PositiveIntegerField("Dia da Etapa (ex: 1, 2, 3)")
    starts_at = models.DateTimeField("Início da Etapa")
    ends_at = models.DateTimeField("Fim Previsto da Etapa")
    title = models.CharField("Nome da Etapa", max_length=150)
    origin_city = models.CharField("Cidade de Partida", max_length=100, default="Tianguá")
    destination_city = models.CharField("Cidade de Chegada", max_length=100)
    meetup_location = models.CharField("Ponto de Concentração / Saída", max_length=150)
    notes = models.TextField("Orientações e Briefing do Dia", blank=True, null=True)

    class Meta:
        unique_together = ("event", "day_number")
        ordering = ["day_number"]
        verbose_name = "Etapa da Expedição"
        verbose_name_plural = "Etapas da Expedição"

    def __str__(self):
        return f"Dia {self.day_number}: {self.title} ({self.origin_city} -> {self.destination_city})"


class Room(models.Model):
    """
    Acomodação / Quarto de hotel ou pousada contratada para a expedição.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="rooms")
    property_label = models.CharField("Nome da Pousada / Hotel", max_length=150)
    room_code = models.CharField("Número / Identificador do Quarto", max_length=30)
    night_date = models.DateField("Data do Pernoite")
    capacity = models.PositiveIntegerField("Capacidade de Leitos", default=2)

    class Meta:
        unique_together = ("event", "property_label", "room_code", "night_date")
        verbose_name = "Quarto / Hospedagem"
        verbose_name_plural = "Quartos / Hospedagens"

    def __str__(self):
        return f"{self.property_label} - Qto {self.room_code} ({self.night_date.strftime('%d/%m')}) [Cap: {self.capacity}]"


class RoomAssignment(models.Model):
    """
    Alocação manual de piloto em quarto específico.
    """
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="assignments")
    registration = models.ForeignKey("registrations.Registration", on_delete=models.CASCADE, related_name="room_assignments")
    bed_label = models.CharField("Identificação da Cama (ex: Cama 1, Cama Casal)", max_length=50, blank=True, null=True)
    status = models.CharField("Status", max_length=20, default="CONFIRMED")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("room", "registration")
        verbose_name = "Alocação de Quarto"
        verbose_name_plural = "Alocações de Quartos"


class TransportVehicle(models.Model):
    """
    Veículo de transporte / van / caminhão de apoio para a expedição.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="transport_vehicles")
    label = models.CharField("Identificação do Veículo (ex: Van 01, Apoio Bagagens)", max_length=100)
    driver_name = models.CharField("Nome do Motorista", max_length=100, blank=True, null=True)
    driver_phone = models.CharField("Telefone do Motorista", max_length=30, blank=True, null=True)
    total_seats = models.PositiveIntegerField("Lugares Disponíveis", default=15)

    class Meta:
        verbose_name = "Veículo de Apoio / Transporte"
        verbose_name_plural = "Veículos de Apoio / Transportes"

    def __str__(self):
        return f"{self.label} ({self.total_seats} lugares)"


class TransportAssignment(models.Model):
    vehicle = models.ForeignKey(TransportVehicle, on_delete=models.CASCADE, related_name="assignments")
    registration = models.ForeignKey("registrations.Registration", on_delete=models.CASCADE, related_name="transport_assignments")
    leg_code = models.CharField("Trecho da Viagem", max_length=50, default="GERAL")
    seat_no = models.PositiveIntegerField("Número do Assento", blank=True, null=True)
    status = models.CharField("Status", max_length=20, default="CONFIRMED")

    class Meta:
        verbose_name = "Alocação de Transporte"
        verbose_name_plural = "Alocações de Transportes"
