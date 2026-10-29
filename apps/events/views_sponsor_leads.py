import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db import transaction,IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required,can_manage_event,accessible_events
from apps.core.utils import log_audit
from apps.finance.forms import parse_amount_cents
from .models import SponsorLead,SponsorProfile,SponsorHistory,Event
from .models_sponsors import sponsor_name_key
from .sponsorships import available_profiles,can_edit_profile
from .sponsor_leads import convert_lead


class LeadForm(forms.ModelForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=0,initial=0)
    previous_amount=forms.CharField(label='Valor pago na edição anterior (R$)',required=False,max_length=30,
        help_text='Só histórico: não entra no acordo ou na receita atual.',widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    class Meta:
        model=SponsorLead
        fields=['name','contact_name','phone','address','notes','follow_up_at','status','previous_edition','previous_amount','previous_notes']
        widgets={'notes':forms.Textarea(attrs={'rows':3}),'previous_notes':forms.Textarea(attrs={'rows':2}),
            'follow_up_at':forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'),
            'phone':forms.TextInput(attrs={'inputmode':'tel','data-mask':'phone'})}

    def __init__(self,*args,event,can_finance=False,**kwargs):
        super().__init__(*args,**kwargs);self.event=event
        self.fields['status'].choices=[(key,label) for key,label in SponsorLead.Status.choices if key!='CONFIRMED']
        if self.instance.sponsor_id:
            self.fields['status'].choices=[('CONFIRMED','Confirmado')];self.fields['status'].disabled=True
        self.initial['revision']=self.instance.revision
        from apps.core.utils import cents_to_reais
        self.initial['previous_amount']=cents_to_reais(self.instance.previous_amount_cents) if self.instance.previous_amount_cents is not None else ''
        if not can_finance:
            self.fields.pop('previous_amount')
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')

    def clean_name(self):
        value=self.cleaned_data['name'].strip()
        if SponsorLead.objects.filter(event=self.event,name_key=sponsor_name_key(value)).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Este interessado já está cadastrado no evento. Edite o contato existente.')
        return value

    def clean_previous_amount(self):
        raw=self.cleaned_data['previous_amount'].strip()
        return None if not raw else 0 if raw in ('0','0,00','0.00') else parse_amount_cents(raw)

    def clean(self):
        data=super().clean()
        if data.get('previous_amount') is not None and not data.get('previous_edition'):
            self.add_error('previous_edition','Informe a edição a que esse valor pertence.')
        return data

    def save(self,commit=True):
        if 'previous_amount' in self.cleaned_data:
            self.instance.previous_amount_cents=self.cleaned_data['previous_amount']
        return super().save(commit=commit)

    def clean_revision(self):
        value=self.cleaned_data['revision']
        if self.instance.pk and value!=self.instance.revision:raise forms.ValidationError('Este contato mudou em outra aba. Atualize antes de salvar.')
        return value


@management_required
@never_cache
@require_http_methods(['GET'])
def sponsor_leads(request,event_id):
    event=request.management_event;query=request.GET.get('q','').strip()[:150];state=request.GET.get('status','')
    leads=SponsorLead.objects.filter(event=event).select_related('sponsor')
    if query:leads=leads.filter(Q(name__icontains=query)|Q(contact_name__icontains=query)|Q(phone__icontains=query))
    if state in SponsorLead.Status.values:leads=leads.filter(status=state)
    return render(request,'gestao/sponsors/leads.html',{'event':event,'page_obj':Paginator(leads,30).get_page(request.GET.get('page')),
        'q':query,'status':state,'statuses':SponsorLead.Status.choices,'can_content':can_manage_event(request.user,event,'content'),
        'can_finance':can_manage_event(request.user,event,'finance')})


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_lead_edit(request,event_id,lead_id=None):
    event=request.management_event
    with transaction.atomic():
        Event.objects.select_for_update().get(pk=event.pk)
        lead=get_object_or_404(SponsorLead.objects.select_for_update(),pk=lead_id,event=event) if lead_id else SponsorLead(event=event)
        form=LeadForm(request.POST if request.method=='POST' else None,instance=lead,event=event,
            can_finance=can_manage_event(request.user,event,'finance'))
        if request.method=='POST' and form.is_valid():
            lead=form.save(commit=False);lead.revision+=1;lead.save()
            log_audit(actor=request.user,event_id=event.pk,action='SPONSOR_LEAD_SAVED',entity_type='SponsorLead',entity_id=lead.pk,
                diff={'fields':form.changed_data,'status':lead.status},request=request)
            messages.success(request,'Contato salvo. Nenhum recebimento foi registrado.')
            return redirect('gestao:sponsor_leads',event_id=event.pk)
    return render(request,'gestao/sponsors/lead_form.html',{'event':event,'lead':lead,'form':form},status=400 if form.errors else 200)


class ConvertForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput,min_value=0)
    profile=forms.ModelChoiceField(label='Marca já cadastrada (opcional)',queryset=SponsorProfile.objects.none(),required=False,
        empty_label='Localizar pelo nome ou criar a marca',widget=forms.Select(attrs={'class':'form-control'}))
    confirmed=forms.BooleanField(label='Este interessado confirmou o patrocínio. O recebimento será registrado separadamente.')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_lead_convert(request,event_id,lead_id):
    event=request.management_event;lead=get_object_or_404(SponsorLead,pk=lead_id,event=event)
    form=ConvertForm(request.POST if request.method=='POST' else None,initial={'revision':lead.revision})
    form.fields['profile'].queryset=available_profiles(request.user,'content').filter(organization=event.organization)
    if request.method=='POST' and form.is_valid():
        try:
            sponsor,created=convert_lead(event=event,lead_id=lead.pk,version=form.cleaned_data['revision'],actor=request.user,
                profile_id=form.cleaned_data['profile'].pk if form.cleaned_data['profile'] else None,request=request)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'Patrocínio confirmado. Defina o acordo, a logo e a exibição; dê baixa somente quando receber o dinheiro.')
            return redirect('gestao:sponsor_detail',event_id=event.pk,sponsor_id=sponsor.pk)
    return render(request,'gestao/sponsors/lead_form.html',{'event':event,'lead':lead,'form':form,'converting':True},status=400 if form.errors else 200)


class HistoryForm(forms.Form):
    edition=forms.CharField(label='Edição ou ano anterior',max_length=150)
    amount=forms.CharField(label='Valor pago naquela edição (R$)',required=False,max_length=30,widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    notes=forms.CharField(label='Observações da edição anterior',required=False,max_length=2000,widget=forms.Textarea(attrs={'rows':3}))
    request_key=forms.UUIDField(widget=forms.HiddenInput)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial['request_key']=uuid.uuid4()
        for field in self.fields.values():field.widget.attrs['class']='form-control'
    def clean_amount(self):
        raw=self.cleaned_data['amount'].strip()
        return None if not raw else 0 if raw in ('0','0,00','0.00') else parse_amount_cents(raw)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def sponsor_history(request,event_id,profile_id):
    event=request.management_event
    profile=get_object_or_404(available_profiles(request.user),pk=profile_id,organization=event.organization)
    can_edit=can_edit_profile(request.user,profile)
    form=HistoryForm(request.POST if request.method=='POST' else None)
    if request.method=='POST':
        if not can_edit:raise PermissionDenied('O histórico compartilhado exige acesso ao conteúdo dos eventos vinculados.')
        if form.is_valid():
            data=form.cleaned_data
            with transaction.atomic():
                SponsorProfile.objects.select_for_update().get(pk=profile.pk)
                previous=SponsorHistory.objects.filter(request_key=data['request_key']).first()
                if previous and (previous.profile_id!=profile.pk or previous.edition!=data['edition'] or previous.amount_cents!=data['amount'] or previous.notes!=data['notes']):
                    form.add_error(None,'Este formulário já foi usado com outros dados. Reabra a página.')
                elif not previous:
                    row=SponsorHistory.objects.create(profile=profile,edition=data['edition'],amount_cents=data['amount'],notes=data['notes'],request_key=data['request_key'],created_by=request.user)
                    log_audit(actor=request.user,event_id=event.pk,action='SPONSOR_PAST_EDITION_RECORDED',entity_type='SponsorHistory',entity_id=row.pk,
                        diff={'historical_only':True},request=request)
            if not form.errors:
                messages.success(request,'Histórico salvo. Esse valor não entra na arrecadação atual.')
                return redirect('gestao:sponsor_history',event_id=event.pk,profile_id=profile.pk)
    return render(request,'gestao/sponsors/history.html',{'event':event,'profile':profile,'form':form,'can_edit':can_edit,
        'history':profile.previous_editions.all(),'participations':profile.participations.filter(event__in=accessible_events(request.user,'finance')).exclude(event=event)},status=400 if form.errors else 200)
