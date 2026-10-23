import uuid
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from .validators import validate_instagram_profile, validate_sponsor_logo_size


class TeamAlias(models.Model):
    event = models.ForeignKey("Event", on_delete=models.CASCADE, related_name="team_aliases")
    source_name = models.CharField("Nome informado", max_length=120)
    source_key = models.CharField(max_length=240, editable=False)
    canonical_name = models.CharField("Exibir como", max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Correção de nome de equipe"
        verbose_name_plural = "Correções de nomes de equipes"
        ordering = ["source_name"]
        constraints = [models.UniqueConstraint(fields=["event", "source_key"], name="unique_team_alias_per_event")]

    def clean(self):
        from .rankings import display_name, group_key, validate_alias_mapping
        self.source_name = display_name(self.source_name)
        self.canonical_name = display_name(self.canonical_name)
        self.source_key = group_key(self.source_name)
        if self.event_id:
            validate_alias_mapping(self.event, "team", self.source_key, self.canonical_name, exclude_pk=self.pk)

    def save(self, *args, **kwargs):
        from .rankings import group_key
        self.source_key = group_key(self.source_name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"source_key"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source_name} → {self.canonical_name}"


class CityAlias(models.Model):
    event = models.ForeignKey("Event", on_delete=models.CASCADE, related_name="city_aliases")
    source_name = models.CharField("Cidade informada", max_length=100)
    state = models.CharField("UF", max_length=2, blank=True)
    source_key = models.CharField(max_length=200, editable=False)
    canonical_name = models.CharField("Exibir como", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Correção de nome de cidade"
        verbose_name_plural = "Correções de nomes de cidades"
        ordering = ["state", "source_name"]
        constraints = [models.UniqueConstraint(fields=["event", "state", "source_key"], name="unique_city_alias_per_event_uf")]

    def clean(self):
        from .rankings import STATE_CODES, display_name, group_key, validate_alias_mapping
        from django.core.exceptions import ValidationError
        self.source_name = display_name(self.source_name)
        self.canonical_name = display_name(self.canonical_name)
        self.state = display_name(self.state).upper()
        if self.state and self.state not in STATE_CODES:
            raise ValidationError({"state": "Escolha uma UF válida."})
        self.source_key = group_key(self.source_name)
        if self.event_id:
            validate_alias_mapping(self.event, "city", self.source_key, self.canonical_name, state=self.state, exclude_pk=self.pk)

    def save(self, *args, **kwargs):
        from .rankings import group_key
        self.source_key = group_key(self.source_name)
        self.state = self.state.strip().upper()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"source_key", "state"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.source_name}/{self.state or 'UF ausente'} → {self.canonical_name}"

class EventType(models.TextChoices):
    TRILHAO = "TRILHAO", "Trilhão Off-Road"
    EXPEDITION = "EXPEDITION", "Expedição de Múltiplos Dias"
    ENCOUNTER = "ENCOUNTER", "Encontro / Passeio Off-Road"

class EventStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    PUBLISHED = "PUBLISHED", "Publicado"
    SALES_CLOSED = "SALES_CLOSED", "Inscrições Encerradas"
    IN_PROGRESS = "IN_PROGRESS", "Em Andamento"
    FINISHED = "FINISHED", "Concluído"
    ARCHIVED = "ARCHIVED", "Arquivado"
    CANCELED = "CANCELED", "Cancelado"

class Event(models.Model):
    """
    Edição específica de um evento da Equipe Morcego.
    Cada evento possui autonomia comercial, visual e financeira.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("core.Organization", on_delete=models.PROTECT, related_name="events")
    title = models.CharField("Nome do Evento", max_length=200)
    slug = models.SlugField("Identificador na URL (Slug)", max_length=200, unique=True, db_index=True)
    subdomain = models.SlugField(
        "Subdomínio Exclusivo",
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        db_index=True,
        help_text="Ex: 3trilhaotiangua (acessível em 3trilhaotiangua.equipemorcego.nabio.pro)"
    )
    edition_number = models.PositiveIntegerField("Número da Edição", default=1)
    type = models.CharField("Tipo do Evento", max_length=30, choices=EventType.choices, default=EventType.TRILHAO)
    status = models.CharField("Status", max_length=30, choices=EventStatus.choices, default=EventStatus.DRAFT, db_index=True)

    city = models.CharField("Cidade", max_length=100, default="Tianguá")
    state = models.CharField("UF", max_length=2, default="CE")
    venue = models.CharField("Local de Concentração", max_length=200, default="Polo de Lazer de Tianguá")
    timezone = models.CharField("Fuso Horário", max_length=50, default="America/Fortaleza")

    starts_at = models.DateTimeField("Data e Hora de Início")
    ends_at = models.DateTimeField("Data e Hora de Término")
    registration_opens_at = models.DateTimeField("Abertura das Inscrições")
    registration_closes_at = models.DateTimeField("Encerramento das Inscrições")

    max_attendees = models.PositiveIntegerField("Capacidade Geral Máxima de Pilotos", default=300)
    description = models.TextField("Apresentação do Evento", blank=True, null=True)
    rules_summary = models.TextField("Resumo de Orientações e Regras", blank=True, null=True)
    contact_email = models.EmailField("E-mail de Contato", default="contato@equipemorcego.com.br")
    contact_phone = models.CharField("Telefone / WhatsApp da Organização", max_length=30, default="(88) 99999-9999")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Evento"
        verbose_name_plural = "Eventos"
        ordering = ["-starts_at"]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        if not self.subdomain and self.slug:
            clean_sub = self.slug.replace("-", "").replace("offroad2027", "").replace("offroad", "")
            self.subdomain = clean_sub[:40] or self.slug.replace("-", "")[:40]
        super().save(*args, **kwargs)

    @property
    def microsite_url(self):
        sub = self.subdomain or self.slug.replace("-", "")[:30]
        return f"https://{sub}.equipemorcego.nabio.pro"

    @property
    def is_registration_open(self):
        now = timezone.now()
        return (
            self.status == EventStatus.PUBLISHED
            and self.registration_opens_at <= now <= self.registration_closes_at
        )


class EventTheme(models.Model):
    """
    Identidade visual personalizada por evento (Design Tokens CSS).
    """
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="theme")
    primary_color = models.CharField("Cor Primária (Hex)", max_length=10, default="#F59E0B")  # Off-road amber / orange
    secondary_color = models.CharField("Cor Secundária (Hex)", max_length=10, default="#10B981") # Trail green
    accent_color = models.CharField("Cor de Destaque (Hex)", max_length=10, default="#EF4444")
    surface_color = models.CharField("Cor de Fundo / Superfície", max_length=10, default="#0F172A") # Dark slate
    text_color = models.CharField("Cor do Texto", max_length=10, default="#F8FAFC")
    hero_banner = models.ImageField("Banner Principal", upload_to="events/banners/", blank=True, null=True)
    logo_image = models.ImageField("Logotipo Específico do Evento", upload_to="events/logos/", blank=True, null=True)
    custom_css = models.TextField("CSS Personalizado Seguro", blank=True, null=True)
    approved_at = models.DateTimeField("Aprovado em", blank=True, null=True)

    class Meta:
        verbose_name = "Tema do Evento"
        verbose_name_plural = "Temas dos Eventos"

    def __str__(self):
        return f"Tema de {self.event.title}"


class EventPageBlock(models.Model):
    """
    Blocos de conteúdo CMS gerenciáveis para o site do evento.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="page_blocks")
    page_slug = models.CharField("Página", max_length=50, default="home")  # home, programacao, percurso, etc.
    section_key = models.CharField("Chave da Seção", max_length=50)  # hero, about, briefing, schedule, sponsors
    title = models.CharField("Título da Seção", max_length=150)
    sort_order = models.PositiveIntegerField("Ordem", default=0)
    content_json = models.JSONField("Conteúdo", default=dict, blank=True)
    visible = models.BooleanField("Visível", default=True)

    class Meta:
        unique_together = ("event", "page_slug", "section_key")
        ordering = ["sort_order"]
        verbose_name = "Bloco de Conteúdo"
        verbose_name_plural = "Blocos de Conteúdo"


class EventScheduleItem(models.Model):
    """
    Item da programação oficial (cronograma) do evento.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="schedule_items")
    starts_at = models.DateTimeField("Horário de Início")
    ends_at = models.DateTimeField("Horário Previsto de Término", blank=True, null=True)
    title = models.CharField("Atividade", max_length=200)
    location = models.CharField("Local", max_length=150, default="Concentração Geral")
    description = models.TextField("Detalhes da Atividade", blank=True, null=True)
    sort_order = models.PositiveIntegerField("Ordem", default=0)

    class Meta:
        ordering = ["starts_at", "sort_order"]
        verbose_name = "Item da Programação"
        verbose_name_plural = "Itens da Programação"

    def __str__(self):
        return f"{self.starts_at.strftime('%d/%m %H:%M')} - {self.title}"


class Route(models.Model):
    """
    Informações sobre a trilha / percurso do evento.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="routes")
    title = models.CharField("Nome da Trilha", max_length=150, default="Trilha Principal Off-Road")
    distance_km = models.DecimalField("Distância Estimada (km)", max_digits=6, decimal_places=2, default=65.0)
    difficulty = models.CharField("Nível de Dificuldade", max_length=50, default="Média / Difícil")
    file_asset = models.FileField("Arquivo GPX / KML (Opcional)", upload_to="events/routes/", blank=True, null=True)
    public_flag = models.BooleanField("Percurso Visível ao Público", default=True)
    description = models.TextField("Descrição do Percurso e Pontos Notáveis", blank=True, null=True)

    class Meta:
        verbose_name = "Percurso / Rota"
        verbose_name_plural = "Percursos / Rotas"

    def __str__(self):
        return f"{self.title} ({self.distance_km} km)"


class RouteCheckpoint(models.Model):
    """
    Pontos de controle / passagem na trilha (ex: Ponto de Apoio 1, Ponto de Hidratação).
    """
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="checkpoints")
    code = models.CharField("Código do Ponto", max_length=20)  # ex: CP-01, APOIO-SERRA
    label = models.CharField("Nome do Ponto de Apoio", max_length=100)
    sort_order = models.PositiveIntegerField("Sequência na Trilha", default=1)
    latitude = models.DecimalField("Latitude", max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField("Longitude", max_digits=9, decimal_places=6, blank=True, null=True)

    class Meta:
        unique_together = ("route", "code")
        ordering = ["sort_order"]
        verbose_name = "Ponto de Controle na Trilha"
        verbose_name_plural = "Pontos de Controle na Trilha"

    def __str__(self):
        return f"{self.code} - {self.label}"


class SponsorTier(models.TextChoices):
    DIAMANTE = "DIAMANTE", "Cota Diamante"
    OURO = "OURO", "Cota Ouro"
    PRATA = "PRATA", "Cota Prata"
    BRONZE = "BRONZE", "Cota Bronze"
    APOIO = "APOIO", "Apoio Institucional"
    PARCEIRO = "PARCEIRO", "Parceiro Oficial"


class Sponsor(models.Model):
    """
    Patrocinadores e apoiadores do evento.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sponsors")
    name = models.CharField("Nome do Patrocinador / Marca", max_length=150)
    tier = models.CharField("Cota de Patrocínio", max_length=30, choices=SponsorTier.choices, default=SponsorTier.OURO)
    website = models.URLField("Site / Instagram", blank=True, null=True)
    logo_image = models.ImageField("Logotipo", upload_to="events/sponsors/", blank=True, validators=[validate_sponsor_logo_size])
    logo_preview = models.ImageField("Prévia do logotipo", upload_to="events/sponsors/previews/", blank=True, editable=False)
    logo_preview_width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    logo_preview_height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    instagram_url = models.URLField("Instagram", blank=True, validators=[validate_instagram_profile])
    sort_order = models.PositiveIntegerField("Ordem de Exibição", default=0)
    approved_at = models.DateTimeField("Aprovado em", default=timezone.now)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Patrocinador"
        verbose_name_plural = "Patrocinadores"

    def __str__(self):
        return f"{self.name} ({self.get_tier_display()})"

    @property
    def display_logo_url(self):
        if not self.logo_image:
            return ""
        return self.logo_preview.url if self.logo_preview else self.logo_image.url


class DocumentKind(models.TextChoices):
    REGULAMENTO = "REGULAMENTO", "Regulamento Oficial do Evento"
    TERMO_RESPONSABILIDADE = "TERMO_RESPONSABILIDADE", "Termo de Responsabilidade e Isenção"
    PRIVACIDADE_LGPD = "PRIVACIDADE_LGPD", "Aviso de Privacidade e Tratamento de Dados"
    MENORES = "MENORES", "Autorização de Participação de Menores"


class LegalDocumentVersion(models.Model):
    """
    Termos legais e regulamentos versionados com hash SHA-256 para auditoria imutável.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="legal_documents")
    kind = models.CharField("Tipo do Documento", max_length=40, choices=DocumentKind.choices)
    version_number = models.PositiveIntegerField("Número da Versão", default=1)
    sha256 = models.CharField("Hash SHA-256", max_length=64, editable=False, db_index=True)
    published_at = models.DateTimeField("Publicado em", default=timezone.now)
    effective_at = models.DateTimeField("Vigência Inicial", default=timezone.now)
    content = models.TextField("Texto Completo dos Termos")

    class Meta:
        unique_together = ("event", "kind", "version_number")
        ordering = ["-version_number"]
        verbose_name = "Versão de Documento Legal"
        verbose_name_plural = "Versões de Documentos Legais"

    def save(self, *args, **kwargs):
        import hashlib
        self.sha256 = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version_number} ({self.event.title})"


# Django discovers these event-specific models through the app's models module.
from .models_raffles import MotorcycleRaffle, RaffleInterest  # noqa: E402,F401
