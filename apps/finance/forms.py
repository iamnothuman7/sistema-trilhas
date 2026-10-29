import re
import uuid
from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from .models import ExpenseCategory, ExpenseStatus, Vendor
from .receipts import validate_receipt, RECEIPT_ACCEPT, RECEIPT_HELP


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
    raffle_expense = forms.BooleanField(label='Esta saída pertence ao sorteio da moto',required=False,
        help_text='Também aparece no financeiro do sorteio, sem duplicar a saída no financeiro geral.')
    description = forms.CharField(label="Descrição", max_length=200)
    category = forms.ChoiceField(label="Categoria", choices=ExpenseCategory.choices)
    vendor_id = forms.ModelChoiceField(label="Fornecedor", queryset=Vendor.objects.none(), required=False, empty_label="Sem fornecedor")
    amount = forms.CharField(label="Valor (R$)", max_length=30, widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"}))
    due_at = forms.DateField(label="Vencimento", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
    status = forms.ChoiceField(label="Situação", choices=[(ExpenseStatus.PAID, "Já paga"), (ExpenseStatus.PENDING, "A pagar")])
    paid_at = forms.DateField(label="Data do pagamento", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"])
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)
    proof_asset = forms.FileField(label='Comprovante / recibo da saída', required=False, validators=[validate_receipt],
        widget=forms.FileInput(attrs={'accept':RECEIPT_ACCEPT}), help_text=RECEIPT_HELP)

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
        from apps.events.models_raffles import MotorcycleRaffle
        data['raffle']=MotorcycleRaffle.objects.filter(event=self.event).first() if data.get('raffle_expense') else None
        if data.get('raffle_expense') and not data['raffle']:
            self.add_error('raffle_expense','Salve primeiro a configuração do sorteio deste evento.')
        if data.get("status") == ExpenseStatus.PAID:
            data["paid_at"] = data.get("paid_at") or timezone.localdate()
            if data["paid_at"] > timezone.localdate():
                self.add_error("paid_at", "Um pagamento realizado não pode ter data futura.")
        else:
            data["paid_at"] = None
        return data


class ExpensePaymentForm(forms.Form):
    paid_at = forms.DateField(label='Data do pagamento', required=False, widget=forms.DateInput(attrs={'type':'date'}), input_formats=['%Y-%m-%d'])
    proof_asset = forms.FileField(label='Comprovante / recibo da saída', required=False, validators=[validate_receipt],
        widget=forms.FileInput(attrs={'accept':RECEIPT_ACCEPT}), help_text=RECEIPT_HELP)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault('paid_at',timezone.localdate().isoformat())
        for field in self.fields.values(): field.widget.attrs['class']='form-control'
        self.fields['paid_at'].widget.attrs['max']=timezone.localdate().isoformat()

    def clean_paid_at(self):
        value=self.cleaned_data.get('paid_at') or timezone.localdate()
        if value>timezone.localdate():raise forms.ValidationError('Um pagamento realizado não pode ter data futura.')
        return value


class ExpenseProofForm(ExpensePaymentForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields.pop('paid_at')
        self.fields['proof_asset'].required=True


def expense_version(expense):
    import hashlib,json
    values={name:str(getattr(expense,name) or '') for name in
        ['description','category','vendor_id','amount_cents','due_at','paid_at','status','notes','proof_asset','raffle_id','revision']}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


class ExpenseEditForm(ExpenseForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo da correção',min_length=10,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))

    def __init__(self,*args,expense,**kwargs):
        self.expense=expense
        super().__init__(*args,event=expense.event,**kwargs)
        self.fields.pop('idempotency_key')
        self.initial.update(description=expense.description,category=expense.category,vendor_id=expense.vendor_id,
            amount=expense.formatted_amount,due_at=expense.due_at,paid_at=expense.paid_at,status=expense.status,version=expense_version(expense),raffle_expense=bool(expense.raffle_id))

    def clean(self):
        data=super().clean()
        if data.get('version')!=expense_version(self.expense):raise forms.ValidationError('Esta despesa mudou em outra aba. Atualize a página antes de editar.')
        return data


class ExpenseStatusForm(ExpensePaymentForm):
    status = forms.ChoiceField(label='Situação', choices=[('PENDING','A pagar'),('PAID','Paga / concluída')])
    version = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Observação (opcional)', required=False, max_length=1000)

    def clean_paid_at(self):
        return None if self.data.get('status')=='PENDING' else super().clean_paid_at()
