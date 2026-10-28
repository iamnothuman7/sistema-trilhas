import re,uuid
from django import forms
from django.utils import timezone
from apps.finance.forms import parse_amount_cents
from apps.finance.receipts import validate_receipt,RECEIPT_ACCEPT,RECEIPT_HELP
from .models import VehicleType
from .management import registration_version


class RegistrationEditForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    full_name=forms.CharField(label='Nome completo',max_length=150)
    email=forms.EmailField(label='E-mail de contato',required=False)
    phone=forms.CharField(label='WhatsApp',max_length=30,required=False,widget=forms.TextInput(attrs={'data-mask':'phone'}))
    city=forms.CharField(label='Cidade',max_length=100)
    state=forms.RegexField(label='UF',regex=r'^[A-Za-z]{2}$',max_length=2,widget=forms.TextInput(attrs={'data-mask':'uf'}))
    team_name=forms.CharField(label='Equipe',max_length=120,required=False)
    vehicle_type=forms.ChoiceField(label='Veículo',choices=[('','Não informado'),*VehicleType.choices],required=False)
    shirt_size=forms.ChoiceField(label='Tamanho da camiseta')
    reason=forms.CharField(label='Motivo da correção',min_length=10,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))

    def __init__(self,*args,registration,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.catalog.models import ProductVariant
        sizes=set(ProductVariant.objects.filter(product_id=registration.source_order_line.product_id,kind='SHIRT_SIZE').values_list('code',flat=True))
        sizes.add(registration.shirt_size)
        self.fields['shirt_size'].choices=[(size,size or 'Sem camiseta') for size in sorted(sizes)]
        self.fields['shirt_size'].required=bool(registration.shirt_size)
        self.initial.update({key:getattr(registration.person,key) for key in ['full_name','email','phone','city','state']})
        self.initial.update({key:getattr(registration,key) for key in ['team_name','vehicle_type','shirt_size']})
        self.initial['version']=registration_version(registration)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')

    def clean_state(self):return self.cleaned_data['state'].upper()

    def clean_phone(self):
        value=self.cleaned_data['phone']
        if value and not 10<=len(re.sub(r'\D','',value))<=13:raise forms.ValidationError('Informe um telefone com DDD.')
        return value


class RegistrationRefundForm(forms.Form):
    request_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    amount=forms.CharField(label='Valor integral já devolvido (R$)',widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    returned_on=forms.DateField(label='Data da devolução realizada',input_formats=['%Y-%m-%d'],initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    reference=forms.CharField(label='Referência da devolução / transação',max_length=100)
    reason=forms.CharField(label='Motivo do cancelamento',min_length=10,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    proof_asset=forms.FileField(label='Comprovante da devolução',required=False,validators=[validate_receipt],
        help_text=RECEIPT_HELP,widget=forms.FileInput(attrs={'accept':RECEIPT_ACCEPT}))
    confirmed=forms.BooleanField(label='Confirmo que o valor integral informado JÁ FOI DEVOLVIDO ao comprador e que todas as inscrições deste pedido serão canceladas.')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['returned_on'].widget.attrs['max']=timezone.localdate().isoformat()
        for field in self.fields.values():
            if not isinstance(field.widget,forms.CheckboxInput):field.widget.attrs.setdefault('class','form-control')

    def clean_amount(self):return parse_amount_cents(self.cleaned_data['amount'])
