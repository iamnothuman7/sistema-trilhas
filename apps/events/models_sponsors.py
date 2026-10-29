from django.db import models
from apps.core.utils import cents_to_reais
import unicodedata


def sponsor_name_key(value):
    value=unicodedata.normalize('NFKD',value or '')
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).casefold().split())


class SponsorLead(models.Model):
    class Status(models.TextChoices):
        NEW='NEW','A contatar'
        SCHEDULED='SCHEDULED','Visita agendada'
        NEGOTIATING='NEGOTIATING','Em negociação'
        CONFIRMED='CONFIRMED','Confirmado'
        UNCONFIRMED='UNCONFIRMED','Não confirmou'
        DECLINED='DECLINED','Sem interesse'
    event=models.ForeignKey('events.Event',on_delete=models.PROTECT,related_name='sponsor_leads')
    name=models.CharField('Empresa ou pessoa',max_length=150)
    name_key=models.CharField(max_length=180,editable=False)
    contact_name=models.CharField('Responsável',max_length=150,blank=True)
    phone=models.CharField('Telefone / WhatsApp',max_length=30,blank=True)
    address=models.CharField('Localização / endereço para visita',max_length=300,blank=True)
    notes=models.TextField('Observações',blank=True)
    previous_edition=models.CharField('Já patrocinou? Edição ou ano anterior',max_length=150,blank=True)
    previous_amount_cents=models.PositiveBigIntegerField('Valor pago na edição anterior',null=True,blank=True)
    previous_notes=models.TextField('Observações do patrocínio anterior',blank=True)
    follow_up_at=models.DateTimeField('Próxima visita ou retorno',null=True,blank=True)
    status=models.CharField('Situação do contato',max_length=20,choices=Status.choices,default=Status.NEW)
    sponsor=models.ForeignKey('events.Sponsor',on_delete=models.PROTECT,null=True,blank=True,related_name='leads')
    revision=models.PositiveIntegerField(default=0,editable=False)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['event','name_key'],name='unique_sponsor_lead_per_event')]
        ordering=['name','pk']

    def save(self,*args,**kwargs):
        self.name_key=sponsor_name_key(self.name)
        super().save(*args,**kwargs)


class SponsorHistory(models.Model):
    profile=models.ForeignKey('events.SponsorProfile',on_delete=models.PROTECT,related_name='previous_editions')
    edition=models.CharField('Edição ou ano anterior',max_length=150)
    amount_cents=models.PositiveBigIntegerField('Valor pago naquela edição (centavos)',null=True,blank=True)
    notes=models.TextField('Observações da edição anterior',blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    created_by=models.ForeignKey('identity.User',on_delete=models.SET_NULL,null=True)
    request_key=models.UUIDField(unique=True)
    class Meta:
        ordering=['-created_at']
