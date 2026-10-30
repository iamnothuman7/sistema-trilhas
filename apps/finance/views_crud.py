"""Finance workspace CRUD; corrections never rewrite gateway transactions."""
import uuid
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required, accessible_events
from apps.core.utils import log_audit
from apps.events.models import Event
from apps.events.models_sponsors import sponsor_name_key
from .forms import parse_amount_cents, expense_version
from .models import ManualMovement, SponsorPaymentMethod, Vendor, Expense, ExpenseStatus
from .movements import save_movement, archive_movement
from .receipts import validate_receipt, RECEIPT_ACCEPT, RECEIPT_HELP
from .services import void_paid_expense, cancel_pending_expense


def style(form):
    for field in form.fields.values():
        if not isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault('class', 'form-control')


class MovementForm(forms.Form):
    kind = forms.ChoiceField(label='Tipo', choices=ManualMovement.Kind.choices)
    description = forms.CharField(label='Descrição', max_length=200)
    amount_cents = forms.CharField(label='Valor (R$)', max_length=30, widget=forms.TextInput(attrs={'inputmode':'decimal', 'placeholder':'0,00'}))
    occurred_on = forms.DateField(label='Data da movimentação', widget=forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d'))
    method = forms.ChoiceField(label='Forma de recebimento / transferência', choices=SponsorPaymentMethod.choices)
    reference = forms.CharField(label='Referência / contas de origem e destino', max_length=150, required=False,
        help_text='Nas transferências, informe de qual conta saiu e para qual conta foi.')
    notes = forms.CharField(label='Observações', max_length=2000, required=False, widget=forms.Textarea(attrs={'rows':3}))
    proof_asset = forms.FileField(label='Comprovante', required=False, validators=[validate_receipt],
        widget=forms.FileInput(attrs={'accept':RECEIPT_ACCEPT}), help_text=RECEIPT_HELP)
    reason = forms.CharField(label='Motivo da correção', min_length=5, max_length=1000)
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    request_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, movement=None, **kwargs):
        super().__init__(*args, **kwargs)
        if movement:
            for field in ('kind','description','occurred_on','method','reference','notes','revision'):
                self.initial[field] = getattr(movement, field)
            self.initial['amount_cents'] = movement.formatted_amount
            self.fields.pop('request_key')
            if movement.proof_asset:
                self.fields.pop('proof_asset')
        else:
            self.fields.pop('reason'); self.fields.pop('revision')
            self.initial.update(request_key=uuid.uuid4(), occurred_on=timezone.localdate(), method='PIX', kind='INCOME')
        self.fields['occurred_on'].widget.attrs['max'] = timezone.localdate().isoformat()
        style(self)

    def clean_amount_cents(self):
        return parse_amount_cents(self.cleaned_data['amount_cents'])


class RecordActionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo', min_length=5, max_length=1000, widget=forms.Textarea(attrs={'rows':3}))
    confirmed = forms.BooleanField(label='Conferi o registro e confirmo esta ação.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); style(self)


def form_page(request, *, event, form, title, note, back, record=None, submit='Salvar', danger=False, proof_kind=None, section='Financeiro'):
    return render(request, 'gestao/finance/record_form.html', dict(event=event, form=form, title=title, note=note,
        back_url=back, record=record, submit_label=submit, danger=danger, proof_kind=proof_kind,section=section), status=400 if form.errors else 200)


@management_required
@never_cache
@require_http_methods(['GET'])
def movement_list(request, event_id):
    event = request.management_event
    archived = request.GET.get('archived') == '1'
    query = request.GET.get('q','').strip()[:150]
    rows = ManualMovement.objects.filter(event=event, archived_at__isnull=not archived)
    if query:
        rows = rows.filter(Q(description__icontains=query) | Q(reference__icontains=query))
    return render(request, 'gestao/finance/movements.html', dict(event=event, archived=archived, q=query,
        page_obj=Paginator(rows, 20).get_page(request.GET.get('page'))))


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def movement_editor(request, event_id, movement_id=None, operation='edit'):
    event = request.management_event
    row = get_object_or_404(ManualMovement, event=event, pk=movement_id) if movement_id else None
    action = operation in ('remove','restore')
    form = RecordActionForm(request.POST if request.method=='POST' else None, initial={'revision':row.revision}) if action else MovementForm(
        request.POST if request.method=='POST' else None, request.FILES or None, movement=row)
    title = {'remove':'Excluir movimentação', 'restore':'Restaurar movimentação'}.get(operation, 'Editar movimentação' if row else 'Nova entrada / transferência')
    back = reverse('gestao:movement_list', args=[event.pk])
    if row and row.archived_at: back += '?archived=1'
    if request.method == 'POST' and form.is_valid():
        try:
            if action:
                archive_movement(event=event, movement_id=row.pk, revision=form.cleaned_data['revision'],
                    actor=request.user, reason=form.cleaned_data['reason'], restore=operation=='restore', request=request)
            else:
                save_movement(event=event, movement_id=row.pk if row else None, data=form.cleaned_data, actor=request.user, request=request)
        except (ValidationError, OSError) as error:
            form.add_error(None, error if isinstance(error, ValidationError) else 'Não foi possível salvar o comprovante. Nenhuma alteração foi concluída.')
        else:
            messages.success(request, 'Movimentação salva. O extrato e os totais foram atualizados; nenhum dinheiro foi enviado ou devolvido.')
            return redirect(back)
    note = ('Excluir retira o valor dos totais e mantém o histórico em Excluídas. Restaurar volta a contabilizar o valor original.' if action else
        'Registre valores já recebidos fora de pedidos e patrocínios. Transferências entre contas próprias não são receita nem despesa e não mudam o resultado. Saídas operacionais ficam em Despesas.')
    return form_page(request,event=event,form=form,title=title,note=note,back=back,record=row,
        submit='Confirmar exclusão' if operation=='remove' else 'Restaurar' if operation=='restore' else 'Salvar movimentação',
        danger=operation=='remove',proof_kind='movimentacao')


class VendorForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    class Meta:
        model = Vendor
        fields = ['name','category','contact_name','contact_phone','contact_email','pix_key']
        widgets = {'contact_phone':forms.TextInput(attrs={'inputmode':'tel','data-mask':'phone'})}

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs); self.organization=organization
        self.initial['revision'] = self.instance.revision
        style(self)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        for existing in Vendor.objects.filter(organization=self.organization).exclude(pk=self.instance.pk).only('name'):
            if sponsor_name_key(existing.name) == sponsor_name_key(name):
                raise forms.ValidationError('Este fornecedor já existe. Confira os ativos e os excluídos antes de cadastrar novamente.')
        return name

    def clean_revision(self):
        value=self.cleaned_data['revision']
        if value!=self.instance.revision:
            raise forms.ValidationError('O fornecedor mudou em outra aba. Atualize antes de salvar.')
        return value


def can_edit_vendor(user, event):
    return not Event.objects.filter(organization=event.organization).exclude(pk__in=accessible_events(user,'finance')).exists()


@management_required
@never_cache
@require_http_methods(['GET'])
def vendor_list(request, event_id):
    event=request.management_event; archived=request.GET.get('archived')=='1'
    return render(request,'gestao/finance/vendors.html',dict(event=event,archived=archived,can_edit=can_edit_vendor(request.user,event),
        page_obj=Paginator(Vendor.objects.filter(organization=event.organization,archived_at__isnull=not archived).order_by('name','pk'),30).get_page(request.GET.get('page'))))


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def vendor_editor(request, event_id, vendor_id=None, operation='edit'):
    from apps.core.models import Organization
    event=request.management_event
    if not can_edit_vendor(request.user,event):
        raise PermissionDenied('Alterar fornecedores compartilhados exige acesso financeiro a todos os eventos desta organização.')
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=event.organization_id)
        row=get_object_or_404(Vendor.objects.select_for_update(),pk=vendor_id,organization=event.organization) if vendor_id else Vendor(organization=event.organization)
        action=operation in ('remove','restore')
        form=RecordActionForm(request.POST if request.method=='POST' else None,initial={'revision':row.revision}) if action else VendorForm(
            request.POST if request.method=='POST' else None,instance=row,organization=event.organization)
        if request.method=='POST' and form.is_valid():
            if action and form.cleaned_data['revision']!=row.revision:
                form.add_error(None,'Este fornecedor mudou. Atualize a página.')
            elif not action and row.archived_at:
                form.add_error(None,'Restaure o fornecedor antes de editar.')
            else:
                if action: row.archived_at=None if operation=='restore' else timezone.now()
                else: row=form.save(commit=False)
                row.revision+=1;row.save()
                log_audit(actor=request.user,event_id=event.pk,action='VENDOR_'+operation.upper(),entity_type='Vendor',entity_id=row.pk,
                    diff={'revision':row.revision,'archived':bool(row.archived_at)},reason=form.cleaned_data.get('reason',''),request=request)
                messages.success(request,'Fornecedor atualizado. As despesas e seus comprovantes foram preservados.')
                return redirect('gestao:vendor_list',event_id=event.pk)
    title={'remove':'Excluir fornecedor','restore':'Restaurar fornecedor'}.get(operation,'Editar fornecedor' if vendor_id else 'Novo fornecedor')
    return form_page(request,event=event,form=form,title=title,note='Cadastro compartilhado entre os eventos da organização. Excluir remove das opções de novos lançamentos e preserva as despesas anteriores.',
        back=reverse('gestao:vendor_list',args=[event.pk]),record=row,submit='Confirmar' if action else 'Salvar fornecedor',danger=operation=='remove')


class ExpenseActionForm(RecordActionForm):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo',min_length=10,max_length=500,widget=forms.Textarea(attrs={'rows':3}))
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields.pop('revision')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def expense_action(request,event_id,expense_id,operation='remove'):
    event=request.management_event
    with transaction.atomic():
        row=get_object_or_404(Expense.objects.select_for_update(),event=event,pk=expense_id)
        form=ExpenseActionForm(request.POST if request.method=='POST' else None,initial={'version':expense_version(row)})
        if request.method=='POST' and form.is_valid():
            try:
                if form.cleaned_data['version']!=expense_version(row):raise ValidationError('A despesa mudou. Atualize a página.')
                if operation=='restore':
                    if row.status!=ExpenseStatus.CANCELED:raise ValidationError('Esta despesa já está ativa.')
                    row.status=ExpenseStatus.PENDING;row.paid_at=None;row.revision+=1
                    row.save(update_fields=['status','paid_at','revision'])
                    log_audit(actor=request.user,event_id=event.pk,action='EXPENSE_RESTORED',entity_type='Expense',entity_id=row.pk,
                        reason=form.cleaned_data['reason'],request=request)
                elif row.status==ExpenseStatus.PAID:
                    void_paid_expense(event=event,expense_id=row.pk,actor=request.user,request=request,**form.cleaned_data)
                else:
                    cancel_pending_expense(event=event,expense_id=row.pk,actor=request.user,reason=form.cleaned_data['reason'],request=request)
            except ValidationError as error:form.add_error(None,error)
            else:
                messages.success(request,'Despesa restaurada como A pagar.' if operation=='restore' else 'Despesa excluída dos totais ativos. Histórico preservado em Canceladas; nenhum estorno bancário foi realizado.')
                return redirect('gestao:finance',event_id=event.pk)
    return form_page(request,event=event,form=form,title='Restaurar despesa' if operation=='restore' else 'Excluir despesa',record=row,
        note='A exclusão cancela a despesa e compensa sua saída no extrato, se paga. Não devolve dinheiro. Restaurar reabre como A pagar; confirme a baixa somente se o pagamento foi realizado.',
        back=reverse('gestao:finance',args=[event.pk]),submit='Restaurar como A pagar' if operation=='restore' else 'Confirmar exclusão',danger=operation=='remove',proof_kind='despesa')
