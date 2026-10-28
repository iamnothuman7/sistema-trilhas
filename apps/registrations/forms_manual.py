from django import forms
from django.core.exceptions import ValidationError
from apps.catalog.models import Lot
from apps.core.utils import cents_to_reais
from apps.identity.documents import normalize_cpf
from .models import VehicleType

PAYMENT_METHODS = [("CASH", "Dinheiro"), ("PIX_MANUAL", "Pix direto na conta da equipe"), ("CARD_EXTERNAL", "Cartão na maquininha")]


class PaymentReceiptForm(forms.Form):
    method = forms.ChoiceField(label="Forma de recebimento", choices=PAYMENT_METHODS, required=False)
    amount = forms.DecimalField(label="Valor recebido (R$)", min_value=0.01, max_digits=10, decimal_places=2,
        localize=True, required=False, widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "120,00"}))
    payment_notes = forms.CharField(label="Identificação do recebimento", max_length=1000, required=False,
        help_text="Informe a referência do Pix, da maquininha ou a identificação do recebimento em dinheiro.", widget=forms.Textarea(attrs={"rows": 3}))
    received_confirmed = forms.BooleanField(label="Conferi e confirmo que o valor integral já foi recebido pela equipe.", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput)):
                field.widget.attrs["class"] = "form-control"

    def clean(self):
        data = super().clean()
        if data.get("payment_state", "received") == "received":
            for name in ("method", "amount", "payment_notes", "received_confirmed"):
                if not data.get(name):
                    self.add_error(name, "Preencha e confirme o recebimento para registrar como pago.")
        return data


class LotChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.product.name} · {obj.title} · R$ {cents_to_reais(obj.price_cents)}"


class ManualRegistrationForm(PaymentReceiptForm):
    request_token = forms.CharField(widget=forms.HiddenInput)
    name = forms.CharField(label="Nome completo do piloto", max_length=150)
    cpf = forms.CharField(label="CPF do piloto", max_length=14, widget=forms.TextInput(attrs={"data-mask": "cpf", "inputmode": "numeric", "autocomplete": "off"}))
    email = forms.EmailField(label="E-mail do responsável pelo acesso", max_length=254,
        help_text="A conta e a inscrição ficarão no acesso do responsável informado. Confira o endereço com o participante.")
    phone = forms.CharField(label="WhatsApp", max_length=30, widget=forms.TextInput(attrs={"data-mask": "phone", "inputmode": "tel"}))
    city = forms.CharField(label="Cidade", max_length=100)
    state = forms.RegexField(label="UF", regex=r"^[A-Za-z]{2}$", max_length=2, widget=forms.TextInput(attrs={"data-mask": "uf"}))
    team = forms.CharField(label="Equipe / moto clube", max_length=120, required=False)
    lot = LotChoice(label="Pacote e lote", queryset=Lot.objects.none(), empty_label="Selecione o pacote")
    shirt = forms.ChoiceField(label="Camiseta", required=False, choices=[])
    vehicle = forms.ChoiceField(label="Veículo / modalidade", choices=VehicleType.choices, initial=VehicleType.MOTO, required=False)
    notes = forms.CharField(label="Motivo / observação do cadastro manual", max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
    payment_state = forms.ChoiceField(label="Situação do pagamento", choices=[("pending", "Aguardar pagamento — reserva por 35 minutos"), ("received", "Pagamento integral já recebido")])

    def __init__(self, *args, event, can_receive, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.catalog.offers import available_registration_lots
        self.fields["lot"].queryset = available_registration_lots(event)
        sizes = {"P", "M", "G", "GG", "XG"}
        from apps.catalog.models import ProductVariant
        sizes.update(ProductVariant.objects.filter(product__event=event, kind="SHIRT_SIZE").values_list("code", flat=True))
        self.fields["shirt"].choices = [("", "Sem camiseta / selecione o tamanho")] + [(size, size) for size in sorted(sizes)]
        if not can_receive:
            self.fields["payment_state"].choices = self.fields["payment_state"].choices[:1]

    def clean_cpf(self):
        return normalize_cpf(self.cleaned_data["cpf"])

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_state(self):
        return self.cleaned_data["state"].upper()

    def clean_phone(self):
        import re
        value = self.cleaned_data["phone"]
        if not 10 <= len(re.sub(r"\D", "", value)) <= 13:
            raise ValidationError("Informe um telefone com DDD.")
        return value
