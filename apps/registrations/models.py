import uuid
from django.db import models
from django.utils import timezone

class CommercialState(models.TextChoices):
    RESERVED = "RESERVED", "Reservada (Aguardando Pagamento)"
    ACTIVE = "ACTIVE", "Ativa (Comercialmente Confirmada)"
    CANCELED = "CANCELED", "Cancelada"
    TRANSFERRED = "TRANSFERRED", "Transferida para Outro Piloto"

class OperationalState(models.TextChoices):
    NOT_READY = "NOT_READY", "Pendente (Falta Aceite dos Termos / Documentos)"
    READY = "READY", "Apto para Credenciamento (QR Code Liberado)"
    CHECKED_IN = "CHECKED_IN", "Credenciado no Evento (Presente)"
    CLOSED = "CLOSED", "Encerrado"

class Registration(models.Model):
    """
    Direito individual de participação de uma pessoa física em determinado evento.
    Separado do pedido de compra para suportar compras em grupo, transferências e reembolsos parciais.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="registrations")
    person = models.ForeignKey("identity.Person", on_delete=models.PROTECT, related_name="registrations")
    source_order_line = models.OneToOneField("checkout.OrderLine", on_delete=models.PROTECT, related_name="registration")

    registration_number = models.PositiveIntegerField("Número de Inscrição (#)", blank=True, null=True, db_index=True)
    participant_cpf = models.CharField("CPF do participante", max_length=11, blank=True, null=True, editable=False)
    commercial_state = models.CharField("Estado Comercial", max_length=30, choices=CommercialState.choices, default=CommercialState.RESERVED, db_index=True)
    operational_state = models.CharField("Estado Operacional", max_length=30, choices=OperationalState.choices, default=OperationalState.NOT_READY, db_index=True)

    category_name = models.CharField("Categoria / Modalidade", max_length=100, default="Piloto Completo")
    team_name = models.CharField("Equipe / Moto Clube", max_length=120, blank=True, null=True)
    shirt_size = models.CharField("Tamanho da Camiseta", max_length=20, default="M")

    product_snapshot_json = models.JSONField("Snapshot do Pacote Contratado", default=dict)
    parent_registration = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="transferred_to")
    current_holder_from = models.DateTimeField(default=timezone.now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inscrição"
        verbose_name_plural = "Inscrições"
        ordering = ["registration_number", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "participant_cpf"],
                condition=models.Q(commercial_state__in=["RESERVED", "ACTIVE"], participant_cpf__isnull=False),
                name="unique_live_registration_cpf_event"),
            models.CheckConstraint(condition=models.Q(participant_cpf__isnull=True) | models.Q(participant_cpf__regex=r"^[0-9]{11}$"),
                name="registration_cpf_canonical"),
        ]

    def save(self, *args, **kwargs):
        if self.participant_cpf:
            from apps.identity.documents import normalize_cpf
            self.participant_cpf = normalize_cpf(self.participant_cpf)
        else:
            self.participant_cpf = None
        super().save(*args, **kwargs)

    def __str__(self):
        num_str = f"#{self.registration_number} - " if self.registration_number else ""
        return f"{num_str}{self.person.full_name} ({self.event.title}) [{self.get_operational_state_display()}]"

    def is_eligible_for_qr(self):
        return self.commercial_state == CommercialState.ACTIVE and self.operational_state in (
            OperationalState.READY, OperationalState.CHECKED_IN
        )


class ManualRegistration(models.Model):
    registration = models.OneToOneField(Registration, on_delete=models.PROTECT, related_name="manual_entry")
    created_by = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    request_key = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    notes = models.TextField("Motivo / observação")
    access_nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    access_expires_at = models.DateTimeField()
    access_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Inscrição manual"
        verbose_name_plural = "Inscrições manuais"


class TermsAcceptance(models.Model):
    """
    Comprovação jurídica e LGPD do aceite pessoal dos termos e regulamento.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(Registration, on_delete=models.PROTECT, related_name="terms_acceptances")
    document_version = models.ForeignKey("events.LegalDocumentVersion", on_delete=models.PROTECT)
    accepting_user = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    accepted_at = models.DateTimeField("Data e Hora do Aceite", default=timezone.now)
    evidence_hash = models.CharField("Hash de Evidência", max_length=64)
    ip_address = models.GenericIPAddressField("IP", blank=True, null=True)
    user_agent = models.TextField("Dispositivo / Navegador", blank=True, null=True)

    class Meta:
        unique_together = ("registration", "document_version", "accepting_user")
        verbose_name = "Aceite de Termo / Regulamento"
        verbose_name_plural = "Aceites de Termos / Regulamentos"


class RegistrationCredential(models.Model):
    """
    Credencial e QR Code de acesso opaco, rotacionável e revogável.
    Não contém dados pessoais sensíveis dentro da string do QR.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.OneToOneField(Registration, on_delete=models.CASCADE, related_name="credential")
    token_digest = models.CharField("Digest Seguro (SHA-256)", max_length=64, unique=True, db_index=True)
    raw_token = models.CharField("Código Alfanumérico de Acesso", max_length=32, unique=True)
    version = models.PositiveIntegerField("Versão da Credencial", default=1)
    qr_code_image = models.TextField("QR Code Imagem (Base64)", blank=True, null=True)
    issued_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField("Revogada em", blank=True, null=True)
    expires_at = models.DateTimeField("Válida até", blank=True, null=True)

    class Meta:
        verbose_name = "Credencial de Acesso / QR Code"
        verbose_name_plural = "Credenciais de Acesso / QR Codes"

    @property
    def is_valid(self):
        return self.revoked_at is None and (not self.expires_at or self.expires_at > timezone.now())


class RegistrationTransfer(models.Model):
    """
    Histórico auditado de transferência de titularidade de inscrição.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_registration = models.ForeignKey(Registration, on_delete=models.PROTECT, related_name="transfers_out")
    to_registration = models.ForeignKey(Registration, on_delete=models.PROTECT, related_name="transfers_in")
    initiated_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="initiated_transfers")
    reason = models.TextField("Motivo da Transferência")
    approved_by = models.ForeignKey("identity.User", on_delete=models.SET_NULL, blank=True, null=True)
    completed_at = models.DateTimeField("Concluída em", default=timezone.now)

    class Meta:
        verbose_name = "Transferência de Inscrição"
        verbose_name_plural = "Transferências de Inscrições"


class BenefitType(models.TextChoices):
    KIT = "KIT", "Kit do Piloto (Sacola/Adesivos/Brindes)"
    CAMISETA = "CAMISETA", "Camiseta Oficial"
    REFEICAO = "REFEICAO", "Alimentação (Café / Almoço)"
    ACESSO_PISTA = "ACESSO_PISTA", "Pulseira de Acesso à Trilha"
    TROFEU = "TROFEU", "Troféu de Participação"
    MEDALHA = "MEDALHA", "Medalha de Participação"

class EntitlementState(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Disponível para Retirada"
    PARTIALLY_USED = "PARTIALLY_USED", "Parcialmente Entregue"
    CONSUMED = "CONSUMED", "Totalmente Entregue"
    REVOKED = "REVOKED", "Revogado por Cancelamento/Transferência"

class Entitlement(models.Model):
    """
    Direito individual consumível associado à inscrição.
    Ex: 1 Kit, 1 Camiseta G, 1 Refeição.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name="entitlements")
    benefit_type = models.CharField("Tipo do Benefício", max_length=30, choices=BenefitType.choices)
    title = models.CharField("Descrição do Benefício", max_length=150)
    quantity_total = models.PositiveIntegerField("Quantidade Total Concedida", default=1)
    quantity_used = models.PositiveIntegerField("Quantidade Já Consumida/Entregue", default=0)
    state = models.CharField("Situação", max_length=30, choices=EntitlementState.choices, default=EntitlementState.AVAILABLE)

    class Meta:
        verbose_name = "Direito Concedido (Benefício)"
        verbose_name_plural = "Direitos Concedidos (Benefícios)"

    def __str__(self):
        return f"{self.title} ({self.quantity_used}/{self.quantity_total}) - {self.registration.person.full_name}"

    @property
    def remaining(self):
        return max(0, self.quantity_total - self.quantity_used)


class Consumption(models.Model):
    """
    Registro atômico e imutável de entrega de item ou presença.
    Prevenção contra dupla entrega simultânea via source_key única.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entitlement = models.ForeignKey(Entitlement, on_delete=models.PROTECT, related_name="consumptions")
    quantity = models.PositiveIntegerField("Quantidade Entregue", default=1)
    action = models.CharField("Ação", max_length=30, default="DELIVERED")  # DELIVERED, REVERSED
    operator = models.ForeignKey("identity.User", on_delete=models.PROTECT)
    location = models.CharField("Local da Entrega", max_length=100, default="Secretaria Geral")
    source_key = models.CharField("Chave de Idempotência da Entrega", max_length=150, unique=True)
    occurred_at = models.DateTimeField("Data e Hora", default=timezone.now)
    notes = models.TextField("Observações", blank=True, null=True)
    reversal_of = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True)

    class Meta:
        verbose_name = "Registro de Entrega de Benefício"
        verbose_name_plural = "Registros de Entregas de Benefícios"
        ordering = ["-occurred_at"]


class WaitlistEntry(models.Model):
    """
    Fila de espera para quando as vagas de um lote ou evento se esgotarem.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="waitlist_entries")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE)
    person = models.ForeignKey("identity.Person", on_delete=models.CASCADE)
    joined_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField("Situação", max_length=20, default="WAITING")  # WAITING, OFFERED, EXPIRED, CONVERTED

    class Meta:
        verbose_name = "Entrada na Lista de Espera"
        verbose_name_plural = "Entradas na Lista de Espera"
        ordering = ["joined_at"]
