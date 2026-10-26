import uuid
from django.db import models
from django.utils import timezone

class RateLimitBucket(models.Model):
    """Shared, bounded request counters. Keys are HMAC digests, never raw identities."""
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class Organization(models.Model):
    """
    Entidade operacional da Equipe Morcego.
    Administrada por Kelvim Muniz.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Nome", max_length=200, default="Equipe Morcego")
    legal_name = models.CharField("Razão Social / Nome Completo", max_length=200, blank=True, null=True)
    document_number = models.CharField("CNPJ / CPF", max_length=30, blank=True, null=True)
    support_email = models.EmailField("E-mail de Suporte", default="contato@equipemorcego.com.br")
    phone = models.CharField("Telefone / WhatsApp", max_length=30, default="(88) 99999-9999")
    city = models.CharField("Cidade Sede", max_length=100, default="Tianguá")
    state = models.CharField("Estado", max_length=2, default="CE")
    default_timezone = models.CharField("Fuso Horário Padrão", max_length=50, default="America/Fortaleza")
    preferences_json = models.JSONField("Preferências Globais", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Organização"
        verbose_name_plural = "Organizações"

    def __str__(self):
        return self.name


class AuditLog(models.Model):
    """
    Trilha de auditoria imutável (append-only) para todas as ações sensíveis.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.CharField("ID do Ator", max_length=100, blank=True, null=True)
    actor_email = models.EmailField("E-mail do Ator", blank=True, null=True)
    event_id = models.UUIDField("ID do Evento", blank=True, null=True, db_index=True)
    action = models.CharField("Ação", max_length=100, db_index=True)
    entity_type = models.CharField("Tipo da Entidade", max_length=100, db_index=True)
    entity_id = models.CharField("ID da Entidade", max_length=100, db_index=True)
    before_hash = models.CharField("Hash Anterior", max_length=64, blank=True, null=True)
    after_hash = models.CharField("Hash Posterior", max_length=64, blank=True, null=True)
    diff_json = models.JSONField("Detalhes / Modificações", default=dict, blank=True)
    reason = models.TextField("Justificativa", blank=True, null=True)
    request_id = models.CharField("Request ID", max_length=64, blank=True, null=True)
    ip_address = models.GenericIPAddressField("IP", blank=True, null=True)
    created_at = models.DateTimeField("Data e Hora", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Registro de Auditoria"
        verbose_name_plural = "Registros de Auditoria"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {self.action} on {self.entity_type} {self.entity_id}"


class OutboxMessage(models.Model):
    """
    Padrão Outbox Transacional para garantir entrega de eventos assíncronos.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField("Nome do Evento", max_length=150, db_index=True)
    aggregate_type = models.CharField("Tipo do Agregado", max_length=100)
    aggregate_id = models.CharField("ID do Agregado", max_length=100)
    payload_json = models.JSONField("Payload Mínimo", default=dict)
    dedupe_key = models.CharField("Chave de Deduplicação", max_length=200, unique=True)
    status = models.CharField("Status", max_length=20, default="PENDING", db_index=True)
    retries = models.PositiveIntegerField("Tentativas", default=0)
    error_message = models.TextField("Último Erro", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField("Processado em", blank=True, null=True)

    class Meta:
        verbose_name = "Mensagem Outbox"
        verbose_name_plural = "Mensagens Outbox"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.event_name} ({self.dedupe_key}) - {self.status}"
