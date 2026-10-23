import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusuário precisa ter is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusuário precisa ter is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Usuário autenticável do sistema (E-mail único).
    Pode ser um membro da equipe gestora ou um piloto/comprador acessando a Minha Conta.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("E-mail", unique=True, db_index=True)
    full_name = models.CharField("Nome Completo", max_length=150)
    phone = models.CharField("WhatsApp / Telefone", max_length=30, blank=True, null=True)
    is_active = models.BooleanField("Ativo", default=True)
    is_staff = models.BooleanField("Equipe", default=False)
    mfa_enabled = models.BooleanField("MFA Ativado", default=False)
    date_joined = models.DateTimeField("Data de Cadastro", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.full_name} ({self.email})"

    def clean(self):
        super().clean()
        if self.email:
            self.email = self.email.lower().strip()


class Person(models.Model):
    """
    Pessoa física real.
    Pode ser um piloto, acompanhante, apoio ou pagador.
    Não exige necessariamente um login criado de imediato.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField("Nome Completo", max_length=150, db_index=True)
    email = models.EmailField("E-mail", blank=True, null=True, db_index=True)
    phone = models.CharField("Telefone / WhatsApp", max_length=30, blank=True, null=True)
    document_number = models.CharField("CPF / Documento", max_length=20, blank=True, null=True, db_index=True)
    city = models.CharField("Cidade", max_length=100, default="Tianguá")
    state = models.CharField("UF", max_length=2, default="CE")
    birthdate = models.DateField("Data de Nascimento", blank=True, null=True)
    team_name = models.CharField("Equipe / Moto Grupo", max_length=120, blank=True, null=True)
    emergency_contact_name = models.CharField("Contato de Emergência", max_length=120, blank=True, null=True)
    emergency_contact_phone = models.CharField("Telefone de Emergência", max_length=30, blank=True, null=True)
    guardian = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="dependents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pessoa"
        verbose_name_plural = "Pessoas"
        ordering = ["full_name"]

    def __str__(self):
        city_uf = f" - {self.city}/{self.state}" if self.city and self.state else ""
        return f"{self.full_name}{city_uf}"


class UserPersonLink(models.Model):
    """
    Vínculo comprovado entre uma conta de usuário e uma pessoa física.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="person_links")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="user_links")
    verified_at = models.DateTimeField("Verificado em", default=timezone.now)
    method = models.CharField("Método", max_length=50, default="SELF_REGISTRATION")

    class Meta:
        unique_together = ("user", "person")
        verbose_name = "Vínculo Usuário-Pessoa"
        verbose_name_plural = "Vínculos Usuário-Pessoa"


class RoleChoices(models.TextChoices):
    OWNER = "OWNER", "Proprietário Geral / Kelvim"
    EVENT_MANAGER = "EVENT_MANAGER", "Gerente do Evento"
    FINANCE = "FINANCE", "Financeiro / Tesouraria"
    REGISTRATION_AGENT = "REGISTRATION_AGENT", "Atendimento / Inscrições"
    CHECKIN_AGENT = "CHECKIN_AGENT", "Operador de Credenciamento"
    LOGISTICS = "LOGISTICS", "Logística / Quartos / Transporte"
    CONTENT = "CONTENT", "Conteúdo / Mídia / Molduras"
    SAFETY = "SAFETY", "Apoio de Segurança / Ocorrências"


class EventMembership(models.Model):
    """
    Papéis e permissões da equipe da Morcego por evento ou global.
    event=None indica papel global (ex: Kelvim Muniz como OWNER).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="event_memberships")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, blank=True, null=True, related_name="team_memberships")
    role_code = models.CharField("Papel", max_length=30, choices=RoleChoices.choices)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="granted_memberships")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField("Revogado em", blank=True, null=True)

    class Meta:
        verbose_name = "Membro da Equipe do Evento"
        verbose_name_plural = "Membros da Equipe dos Eventos"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event", "role_code"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_user_event_role"
            )
        ]

    def __str__(self):
        event_str = self.event.title if self.event else "GLOBAL (Todos os Eventos)"
        return f"{self.user.full_name} [{self.get_role_code_display()}] @ {event_str}"

    @property
    def is_active(self):
        return self.revoked_at is None


class ConsentPreference(models.Model):
    """
    Registro explícito de consentimento e LGPD.
    Finalidade promocional separada da finalidade transacional.
    """
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="consents")
    channel = models.CharField("Canal", max_length=30, default="EMAIL")  # EMAIL, WHATSAPP, SMS
    purpose = models.CharField("Finalidade", max_length=50, default="TRANSACTIONAL")  # TRANSACTIONAL, MARKETING
    value = models.BooleanField("Consentido", default=True)
    version = models.CharField("Versão dos Termos", max_length=20, default="1.0")
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Preferência de Consentimento LGPD"
        verbose_name_plural = "Preferências de Consentimento LGPD"


class ClaimToken(models.Model):
    """
    Token seguro para reivindicação de inscrição comprada por terceiros (compras em grupo).
    O token original é entregue apenas no link e gravado aqui como hash SHA-256.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey("registrations.Registration", on_delete=models.CASCADE, related_name="claim_tokens")
    hashed_token = models.CharField("Hash do Token", max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField("Expira em")
    used_at = models.DateTimeField("Usado em", blank=True, null=True)
    sent_to_email = models.CharField("Enviado para", max_length=150, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Token de Reivindicação"
        verbose_name_plural = "Tokens de Reivindicação"

    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class CheckoutAccount(models.Model):
    """Origin of a new buyer account; only its original checkout may set its first password."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="checkout_origin")
    order = models.OneToOneField("checkout.Order", on_delete=models.PROTECT, related_name="created_account")
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(blank=True, null=True)
