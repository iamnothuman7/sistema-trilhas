from decimal import Decimal

from django import forms

from .models_raffles import MotorcycleRaffle, RAFFLE_INTEREST_CONSENT
from .forms import SponsorLogoField
from .sponsor_images import make_sponsor_preview
from django.core.files.uploadedfile import UploadedFile


class MotorcycleRaffleForm(forms.ModelForm):
    primary_image = SponsorLogoField(label='Foto ou arte principal',required=False,
        help_text='PNG, JPG ou WebP, até 15 MB e 24 megapixels. PNG/WebP com transparência mantém o fundo removido.',
        widget=forms.ClearableFileInput(attrs={'accept':'image/png,image/jpeg,image/webp'}))
    secondary_image = SponsorLogoField(label='Foto ou arte da segunda moto',required=False,
        help_text='Opcional. Pode deixar em branco até preparar a segunda imagem.',
        widget=forms.ClearableFileInput(attrs={'accept':'image/png,image/jpeg,image/webp'}))
    quota_price = forms.DecimalField(
        label="Preço de cada número extra (R$)", required=False, min_value=Decimal("0.01"),
        max_digits=9, decimal_places=2, localize=True,
        help_text="Deixe em branco até a organização definir o valor.",
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "A definir"}),
    )

    class Meta:
        model = MotorcycleRaffle
        fields = ("title", "motorcycle_model", "motorcycle_count", "description", "primary_image", "secondary_image", "image_style", "quota_price", "quota_quantity", "draw_at", "rules_text", "authorization_reference", "provider_review_reference", "online_sales_enabled", "operation_review_confirmed", "is_public", "interest_enabled", "registration_tickets_enabled", "paper_start", "paper_end")
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
        self.fields['image_style'].required=False
        self.fields['quota_quantity'].label='Limite de números extras online'
        self.fields['quota_quantity'].help_text='Estoque exclusivo da compra online. O número incluído na inscrição e os bilhetes em papel não consomem este limite.'
        self.fields['online_sales_enabled'].help_text='Deixe desligado enquanto os dados estiverem em preparação. A mesma aplicação Mercado Pago das inscrições será usada.'
        if self.instance.quota_price_cents is not None:
            self.initial.setdefault("quota_price", Decimal(self.instance.quota_price_cents) / 100)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-control")

    def clean_image_style(self):
        return self.cleaned_data.get('image_style') or 'CUTOUT'

    def clean(self):
        data = super().clean()
        if not self.instance._state.adding and hasattr(self.instance,'result'):
            locked={'draw_at','rules_text','motorcycle_count','registration_tickets_enabled','paper_start','paper_end','quota_price','quota_quantity','online_sales_enabled'}
            if locked.intersection(self.changed_data):
                raise forms.ValidationError('O resultado foi registrado. Regras, prêmios, numeração e vendas deste sorteio estão encerrados.')
        for name in ('primary_image','secondary_image'):
            upload=data.get(name)
            if isinstance(upload,UploadedFile):
                try:
                    data[name]=make_sponsor_preview(upload)[0]
                except (OSError,ValueError):
                    self.add_error(name,'Não foi possível preparar a imagem. Selecione outro PNG, JPG ou WebP.')
        if data.get("interest_enabled") and not data.get("is_public"):
            self.add_error("interest_enabled", "Publique a apresentação antes de habilitar o registro de interesse.")
        if data.get('secondary_image') and (data.get('motorcycle_count') or 0)<2:
            self.add_error('motorcycle_count','Informe pelo menos duas motos para exibir a segunda imagem.')
        start, end = data.get('paper_start'), data.get('paper_end')
        if start is not None and end is not None and (end < start or end-start >= 5000):
            self.add_error('paper_end', 'Use uma faixa crescente de até 5.000 números.')
        if self.instance.pk and not self.instance._state.adding and self.instance.tickets.exists() and any(name in self.changed_data for name in ['paper_start','paper_end']):
            self.add_error('paper_start', 'A faixa não pode mudar depois da emissão de números. Isso evita bilhetes duplicados.')
        if data.get('motorcycle_count') is not None and not 1 <= data['motorcycle_count'] <= 10:
            self.add_error('motorcycle_count', 'Informe entre 1 e 10 motos.')
        quantity=data.get('quota_quantity')
        if quantity and quantity>100000:self.add_error('quota_quantity','O limite online desta edição deve ser de até 100.000 números.')
        if quantity and not self.instance._state.adding:
            from apps.catalog.models import CapacityResource
            resource=CapacityResource.objects.filter(event_id=self.instance.event_id,type='RAFFLE_TOTAL',reference_id=str(self.instance.pk)).first()
            if resource and quantity<resource.reserved_count+resource.committed_count:
                self.add_error('quota_quantity','O limite não pode ficar abaixo dos números já pagos ou reservados.')
        if data.get('online_sales_enabled'):
            required=['quota_price','quota_quantity','draw_at','rules_text','authorization_reference','provider_review_reference','operation_review_confirmed','is_public']
            if any(not data.get(name) for name in required):
                self.add_error('online_sales_enabled','Complete preço, limite, data, regulamento, referências e a conferência antes de ativar.')
            from django.utils import timezone
            if data.get('draw_at') and data['draw_at']<=timezone.now():self.add_error('draw_at','A data do sorteio precisa estar no futuro para abrir vendas.')
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
