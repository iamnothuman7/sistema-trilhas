import uuid
from django import forms
from django.utils import timezone
from apps.core.utils import cents_to_reais
from apps.finance.forms import parse_amount_cents
from apps.finance.models import SponsorPaymentMethod
from .forms import SponsorForm
from .models import SponsorProfile, SponsorTier


def style_fields(form):
    for name, field in form.fields.items():
        if not field.widget.is_hidden:
            field.widget.attrs.setdefault("class", "form-control")
            field.widget.attrs["aria-describedby"] = f"{form[name].id_for_label}_helptext"


class SponsorProfileForm(SponsorForm):
    class Meta(SponsorForm.Meta):
        model = SponsorProfile
        fields = ("name", "logo_image", "instagram_url", "whatsapp_number", "website", "contact_name", "contact_phone", "contact_email", "notes")
        widgets = {
            **SponsorForm.Meta.widgets,
            "contact_phone": forms.TextInput(attrs={"data-mask": "phone", "inputmode": "tel"}),
            "notes": forms.Textarea(attrs={"rows": 3, "maxlength": 2000}),
        }

    def clean_notes(self):
        text = self.cleaned_data["notes"]
        if len(text) > 2000:
            raise forms.ValidationError("Use até 2.000 caracteres.")
        return text


class SponsorLinkForm(forms.Form):
    profile = forms.ModelChoiceField(label="Marca cadastrada", queryset=SponsorProfile.objects.none(), empty_label="Escolha um patrocinador")
    tier = forms.ChoiceField(label="Cota de patrocínio", choices=SponsorTier.choices, initial=SponsorTier.OURO)
    sort_order = forms.IntegerField(label="Ordem de exibição", min_value=0, initial=0)
    is_public = forms.BooleanField(label="Autorizar exibição na página deste evento", required=False,
        help_text="A autorização vale somente para esta edição e independe do pagamento.")

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profile"].queryset = SponsorProfile.objects.filter(organization=event.organization).exclude(participations__event=event)
        style_fields(self)


class SponsorAgreementForm(forms.Form):
    amount = forms.CharField(label="Valor combinado (R$)", max_length=30, required=False,
        help_text="Deixe vazio enquanto negocia. Use 0,00 para um apoio sem cobrança.",
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ex.: 1.500,00"}))
    due_at = forms.DateField(label="Prazo para quitar", required=False, widget=forms.DateInput(attrs={"type": "date"}), input_formats=["%Y-%m-%d"])
    agreement_notes = forms.CharField(label="Observações do acordo", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Contrapartidas, condições e outras combinações. Visível somente para a equipe financeira.")
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0, required=False)

    def __init__(self, *args, sponsor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if sponsor:
            self.initial.update(amount="" if sponsor.agreed_amount_cents is None else cents_to_reais(sponsor.agreed_amount_cents),
                due_at=sponsor.due_at.isoformat() if sponsor.due_at else "", agreement_notes=sponsor.agreement_notes, version=sponsor.finance_version)
            self.fields["version"].required = True
        style_fields(self)

    def clean_amount(self):
        raw = self.cleaned_data["amount"].strip()
        if not raw:
            return None
        if raw in {"0", "0,0", "0,00", "0.0", "0.00"}:
            return 0
        value = parse_amount_cents(raw)
        if value > 99999999999:
            raise forms.ValidationError("O valor máximo é R$ 999.999.999,99.")
        return value


class SponsorReceiptForm(forms.Form):
    amount = forms.CharField(label="Valor recebido (R$)", max_length=30, widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"}))
    received_on = forms.DateField(label="Recebido em", widget=forms.DateInput(attrs={"type": "date"}), input_formats=["%Y-%m-%d"])
    method = forms.ChoiceField(label="Forma de recebimento", choices=SponsorPaymentMethod.choices)
    reference = forms.CharField(label="Referência do comprovante", max_length=120, required=False, help_text="Ex.: identificador do Pix ou número do recibo.")
    notes = forms.CharField(label="Observação desta baixa", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    confirm_received = forms.BooleanField(label="Confirmo que este valor já foi recebido pela organização.")
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("received_on", timezone.localdate().isoformat())
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.fields["received_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        style_fields(self)

    def clean_amount(self):
        return parse_amount_cents(self.cleaned_data["amount"])

    def clean_received_on(self):
        value = self.cleaned_data["received_on"]
        if value > timezone.localdate():
            raise forms.ValidationError("Um recebimento realizado não pode ter data futura.")
        return value


class SponsorReversalForm(forms.Form):
    reason = forms.CharField(label="Motivo da reversão", min_length=5, max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="O recebimento permanece no histórico e um ajuste é lançado no financeiro. Isto não devolve dinheiro automaticamente.")
    confirm = forms.BooleanField(label="Confirmo a reversão desta baixa e a reabertura do saldo.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
