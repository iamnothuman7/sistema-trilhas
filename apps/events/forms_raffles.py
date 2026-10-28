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
        fields = ("title", "motorcycle_model", "motorcycle_count", "description", "quota_price", "quota_quantity", "draw_at", "rules_text", "is_public", "interest_enabled", "registration_tickets_enabled", "paper_start", "paper_end")
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
        start, end = data.get('paper_start'), data.get('paper_end')
        if start is not None and end is not None and (end < start or end-start >= 5000):
            self.add_error('paper_end', 'Use uma faixa crescente de até 5.000 números.')
        if self.instance.pk and not self.instance._state.adding and self.instance.tickets.exists() and any(name in self.changed_data for name in ['paper_start','paper_end']):
            self.add_error('paper_start', 'A faixa não pode mudar depois da emissão de números. Isso evita bilhetes duplicados.')
        if data.get('motorcycle_count') is not None and not 1 <= data['motorcycle_count'] <= 10:
            self.add_error('motorcycle_count', 'Informe entre 1 e 10 motos.')
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
