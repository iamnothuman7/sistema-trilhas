import uuid
from django.db import models

class FrameFormat(models.TextChoices):
    FEED_1080x1350 = "FEED_1080x1350", "Instagram Feed / Post (1080 x 1350 px)"
    STORY_1080x1920 = "STORY_1080x1920", "Instagram Stories / Reels (1080 x 1920 px)"

class AccessPolicy(models.TextChoices):
    PUBLIC = "PUBLIC", "Público (Qualquer pessoa / Convite 'Eu Vou')"
    REGISTERED_READY = "REGISTERED_READY", "Exclusivo para Inscritos Confirmados"

class FrameTemplate(models.Model):
    """
    Template de moldura digital oficial do evento.
    A renderização é feita no navegador do piloto via Canvas 2D sem enviar fotos para o servidor.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="frame_templates")
    title = models.CharField("Nome da Moldura", max_length=150, default="Moldura Oficial 3º Trilhão Tianguá")
    format = models.CharField("Formato", max_length=30, choices=FrameFormat.choices, default=FrameFormat.FEED_1080x1350)
    width = models.PositiveIntegerField("Largura (px)", default=1080)
    height = models.PositiveIntegerField("Altura (px)", default=1350)

    overlay_asset = models.ImageField("Arquivo PNG Transparente da Moldura", upload_to="frames/overlays/")
    preview_asset = models.ImageField("Prévia de Amostra", upload_to="frames/previews/", blank=True, null=True)

    access_policy = models.CharField("Política de Acesso", max_length=30, choices=AccessPolicy.choices, default=AccessPolicy.PUBLIC)
    is_active = models.BooleanField("Ativo para Download", default=True)

    text_overlay_enabled = models.BooleanField("Permitir Nome do Piloto / Equipe Estampados", default=True)
    text_position_y = models.PositiveIntegerField("Posição Vertical do Texto (px a partir do topo)", default=1200)
    text_color = models.CharField("Cor do Texto (Hex)", max_length=10, default="#FFFFFF")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Template de Moldura Digital"
        verbose_name_plural = "Templates de Molduras Digitais"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_format_display()}) - {self.event.title}"
