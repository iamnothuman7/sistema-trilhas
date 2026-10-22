import re
import uuid
from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from .models import ExpenseCategory, ExpenseStatus, Vendor


def parse_amount_cents(value):
    """Parse Brazilian money without float arithmetic or silent cent rounding."""
    raw = str(value or "").strip().replace("R$", "").replace("\u00a0", "").replace(" ", "")
    if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?|\d+\.\d{1,2}", raw):
        raise forms.ValidationError("Informe um valor positivo, com no máximo duas casas decimais.")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        cents = Decimal(raw) * 100
        if not cents.is_finite() or cents <= 0 or cents > 9223372036854775807:
            raise InvalidOperation
        return int(cents)
    except (InvalidOperation, ValueError):
        raise forms.ValidationError("Informe um valor de despesa válido e maior que zero.")


class ExpenseForm(forms.Form):
    description = forms.CharField(label="Descrição", max_length=200)
    category = forms.ChoiceField(label="Categoria", choices=ExpenseCategory.choices)
    vendor_id = forms.ModelChoiceField(label="Fornecedor", queryset=Vendor.objects.none(), required=False, empty_label="Sem fornecedor")
    amount = forms.CharField(label="Valor (R$)", max_length=30, widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"}))
    due_at = forms.DateField(label="Vencimento", widget=forms.DateInput(attrs={"type": "date"}), input_formats=["%Y-%m-%d"])
    status = forms.ChoiceField(label="Situação", choices=[(ExpenseStatus.PAID, "Já paga"), (ExpenseStatus.PENDING, "A pagar")])
    paid_at = forms.DateField(label="Data do pagamento", required=False, widget=forms.DateInput(attrs={"type": "date"}), input_formats=["%Y-%m-%d"])
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.fields["vendor_id"].queryset = Vendor.objects.filter(organization=event.organization).order_by("name")
        today = timezone.localdate()
        self.initial.setdefault("due_at", today.isoformat())
        self.initial.setdefault("paid_at", today.isoformat())
        self.initial.setdefault("status", ExpenseStatus.PAID)
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.fields["paid_at"].widget.attrs["max"] = today.isoformat()
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs["class"] = "form-control"

    def clean_amount(self):
        return parse_amount_cents(self.cleaned_data["amount"])

    def clean(self):
        data = super().clean()
        if data.get("status") == ExpenseStatus.PAID:
            data["paid_at"] = data.get("paid_at") or timezone.localdate()
            if data["paid_at"] > timezone.localdate():
                self.add_error("paid_at", "Um pagamento realizado não pode ter data futura.")
        else:
            data["paid_at"] = None
        return data
