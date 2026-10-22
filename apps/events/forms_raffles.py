from decimal import Decimal

from django import forms

from .models_raffles import MotorcycleRaffle, RAFFLE_INTEREST_CONSENT


class MotorcycleRaffleForm(forms.ModelForm):
    quota_price = forms.DecimalField(
        label="Valor previsto por cota (R$)", required=False, min_value=Decimal("0.01"),
        max_digits=9, decimal_places=2, localize=True,
        help_text="Deixe em branco até a organização definir o valor.",
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "A definir"}),
    )

    class Meta:
        model = MotorcycleRaffle
        fields = ("title", "motorcycle_model", "description", "quota_price", "quota_quantity", "draw_at", "rules_text", "is_public", "interest_enabled")
        widgets = {
            "motorcycle_model": forms.TextInput(attrs={"placeholder": "A definir"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "quota_quantity": forms.NumberInput(attrs={"min": 1, "placeholder": "A definir"}),
            "draw_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "rules_text": forms.Textarea(attrs={"rows": 6}),
        }
        help_texts = {
            "motorcycle_model": "Informe somente os dados confirmados da moto.",
            "draw_at": "Horário do evento. Deixe em branco se ainda não houver data definida.",
            "rules_text": "Regras específicas do sorteio, separadas dos termos de inscrição no evento.",
            "is_public": "Exibe os dados preenchidos na página do sorteio. Não libera vendas ou pagamentos.",
            "interest_enabled": "Disponível apenas com a apresentação pública. Não reserva número nem gera cobrança.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.quota_price_cents is not None:
            self.initial.setdefault("quota_price", Decimal(self.instance.quota_price_cents) / 100)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        data = super().clean()
        if data.get("interest_enabled") and not data.get("is_public"):
            self.add_error("interest_enabled", "Publique a apresentação antes de habilitar o registro de interesse.")
        return data

    def save(self, commit=True):
        price = self.cleaned_data.get("quota_price")
        self.instance.quota_price_cents = int(price * 100) if price is not None else None
        return super().save(commit=commit)


class MotorcycleRaffleAdminForm(MotorcycleRaffleForm):
    class Meta(MotorcycleRaffleForm.Meta):
        fields = ("event", *MotorcycleRaffleForm.Meta.fields)


class RaffleInterestForm(forms.Form):
    consent = forms.BooleanField(label=RAFFLE_INTEREST_CONSENT, required=True)
