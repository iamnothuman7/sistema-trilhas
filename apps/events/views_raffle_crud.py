from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required,can_manage_event
from apps.core.record_actions import RecordConfirmationForm,record_version
from apps.core.utils import log_audit,cents_to_reais
from apps.finance.views_crud import form_page,style
from apps.finance.forms import parse_amount_cents
from apps.finance.models import FinancialEntry,FinancialEntryKind
from .models import Event,MotorcycleRaffle,RaffleSalesPoint,RaffleTicket


class PointForm(forms.ModelForm):
    version=forms.CharField(widget=forms.HiddenInput)
    class Meta:
        model=RaffleSalesPoint
        fields=['name','contact','active']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial['version']=record_version(self.instance);style(self)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def raffle_point_editor(request,event_id,point_id,remove=False):
    event=request.management_event
    with transaction.atomic():
        Event.objects.select_for_update().get(pk=event.pk)
        row=get_object_or_404(RaffleSalesPoint.objects.select_for_update(),pk=point_id,raffle__event=event)
        version=record_version(row)
        form=RecordConfirmationForm(request.POST if request.method=='POST' else None,initial={'version':version}) if remove else PointForm(request.POST if request.method=='POST' else None,instance=row)
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['version']!=version:form.add_error(None,'O ponto mudou. Atualize a página.')
            else:
                if remove:row.active=False;row.save(update_fields=['active'])
                else:form.save()
                log_audit(actor=request.user,event_id=event.pk,action='RAFFLE_POINT_REMOVED' if remove else 'RAFFLE_POINT_EDITED',entity_type='RaffleSalesPoint',entity_id=row.pk,reason=form.cleaned_data.get('reason',''),request=request)
                messages.success(request,'Ponto atualizado. Distribuições e recebimentos anteriores preservados.')
                return redirect('gestao:raffle_operations',event_id=event.pk)
    return form_page(request,event=event,form=form,record=row,title='Excluir ponto de venda' if remove else 'Editar ponto de venda',
        note='Excluir desativa novas distribuições. Bilhetes e valores já atribuídos permanecem neste ponto. Para restaurar, edite o ponto e marque Ativo.',
        back=reverse('gestao:raffle_operations',args=[event.pk]),submit='Confirmar exclusão' if remove else 'Salvar ponto',danger=remove,section='Sorteio')


class TicketForm(forms.ModelForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo da correção',min_length=10,max_length=500,widget=forms.Textarea(attrs={'rows':2}))
    amount=forms.CharField(label='Valor já recebido (R$)',required=False,widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    class Meta:
        model=RaffleTicket
        fields=['buyer_name','buyer_phone','point']
        widgets={'buyer_phone':forms.TextInput(attrs={'inputmode':'tel','data-mask':'phone'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial.update(version=record_version(self.instance),amount=cents_to_reais(self.instance.amount_cents))
        self.fields['point'].queryset=RaffleSalesPoint.objects.filter(raffle=self.instance.raffle).filter(Q(active=True)|Q(pk=self.instance.point_id))
        if self.instance.state!='SOLD':self.fields.pop('amount')
        else:self.fields['amount'].required=True;self.fields['buyer_name'].required=True
        style(self)
    def clean_amount(self):return parse_amount_cents(self.cleaned_data['amount'])


class TicketRemoveForm(RecordConfirmationForm):
    returned=forms.BooleanField(label='Se houve pagamento, confirmo que o dinheiro já foi devolvido fora desta tela.',required=False)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def raffle_ticket_editor(request,event_id,ticket_id,remove=False):
    event=request.management_event
    destination='gestao:raffle_operations' if can_manage_event(request.user,event,'events') else 'gestao:raffle_finance'
    with transaction.atomic():
        Event.objects.select_for_update().get(pk=event.pk)
        raffle=get_object_or_404(MotorcycleRaffle.objects.select_for_update(),event=event)
        row=get_object_or_404(RaffleTicket.objects.select_for_update(),raffle=raffle,pk=ticket_id,source='PAPER')
        version=record_version(row);before={'name':row.buyer_name,'phone':row.buyer_phone,'point':str(row.point_id),'amount':row.amount_cents}
        form=TicketRemoveForm(request.POST if request.method=='POST' else None,initial={'version':version}) if remove else TicketForm(request.POST if request.method=='POST' else None,instance=row)
        if request.method=='POST' and form.is_valid():
            try:
                if hasattr(raffle,'result'):raise ValidationError('O sorteio já foi apurado. Os números do resultado e seu histórico estão preservados.')
                if form.cleaned_data['version']!=version:raise ValidationError('Este bilhete mudou. Atualize a página.')
                if row.state=='VOID':raise ValidationError('Este número foi cancelado e não será reutilizado.')
                amount=form.cleaned_data.get('amount',row.amount_cents)
                if row.amount_cents and (remove or amount!=row.amount_cents) and not can_manage_event(request.user,event,'finance'):
                    raise PermissionDenied('A correção de valores recebidos exige acesso financeiro.')
                if remove:
                    if row.amount_cents and not form.cleaned_data['returned']:raise ValidationError('Registre a exclusão de um bilhete pago somente após devolver o valor ao comprador.')
                    if row.amount_cents:FinancialEntry.objects.create(event=event,raffle=raffle,kind=FinancialEntryKind.ESTORNO_SORTEIO,amount_cents_signed=-row.amount_cents,source_reference=f'RAFFLE-{row.pk}-VOID',posted_by=request.user,description=f'Devolução do bilhete {row.number}')
                    row.state='VOID';row.save(update_fields=['state'])
                else:
                    if amount!=before['amount']:
                        if not row.paid_at:raise ValidationError('Confira o recebimento original antes de corrigir o valor.')
                        FinancialEntry.objects.create(event=event,raffle=raffle,kind=FinancialEntryKind.RECEITA_SORTEIO,amount_cents_signed=amount-before['amount'],recognized_at=row.paid_at,
                            source_reference=f'RAFFLE-{row.pk}-EDIT-{version[:16]}',posted_by=request.user,description=f'Correção do valor do bilhete {row.number}')
                    row.amount_cents=amount;form.save()
                log_audit(actor=request.user,event_id=event.pk,action='RAFFLE_TICKET_VOIDED' if remove else 'RAFFLE_TICKET_EDITED',entity_type='RaffleTicket',entity_id=row.pk,reason=form.cleaned_data['reason'],diff={'before':before},request=request)
            except ValidationError as error:form.add_error(None,error)
            else:
                messages.success(request,'Bilhete cancelado. O número não será reutilizado.' if remove else 'Bilhete e financeiro atualizados.')
                return redirect(destination,event_id=event.pk)
    return form_page(request,event=event,form=form,record=row,title=f'Excluir bilhete {row.number}' if remove else f'Editar bilhete {row.number}',
        note='O número é fixo. Edite o comprador, o contato e o ponto. A correção do valor recebido atualiza o financeiro principal e o do sorteio. Cancelar um bilhete pago exige devolução já realizada; nenhuma transferência é executada aqui.',
        back=reverse(destination,args=[event.pk]),submit='Confirmar exclusão' if remove else 'Salvar bilhete',danger=remove,section='Sorteio')
