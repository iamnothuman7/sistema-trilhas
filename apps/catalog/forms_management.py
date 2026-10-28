import hashlib
import json
from django import forms
from django.forms.models import model_to_dict
from apps.events.forms_management import EventDateTimeField
from apps.finance.forms import parse_amount_cents
from apps.reporting.selectors import event_timezone
from apps.checkout.models import OrderLine
from .models import Product, Lot, ProductVariant, Coupon, AddOn


def edit_version(instance):
    values = model_to_dict(instance, exclude=['current_sold', 'used_count'])
    return hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest()


class CatalogForm(forms.ModelForm):
    version = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.original_version = edit_version(self.instance)
        if not self.instance._state.adding:
            self.fields['version'].required = True
            self.initial['version'] = self.original_version
        for field in self.fields.values():
            if not field.widget.is_hidden:
                field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'
            if isinstance(field, EventDateTimeField):
                field.event_timezone = event_timezone(event)

    def clean(self):
        data = super().clean()
        if not self.instance._state.adding and data.get('version') != self.original_version:
            raise forms.ValidationError('Este cadastro foi alterado em outra sessão. Reabra a página antes de salvar.')
        for opening, closing in [('opens_at', 'closes_at'), ('valid_from', 'valid_until')]:
            if data.get(opening) and data.get(closing) and data[closing] <= data[opening]:
                self.add_error(closing, 'O encerramento precisa ser posterior à abertura.')
        return data


class ProductForm(CatalogForm):
    class Meta:
        model = Product
        fields = ['name', 'kind', 'description', 'includes_shirt', 'includes_meal', 'includes_kit', 'active']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and OrderLine.objects.filter(product=self.instance).exists():
            self.fields['kind'].disabled = True
            self.fields['kind'].help_text = 'Modalidade preservada porque já existem pedidos. Crie outro produto para uma modalidade diferente.'


class AddOnForm(CatalogForm):
    price = forms.CharField(label='Preço em reais', widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    max_quantity = forms.IntegerField(label='Estoque disponível para venda', min_value=0, max_value=2147483647)

    class Meta:
        model = AddOn
        fields = ['title', 'description', 'variant_code', 'price', 'max_quantity', 'active']
        widgets = {'description': forms.Textarea(attrs={'rows':3})}
        help_texts = {'variant_code':'Ex.: P, M, G ou uma variação do produto. Cadastre um item separado para cada estoque.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['price'] = self.instance.formatted_price

    def clean_price(self):
        return parse_amount_cents(self.cleaned_data['price'])

    def save(self, commit=True):
        self.instance.price_cents = self.cleaned_data['price']
        return super().save(commit)


def date_field(label):
    return EventDateTimeField(label=label, input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))


class LotForm(CatalogForm):
    price = forms.CharField(label='Preço em reais', help_text='Exemplo: 120,00. Alterações valem para novos pedidos.', widget=forms.TextInput(attrs={'inputmode': 'decimal'}))
    opens_at = date_field('Abertura das vendas')
    closes_at = date_field('Encerramento das vendas')
    max_quantity = forms.IntegerField(label='Limite de vagas do lote', min_value=1, max_value=2147483647)
    class Meta:
        model = Lot
        fields = ['title', 'price', 'opens_at', 'closes_at', 'max_quantity', 'priority', 'active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['price'] = self.instance.formatted_price
        self.fields['max_quantity'].help_text = 'O limite não pode ficar abaixo de vendas e reservas existentes. A capacidade geral do evento também continua valendo.'

    def clean_price(self):
        return parse_amount_cents(self.cleaned_data['price'])

    def save(self, commit=True):
        self.instance.price_cents = self.cleaned_data['price']
        return super().save(commit=commit)


class VariantForm(CatalogForm):
    extra_price = forms.CharField(label='Acréscimo em reais', help_text='Use 0,00 para não cobrar acréscimo.', widget=forms.TextInput(attrs={'inputmode': 'decimal'}))
    class Meta:
        model = ProductVariant
        fields = ['code', 'label', 'extra_price']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.utils import cents_to_reais
        self.initial['extra_price'] = cents_to_reais(self.instance.extra_price_cents)

    def clean_extra_price(self):
        value = self.cleaned_data['extra_price'].strip()
        if value in ('0', '0,0', '0.0', '0,00', '0.00'): return 0
        return parse_amount_cents(value)

    def clean_code(self):
        value = self.cleaned_data['code'].strip().upper()
        if ProductVariant.objects.filter(product=self.instance.product, kind=self.instance.kind, code=value).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Este código já existe neste produto.')
        return value

    def save(self, commit=True):
        self.instance.extra_price_cents = self.cleaned_data['extra_price']
        return super().save(commit=commit)


class CouponForm(CatalogForm):
    amount = forms.CharField(label='Desconto fixo em reais', required=False, widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': 'Ex.: 20,00'}))
    percent_off = forms.IntegerField(label='Percentual de desconto', required=False,
        widget=forms.NumberInput(attrs={'min': 1, 'max': 100, 'inputmode': 'numeric', 'placeholder': 'Ex.: 10'}))
    valid_from = date_field('Válido a partir de')
    valid_until = date_field('Válido até')
    max_uses = forms.IntegerField(label='Limite de usos', min_value=1, max_value=2147483647)
    class Meta:
        model = Coupon
        fields = ['code_upper', 'kind', 'amount', 'percent_off', 'valid_from', 'valid_until', 'max_uses', 'active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.utils import cents_to_reais
        self.initial['amount'] = cents_to_reais(self.instance.value_cents)
        self.selected_kind = self.data.get(self.add_prefix('kind'), self.instance.kind) if self.is_bound else self.instance.kind
        self.fields['percent_off'].required = self.selected_kind == 'PERCENT'
        self.fields['percent_off'].disabled = self.selected_kind == 'FIXED'
        self.fields['amount'].required = self.selected_kind == 'FIXED'
        self.fields['amount'].disabled = self.selected_kind == 'PERCENT'
        self.fields['percent_off'].help_text = 'Informe de 1 a 100. Ex.: 10 significa 10% de desconto.'
        self.fields['amount'].help_text = 'Valor em reais por pedido. Ex.: 20,00 desconta R$ 20,00.'
        self.fields['kind'].help_text = 'Escolha um desconto em reais ou em porcentagem. Só o valor do tipo selecionado é utilizado.'
        self.fields['max_uses'].help_text = f'{self.instance.used_count} uso(s) já registrado(s). O limite não pode ficar abaixo desse total.'
        self.fields['code_upper'].help_text = 'Você pode renomear mesmo após o uso. O novo código vale para novas compras; os pedidos anteriores mantêm o código e o desconto registrados na compra.'

    def clean_percent_off(self):
        if self.selected_kind != 'PERCENT':
            # Fixed coupons can have legacy zero/unused percentage values. They
            # are not a second discount and must not prevent editing the coupon.
            return self.instance.percent_off
        value = self.cleaned_data.get('percent_off')
        if value is not None and not 1 <= value <= 100:
            raise forms.ValidationError('Informe um percentual entre 1 e 100.')
        return value

    def clean_code_upper(self):
        value = self.cleaned_data['code_upper'].strip().upper()
        if Coupon.objects.filter(event=self.event, code_upper=value).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Este código já existe nesta edição.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'FIXED' and 'amount' in data:
            try: data['amount_cents'] = parse_amount_cents(data.get('amount'))
            except forms.ValidationError as exc: self.add_error('amount', exc)
        if data.get('max_uses', 0) < self.instance.used_count:
            self.add_error('max_uses', 'O limite não pode ser menor que os usos já registrados.')
        return data

    def save(self, commit=True):
        if self.cleaned_data['kind'] == 'FIXED': self.instance.value_cents = self.cleaned_data['amount_cents']
        return super().save(commit=commit)
