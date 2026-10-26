"""Validated event editing, separate from sponsor and public checkout forms."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import CapacityResource
from apps.registrations.models import Registration, CommercialState
from .forms import SponsorLogoField
from .models import Event, EventTheme


class EventDateTimeField(forms.DateTimeField):
    event_timezone = ZoneInfo("America/Fortaleza")

    def to_python(self, value):
        with timezone.override(self.event_timezone):
            return super().to_python(value)

    def prepare_value(self, value):
        if hasattr(value, "tzinfo") and timezone.is_aware(value):
            return timezone.localtime(value, self.event_timezone).replace(tzinfo=None)
        return super().prepare_value(value)


class EventManagementForm(forms.ModelForm):
    version = forms.CharField(required=False, widget=forms.HiddenInput)
    timezone = forms.ChoiceField(label="Fuso horário")
    contact_email = forms.EmailField(label="E-mail de atendimento", required=False)
    contact_phone = forms.CharField(label="Telefone de atendimento", max_length=30, required=False)
    max_attendees = forms.IntegerField(label="Capacidade de participantes", min_value=1)
    edition_number = forms.IntegerField(label="Número da edição", min_value=1)
    slug = forms.SlugField(label="Endereço da edição", max_length=200, required=False)
    for _name, _label in (
        ("starts_at", "Início do evento"), ("ends_at", "Fim do evento"),
        ("registration_opens_at", "Abertura das inscrições"),
        ("registration_closes_at", "Encerramento das inscrições"),
    ):
        locals()[_name] = EventDateTimeField(label=_label, input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
            widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))

    class Meta:
        model = Event
        fields = ("organization", "title", "slug", "edition_number", "type", "status", "city", "state", "venue",
                  "timezone", "starts_at", "ends_at", "registration_opens_at", "registration_closes_at",
                  "max_attendees", "description", "rules_summary", "contact_email", "contact_phone")
        labels = {"organization": "Organização responsável", "title": "Nome do evento", "state": "UF", "venue": "Local de encontro",
                  "status": "Situação da edição", "description": "Apresentação", "rules_summary": "Orientações resumidas"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4}), "rules_summary": forms.Textarea(attrs={"rows": 4}),
                   "state": forms.TextInput(attrs={"maxlength": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.creating = self.instance._state.adding
        self.version_conflict = False
        zones = [("America/Fortaleza", "Fortaleza / Ceará"), ("America/Sao_Paulo", "Brasília / São Paulo"),
                 ("America/Manaus", "Manaus"), ("America/Rio_Branco", "Rio Branco"), ("UTC", "UTC")]
        current = self.instance.timezone or "America/Fortaleza"
        if current not in dict(zones):
            zones.append((current, current))
        self.fields["timezone"].choices = zones
        selected = self.data.get("timezone", current) if self.is_bound else current
        try:
            zone = ZoneInfo(selected)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            zone = ZoneInfo("America/Fortaleza")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            if isinstance(field, EventDateTimeField):
                field.event_timezone = zone
        self.fields["slug"].help_text = "Opcional ao criar. Usamos o nome para montar o endereço da edição."
        self.fields["timezone"].help_text = "Todos os horários deste formulário seguem o fuso selecionado."
        self.fields["max_attendees"].help_text = "Não pode ficar abaixo das vagas já confirmadas ou reservadas em checkouts."
        self.fields["rules_summary"].help_text = "Resumo público. Não substitui os documentos e aceites legais versionados."
        self.fields["status"].help_text = "Publicado permite inscrições apenas dentro das datas e com lote disponível. Cancelar não estorna pagamentos automaticamente."
        if self.creating:
            self.fields.pop("version")
            self.fields.pop("status")
            self.initial.update(contact_email="", contact_phone="")
            organizations = self.fields["organization"].queryset
            if organizations.count() == 1:
                self.initial["organization"] = organizations.first().pk
        else:
            self.initial["version"] = self.instance.updated_at.isoformat()
            self.fields.pop("organization")
            self.fields["slug"].disabled = True
            self.fields["slug"].help_text = "Endereço preservado para não quebrar links e inscrições existentes."

    def clean_state(self):
        value = self.cleaned_data["state"].strip().upper()
        if value not in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split():
            raise forms.ValidationError("Informe uma UF brasileira válida, como CE.")
        return value

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError("Escolha um fuso horário válido.")
        return value

    def clean_slug(self):
        value = self.cleaned_data.get("slug")
        if value or not self.creating:
            return value
        base = slugify(self.data.get("title", ""))[:180] or "evento"
        value, suffix = base, 2
        while Event.objects.filter(slug=value).exists():
            value, suffix = f"{base}-{suffix}", suffix + 1
        return value

    def clean_max_attendees(self):
        value = self.cleaned_data["max_attendees"]
        if not self.creating:
            resources = CapacityResource.objects.filter(event=self.instance, type="EVENT_OVERALL")
            counters = resources.aggregate(reserved=Sum("reserved_count"), committed=Sum("committed_count"))
            active = Registration.objects.filter(event=self.instance, commercial_state=CommercialState.ACTIVE).count()
            occupied = (counters["reserved"] or 0) + max(counters["committed"] or 0, active)
            if value < occupied:
                raise forms.ValidationError(f"A capacidade deve comportar pelo menos {occupied} vagas já utilizadas ou reservadas.")
            if resources.count() > 1 and value != self.instance.max_attendees:
                raise forms.ValidationError("Há mais de um controle de capacidade nesta edição. Solicite conferência antes de alterar o limite.")
        return value

    def clean(self):
        data = super().clean()
        # The view binds the form to the freshly locked row, as in Nabiobjj's
        # financial editor. A stale tab must not overwrite someone else's edit.
        if not self.creating and data.get("version") != self.instance.updated_at.isoformat():
            self.version_conflict = True
            self.add_error(None, "Este evento foi alterado em outra aba ou por outra pessoa. Copie suas alterações e reabra os dados atuais antes de salvar.")
        start, end = data.get("starts_at"), data.get("ends_at")
        opens, closes = data.get("registration_opens_at"), data.get("registration_closes_at")
        if start and end and end <= start:
            self.add_error("ends_at", "O fim deve ocorrer depois do início do evento.")
        if opens and closes and closes < opens:
            self.add_error("registration_closes_at", "O encerramento deve ocorrer depois da abertura das inscrições.")
        if closes and end and closes > end:
            self.add_error("registration_closes_at", "Encerre as inscrições até o fim do evento.")
        return data


class EventAppearanceForm(forms.ModelForm):
    hero_banner = SponsorLogoField(label="Banner da edição", required=False,
        help_text="PNG, JPG ou WebP, até 15 MB. Prefira uma imagem horizontal; confira o enquadramento no celular.")
    logo_image = SponsorLogoField(label="Logo da edição", required=False,
        help_text="PNG, JPG ou WebP, até 15 MB. Uma imagem com transparência preserva melhor a identidade visual.")
    cover_image = SponsorLogoField(label="Capa horizontal (16:9)", required=False,
        help_text="PNG, JPG ou WebP, até 15 MB. Usada na lista de eventos e na prévia ao compartilhar o link.")
    square_image = SponsorLogoField(label="Capa quadrada (1:1)", required=False,
        help_text="PNG, JPG ou WebP, até 15 MB. Usada no destaque da página inicial.")

    class Meta:
        model = EventTheme
        fields = ("hero_banner", "logo_image", "cover_image", "square_image")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control", "accept": "image/png,image/jpeg,image/webp"})
