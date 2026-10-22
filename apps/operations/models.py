import uuid
from django.db import models
from django.utils import timezone

class CheckinType(models.TextChoices):
    SCAN = "SCAN", "Leitura de QR Code via Câmera"
    MANUAL_SEARCH = "MANUAL_SEARCH", "Busca Manual por Nome / CPF"

class CheckinRecord(models.Model):
    """
    Registro definitivo de presença do piloto no dia do evento.
    Independente da entrega do kit físico.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.OneToOneField("registrations.Registration", on_delete=models.PROTECT, related_name="checkin_record")
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="checkins")
    operator = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    occurred_at = models.DateTimeField("Data e Hora da Chegada", default=timezone.now)
    source_key = models.CharField("Chave de Idempotência", max_length=150, unique=True)
    checkin_type = models.CharField("Forma de Credenciamento", max_length=30, choices=CheckinType.choices, default=CheckinType.SCAN)
    notes = models.TextField("Observações", blank=True, null=True)

    class Meta:
        verbose_name = "Registro de Presença / Credenciamento"
        verbose_name_plural = "Registros de Presenças / Credenciamentos"
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"Presença de {self.registration.person.full_name} em {self.occurred_at.strftime('%d/%m %H:%M')}"


class FieldRecordKind(models.TextChoices):
    START = "START", "Largada na Trilha"
    CHECKPOINT = "CHECKPOINT", "Passagem em Ponto de Apoio"
    RETURN = "RETURN", "Retorno / Chegada Seguro"

class FieldRecord(models.Model):
    """
    Registro de observação de passagem na trilha.
    Ausência de registro não implica acidente; apenas status não observado.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey("registrations.Registration", on_delete=models.PROTECT, related_name="field_records")
    checkpoint = models.ForeignKey("events.RouteCheckpoint", on_delete=models.SET_NULL, blank=True, null=True)
    kind = models.CharField("Tipo do Registro", max_length=30, choices=FieldRecordKind.choices, default=FieldRecordKind.CHECKPOINT)
    occurred_at = models.DateTimeField(default=timezone.now)
    operator = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    status = models.CharField("Status da Passagem", max_length=50, default="CONFIRMED")
    notes = models.TextField("Anotações da Equipe de Pista", blank=True, null=True)

    class Meta:
        verbose_name = "Registro de Passagem na Trilha"
        verbose_name_plural = "Registros de Passagens na Trilha"
        ordering = ["-occurred_at"]


class OccurrenceSeverity(models.TextChoices):
    INFO = "INFO", "Informativa (Pneu furado, quebra simples de moto)"
    WARNING = "WARNING", "Atenção (Necessidade de reboque / atraso)"
    URGENT = "URGENT", "Urgente (Resgate operacional necessário)"

class OccurrenceRecord(models.Model):
    """
    Registro de ocorrência operacional.
    Não coleta prontuário médico sensível (LGPD).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="occurrences")
    registration = models.ForeignKey("registrations.Registration", on_delete=models.SET_NULL, blank=True, null=True)
    severity = models.CharField("Gravidade", max_length=20, choices=OccurrenceSeverity.choices, default=OccurrenceSeverity.INFO)
    summary = models.CharField("Resumo do Incidente", max_length=200)
    details = models.TextField("Descrição Operacional")
    location = models.CharField("Local Aproximado na Trilha", max_length=150, blank=True, null=True)
    staff = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    resolved = models.BooleanField("Resolvido", default=False)
    resolved_at = models.DateTimeField("Resolvido em", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ocorrência Operacional"
        verbose_name_plural = "Ocorrências Operacionais"
        ordering = ["-created_at"]
