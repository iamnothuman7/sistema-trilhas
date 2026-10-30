"""Explicit removal and correction of user-managed event and sponsor records."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required,accessible_events,can_manage_event
from apps.core.record_actions import RecordConfirmationForm,record_version
from apps.core.utils import log_audit
from apps.finance.views_crud import form_page
from .models import Event,Sponsor,SponsorProfile,SponsorHistory
from .sponsorships import available_profiles,can_edit_profile
from .views_sponsor_leads import HistoryForm
from .forms_sponsorships import SponsorReceiptForm


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def event_action(request,event_id,restore=False):
    with transaction.atomic():
        row=get_object_or_404(Event.objects.select_for_update(),pk=request.management_event.pk)
        form=RecordConfirmationForm(request.POST if request.method=='POST' else None,initial={'version':record_version(row)})
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['version']!=record_version(row):form.add_error(None,'Este evento mudou. Atualize antes de continuar.')
            elif restore and row.status!='ARCHIVED':form.add_error(None,'Este evento já está ativo.')
            else:
                row.status='DRAFT' if restore else 'ARCHIVED';row.save(update_fields=['status','updated_at'])
                log_audit(actor=request.user,event_id=row.pk,action='EVENT_RESTORED' if restore else 'EVENT_ARCHIVED',entity_type='Event',entity_id=row.pk,reason=form.cleaned_data['reason'],request=request)
                messages.success(request,'Evento restaurado em rascunho.' if restore else 'Evento excluído da publicação. Dados anteriores preservados como Arquivado.')
                return redirect('gestao:event_list')
    return form_page(request,event=row,form=form,record=row,title='Restaurar evento' if restore else 'Excluir evento',
        note='A exclusão arquiva a edição e fecha novas inscrições. Inscrições, documentos, recebimentos e comprovantes continuam preservados. Restaurar volta como rascunho, sem reabrir vendas automaticamente.',
        back=reverse('gestao:event_list'),submit='Restaurar em rascunho' if restore else 'Confirmar exclusão',danger=not restore,section='Eventos')


def require_sponsor_removal(user,sponsors):
    for sponsor in sponsors:
        if not can_manage_event(user,sponsor.event,'content'):raise PermissionDenied('Sem acesso ao conteúdo dos eventos vinculados.')
        if (sponsor.agreed_amount_cents or sponsor.payments.exists()) and not can_manage_event(user,sponsor.event,'finance'):
            raise PermissionDenied('Um patrocínio com acordo ou recebimentos exige também acesso financeiro para excluir ou restaurar.')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_action(request,event_id,sponsor_id,restore=False):
    event=request.management_event
    with transaction.atomic():
        row=get_object_or_404(Sponsor.objects.select_for_update().select_related('event','profile'),pk=sponsor_id,event=event)
        require_sponsor_removal(request.user,[row])
        form=RecordConfirmationForm(request.POST if request.method=='POST' else None,initial={'version':record_version(row)})
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['version']!=record_version(row):form.add_error(None,'O patrocínio mudou. Atualize a página.')
            elif restore and row.profile and row.profile.archived_at:form.add_error(None,'Restaure primeiro a marca no banco de patrocinadores.')
            else:
                row.archived_at=None if restore else timezone.now();row.is_public=False;row.finance_version+=1
                row.save(update_fields=['archived_at','is_public','finance_version'])
                log_audit(actor=request.user,event_id=event.pk,action='SPONSOR_RESTORED' if restore else 'SPONSOR_ARCHIVED',entity_type='Sponsor',entity_id=row.pk,reason=form.cleaned_data['reason'],request=request)
                messages.success(request,'Patrocínio restaurado, ainda oculto no site.' if restore else 'Patrocínio excluído da lista ativa e do site. Recebimentos anteriores preservados.')
                return redirect('gestao:sponsor_list',event_id=event.pk)
    return form_page(request,event=event,form=form,record=row,title='Restaurar patrocínio' if restore else 'Excluir patrocínio deste evento',
        note='Retira a participação da lista ativa, da cobrança pendente e do site. Não exclui a marca de outros eventos, não apaga recebimentos e não devolve dinheiro. Restaurar não publica a logo automaticamente.',
        back=reverse('gestao:sponsor_list',args=[event.pk]),submit='Restaurar' if restore else 'Confirmar exclusão',danger=not restore,section='Patrocinadores')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_profile_action(request,profile_id,restore=False):
    with transaction.atomic():
        row=get_object_or_404(available_profiles(request.user,'content',include_archived=True).select_for_update(),pk=profile_id)
        if not can_edit_profile(request.user,row):raise PermissionDenied('Sem acesso a todos os eventos da marca.')
        sponsors=list(row.participations.select_for_update().select_related('event'))
        require_sponsor_removal(request.user,sponsors)
        event=accessible_events(request.user,'content').filter(organization=row.organization).order_by('pk').first()
        import hashlib
        version=hashlib.sha256((record_version(row)+''.join(record_version(s) for s in sorted(sponsors,key=lambda s:s.pk))).encode()).hexdigest()
        form=RecordConfirmationForm(request.POST if request.method=='POST' else None,initial={'version':version})
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['version']!=version:form.add_error(None,'A marca ou suas participações mudaram. Atualize a página.')
            else:
                row.archived_at=None if restore else timezone.now();row.save(update_fields=['archived_at','updated_at'])
                if not restore:
                    for sponsor in sponsors:
                        sponsor.archived_at=row.archived_at;sponsor.is_public=False;sponsor.finance_version+=1
                        sponsor.save(update_fields=['archived_at','is_public','finance_version'])
                        log_audit(actor=request.user,event_id=sponsor.event_id,action='SPONSOR_ARCHIVED',entity_type='Sponsor',entity_id=sponsor.pk,reason=form.cleaned_data['reason'],request=request)
                log_audit(actor=request.user,event_id=event.pk,action='SPONSOR_PROFILE_RESTORED' if restore else 'SPONSOR_PROFILE_ARCHIVED',entity_type='SponsorProfile',entity_id=row.pk,reason=form.cleaned_data['reason'],request=request)
                messages.success(request,'Marca restaurada. Restaure separadamente as participações que deseja retomar.' if restore else 'Marca e participações excluídas das listas ativas. Histórico e recebimentos preservados.')
                return redirect(reverse('gestao:sponsor_bank')+'?event_id=all&archived=1')
    return form_page(request,event=event,form=form,record=row,title='Restaurar marca' if restore else 'Excluir marca do banco',
        note=f'Esta marca tem {len(sponsors)} participação(ões). Excluir a marca também retira sua exibição e suas participações ativas em todos esses eventos. Valores já recebidos permanecem no financeiro.',
        back=reverse('gestao:sponsor_bank')+'?event_id=all',submit='Restaurar marca' if restore else 'Excluir marca e participações',danger=not restore,section='Patrocinadores')


class HistoryEditForm(HistoryForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo da correção',min_length=5,max_length=500,widget=forms.Textarea(attrs={'class':'form-control','rows':2}))
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs);self.fields.pop('request_key')
        from apps.core.utils import cents_to_reais
        self.initial.update(edition=record.edition,amount='' if record.amount_cents is None else cents_to_reais(record.amount_cents),notes=record.notes,version=record_version(record))


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_history_editor(request,event_id,history_id,remove=False):
    event=request.management_event
    with transaction.atomic():
        row=get_object_or_404(SponsorHistory.objects.select_for_update().select_related('profile'),pk=history_id,profile__organization=event.organization)
        if not can_edit_profile(request.user,row.profile):raise PermissionDenied('Sem acesso aos eventos desta marca.')
        form=RecordConfirmationForm(request.POST if request.method=='POST' else None,initial={'version':record_version(row)}) if remove else HistoryEditForm(request.POST if request.method=='POST' else None,record=row)
        back=reverse('gestao:sponsor_history',args=[event.pk,row.profile_id])
        if request.method=='POST' and form.is_valid():
            if form.cleaned_data['version']!=record_version(row):form.add_error(None,'Este histórico mudou. Atualize a página.')
            else:
                pk=row.pk;before={'edition':row.edition,'amount_cents':row.amount_cents,'notes':row.notes}
                if remove:row.delete()
                else:
                    row.edition=form.cleaned_data['edition'];row.amount_cents=form.cleaned_data['amount'];row.notes=form.cleaned_data['notes'];row.save()
                log_audit(actor=request.user,event_id=event.pk,action='SPONSOR_HISTORY_REMOVED' if remove else 'SPONSOR_HISTORY_EDITED',entity_type='SponsorHistory',entity_id=pk,diff={'before':before},reason=form.cleaned_data['reason'],request=request)
                messages.success(request,'Histórico atualizado. O financeiro atual não foi alterado.')
                return redirect(back)
    return form_page(request,event=event,form=form,record=row,title='Excluir histórico anterior' if remove else 'Editar histórico anterior',note='Referência de uma edição antiga. Este registro não é um recebimento no financeiro atual.',back=back,submit='Confirmar exclusão' if remove else 'Salvar correção',danger=remove,section='Patrocinadores')


class SponsorPaymentEditForm(SponsorReceiptForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo da correção',min_length=5,max_length=500,widget=forms.Textarea(attrs={'class':'form-control','rows':2}))
    def __init__(self,*args,payment,**kwargs):
        super().__init__(*args,**kwargs)
        from apps.core.utils import cents_to_reais
        self.initial.update(version=record_version(payment),amount=cents_to_reais(payment.amount_cents),received_on=payment.received_on.isoformat(),method=payment.method,reference=payment.reference,notes=payment.notes)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_payment_editor(request,event_id,sponsor_id,payment_id):
    from apps.finance.models import SponsorPayment
    from apps.finance.sponsorships import edit_sponsor_payment
    event=request.management_event
    payment=get_object_or_404(SponsorPayment,pk=payment_id,sponsorship_id=sponsor_id,sponsorship__event=event)
    form=SponsorPaymentEditForm(request.POST if request.method=='POST' else None,payment=payment)
    if request.method=='POST' and form.is_valid():
        try:edit_sponsor_payment(event=event,sponsor_id=sponsor_id,payment_id=payment.pk,data=form.cleaned_data,actor=request.user,request=request)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'Recebimento corrigido no patrocínio e no financeiro. O lançamento anterior permanece identificado como substituído.')
            return redirect('gestao:sponsor_detail',event_id=event.pk,sponsor_id=sponsor_id)
    return form_page(request,event=event,form=form,record=payment,title='Editar recebimento do patrocínio',
        note='Corrige valor, data, forma e referência do dinheiro recebido. O registro anterior é compensado e substituído, sem fazer transferência bancária.',
        back=reverse('gestao:sponsor_detail',args=[event.pk,sponsor_id]),submit='Salvar correção',section='Patrocinadores')
