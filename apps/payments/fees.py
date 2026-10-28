"""Immutable checkout fee quotes and integer-cent gross-up calculations."""
from dataclasses import dataclass, asdict
from decimal import Decimal
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from apps.core.utils import cents_to_reais
from .models import PaymentFeePolicy, PaymentMethod

OFFICIAL_FEE_SOURCE = 'https://www.mercadopago.com.br/ferramentas-para-vender/check-out'
DEFAULT_INSTALLMENTS = {'2':751,'3':960,'4':1167,'5':1364,'6':1494,'7':1622,'8':1748,'9':1871,'10':1991,'11':2110,'12':2226}


def gross_up(net_cents, rate_bps, fixed_cents=0):
    if not 0 <= rate_bps <= 5000 or net_cents < 0 or fixed_cents < 0:
        raise ValidationError('A tarifa de pagamento está inválida. Fale com a organização.')
    if not net_cents:
        return 0
    gross = ((net_cents + fixed_cents) * 10000 + (9999-rate_bps)) // (10000-rate_bps)
    def net(value):
        return value - ((value * rate_bps + 5000) // 10000 + fixed_cents)
    while gross > net_cents and net(gross - 1) >= net_cents:
        gross -= 1
    return gross


@dataclass
class FeeQuote:
    order: str
    method: str
    installments: int
    base: int
    fee: int
    rate_bps: int
    fixed_cents: int

    @property
    def total(self): return self.base + self.fee
    @property
    def formatted_total(self): return cents_to_reais(self.total)
    @property
    def formatted_fee(self): return cents_to_reais(self.fee)
    @property
    def percent(self): return format(Decimal(self.rate_bps) / 100, '.2f').replace('.', ',')
    @property
    def token(self): return signing.dumps(asdict(self), salt='checkout-fee-quote')


def policy():
    return PaymentFeePolicy.objects.filter(pk=1).first() or PaymentFeePolicy()


def quote_for(order, method, installments=1, configuration=None):
    config = configuration or policy()
    if installments < 1 or installments > (12 if config._state.adding and not config.enabled else config.max_installments):
        raise ValidationError('Selecione uma quantidade de parcelas disponível.')
    rate, fixed = 0, 0
    if config.enabled and (config.pix_pass_through if method == PaymentMethod.PIX else config.card_pass_through):
        if method == PaymentMethod.PIX:
            rate, fixed = config.pix_bps, config.pix_fixed_cents
        else:
            rate = config.card_bps if installments == 1 else config.installment_rates.get(str(installments))
            if not isinstance(rate, int):
                raise ValidationError('A tarifa desta quantidade de parcelas ainda não foi configurada.')
            fixed = config.card_fixed_cents
    total = gross_up(order.base_total_cents, rate, fixed)
    return FeeQuote(str(order.pk), method, installments, order.base_total_cents, total-order.base_total_cents, rate, fixed)


def submitted_quote(order, method, installments, token):
    if not token and not policy().enabled:
        return quote_for(order, method, installments)
    try:
        value = signing.loads(token, salt='checkout-fee-quote', max_age=1800)
        quote = FeeQuote(**value)
        if (quote.order != str(order.pk) or quote.method != method or quote.installments != installments
                or quote.base != order.base_total_cents
                or quote.total != gross_up(quote.base, quote.rate_bps, quote.fixed_cents)):
            raise ValueError
        return quote
    except (signing.BadSignature, AssertionError, TypeError, ValueError, KeyError, ValidationError):
        raise ValidationError('O valor do pagamento foi atualizado ou expirou. Confira o total novamente antes de pagar.') from None


class PaymentFeeForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    commission_percent = forms.DecimalField(label='Comissão sobre o valor dos itens (%)', min_value=0, max_value=50, decimal_places=2, initial=0)
    commission_fixed = forms.DecimalField(label='Comissão fixa por pedido (R$)', min_value=0, max_value=1000, decimal_places=2, initial=0)
    pix_percent = forms.DecimalField(label='Tarifa Pix (%)', min_value=0, max_value=50, decimal_places=2, initial=Decimal('0.99'))
    card_percent = forms.DecimalField(label='Tarifa cartão à vista (%)', min_value=0, max_value=50, decimal_places=2, initial=Decimal('4.98'))
    pix_fixed = forms.DecimalField(label='Tarifa fixa Pix (R$)', min_value=0, max_value=1000, decimal_places=2, initial=0)
    card_fixed = forms.DecimalField(label='Tarifa fixa cartão (R$)', min_value=0, max_value=1000, decimal_places=2, initial=0)
    max_installments = forms.IntegerField(label='Máximo de parcelas no cartão', min_value=1, max_value=12)

    class Meta:
        model = PaymentFeePolicy
        fields = ['enabled', 'pix_pass_through', 'pix_percent', 'pix_fixed', 'card_pass_through', 'card_percent', 'card_fixed', 'max_installments',
            'commission_requested', 'commission_percent', 'commission_fixed', 'commission_payer', 'commission_recipient', 'commission_account_id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['version'] = self.instance.revision
        self.initial.update(pix_percent=Decimal(self.instance.pix_bps)/100, card_percent=Decimal(self.instance.card_bps)/100,
            pix_fixed=Decimal(self.instance.pix_fixed_cents)/100, card_fixed=Decimal(self.instance.card_fixed_cents)/100,
            commission_percent=Decimal(self.instance.commission_bps)/100, commission_fixed=Decimal(self.instance.commission_fixed_cents)/100)
        for number in range(2, 13):
            self.fields[f'installment_{number}'] = forms.DecimalField(label=f'{number} parcelas — tarifa total (%)',
                required=False, min_value=0, max_value=50, decimal_places=2,
                initial=Decimal(self.instance.installment_rates.get(str(number), DEFAULT_INSTALLMENTS[str(number)]))/100)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'
            if isinstance(field, forms.DecimalField): field.widget.attrs['step'] = '0.01'

    def clean(self):
        data = super().clean()
        if data.get('version') != self.instance.revision:
            raise ValidationError('As regras foram alteradas em outra aba. Atualize a página antes de salvar novamente.')
        account = data.get('commission_account_id', '')
        if account and (not account.isascii() or not account.isdigit() or account.startswith('0')):
            self.add_error('commission_account_id', 'Informe somente o ID numérico da conta Mercado Pago.')
        if data.get('commission_requested'):
            if not data.get('commission_recipient'):
                self.add_error('commission_recipient', 'Informe o nome do destinatário pretendido.')
            if not data.get('commission_percent') and not data.get('commission_fixed'):
                self.add_error('commission_percent', 'Informe uma porcentagem ou comissão fixa para preparar o repasse.')
        for number in range(2, (data.get('max_installments') or 1) + 1):
            if data.get(f'installment_{number}') is None:
                self.add_error(f'installment_{number}', 'Informe a tarifa contratada para liberar esta quantidade de parcelas.')
        return data

    def save(self, commit=True):
        for target, source in [('pix_bps','pix_percent'),('card_bps','card_percent'),('pix_fixed_cents','pix_fixed'),('card_fixed_cents','card_fixed'),
            ('commission_bps','commission_percent'),('commission_fixed_cents','commission_fixed')]:
            setattr(self.instance, target, int(self.cleaned_data[source]*100))
        self.instance.installment_rates = {str(n):int(self.cleaned_data[f'installment_{n}']*100) for n in range(2,13) if self.cleaned_data.get(f'installment_{n}') is not None}
        self.instance.revision += 1
        return super().save(commit)
