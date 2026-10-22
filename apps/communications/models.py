import uuid
import urllib.parse
from django.db import models
from django.utils import timezone

class NotificationType(models.TextChoices):
    ORDER_RESERVED = "ORDER_RESERVED", "Reserva Criada (Pix Aguardando)"
    PAYMENT_APPROVED = "PAYMENT_APPROVED", "Pagamento Confirmado"
    CLAIM_INVITE = "CLAIM_INVITE", "Convite para Reivindicar Inscrição (Grupo)"
    TERMS_PENDING = "TERMS_PENDING", "Lembrete de Aceite do Regulamento"
    CREDENTIAL_READY = "CREDENTIAL_READY", "QR Code de Acesso Liberado"
    TRANSFER_NOTICE = "TRANSFER_NOTICE", "Aviso de Transferência"
    EVENT_UPDATE = "EVENT_UPDATE", "Atualização Importante da Organização"

class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente de Envio"
    SENT = "SENT", "Enviado com Sucesso"
    FAILED = "FAILED", "Falhou"

class NotificationOutbox(models.Model):
    """
    Fila de saída de notificações transacionais e e-mails com garantia de deduplicação.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="notifications")
    recipient_name = models.CharField("Nome do Destinatário", max_length=150)
    recipient_email = models.EmailField("E-mail do Destinatário", blank=True, null=True)
    recipient_phone = models.CharField("Telefone / WhatsApp", max_length=30, blank=True, null=True)

    type = models.CharField("Tipo da Notificação", max_length=40, choices=NotificationType.choices)
    template_key = models.CharField("Identificador do Template", max_length=100)
    dedupe_key = models.CharField("Chave de Deduplicação", max_length=150, unique=True)
    payload_json = models.JSONField("Dados da Notificação", default=dict)

    status = models.CharField("Situação", max_length=20, choices=NotificationStatus.choices, default=NotificationStatus.PENDING)
    attempts = models.PositiveIntegerField("Tentativas", default=0)
    error_message = models.TextField("Log de Erro", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField("Enviado em", blank=True, null=True)

    class Meta:
        verbose_name = "Notificação Outbox"
        verbose_name_plural = "Notificações Outbox"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} -> {self.recipient_name} ({self.status})"

    def generate_whatsapp_link(self, message_text: str) -> str:
        """Gera link oficial para conversa wa.me preenchida."""
        if not self.recipient_phone:
            return ""
        clean_phone = "".join(filter(str.isdigit, self.recipient_phone))
        if len(clean_phone) <= 11 and not clean_phone.startswith("55"):
            clean_phone = f"55{clean_phone}"
        encoded_msg = urllib.parse.quote(message_text)
        return f"https://wa.me/{clean_phone}?text={encoded_msg}"
