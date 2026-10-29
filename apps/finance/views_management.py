"""Permission-scoped expense corrections and private supporting documents."""
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required
from .models import Expense, ExpenseStatus
from .forms import ExpenseEditForm, ExpensePaymentForm, ExpenseProofForm, expense_version
from .services import update_expense, settle_expense, attach_expense_receipt, void_paid_expense
from django.urls import reverse
from urllib.parse import urlsplit


def finance_return_url(request, event):
    default = reverse('gestao:finance', args=[event.pk])
    candidate = request.POST.get('return_to', '')
    parsed = urlsplit(candidate)
    return candidate if not parsed.netloc and not parsed.scheme and parsed.path == default else default


@management_required
@never_cache
@require_http_methods(['POST'])
def expense_status(request, event_id, expense_id):
    from .forms import ExpenseStatusForm
    from apps.core.views_gestao import _finance_context
    event = request.management_event
    expense = get_object_or_404(Expense, event=event, pk=expense_id)
    form = ExpenseStatusForm(request.POST, request.FILES)
    if form.is_valid():
        data = dict(form.cleaned_data, description=expense.description, category=expense.category,
            amount=expense.amount_cents, due_at=expense.due_at, vendor_id=expense.vendor, raffle=expense.raffle)
        data['paid_at'] = data['paid_at'] if data['status']=='PAID' else None
        data['reason'] = data['reason'] or 'Correção de status pelo painel financeiro.'
        try:
            update_expense(event=event,expense_id=expense.pk,data=data,actor=request.user,request=request)
        except (ValidationError,OSError) as error:
            form.add_error(None,error if isinstance(error,ValidationError) else 'Não foi possível salvar o comprovante. Nenhuma alteração foi concluída.')
        else:
            messages.success(request,'Situação salva. Saldo e totais atualizados; nenhum dinheiro foi enviado ou estornado.')
            return redirect(finance_return_url(request,event))
    context = _finance_context(request,event)
    target=finance_return_url(request,event)
    context.update(status_form=form,status_expense=expense,management_workspace_url=target)
    response=render(request,'gestao/finance/overview.html',context,status=400)
    response['HX-Replace-Url']=target
    return response


class VoidExpenseForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo da anulação', min_length=10, max_length=1000,
                             widget=forms.Textarea(attrs={'class':'form-control','rows':3}))
    confirmed = forms.BooleanField(label='Confirmo a anulação deste lançamento incorreto. O histórico e o comprovante serão preservados.')


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def expense_editor(request, event_id, expense_id, operation='edit'):
    event = request.management_event
    expense = get_object_or_404(Expense, event=event, pk=expense_id)
    definitions = {
        'edit': (ExpenseEditForm, 'Editar despesa', 'Salvar correção'),
        'pay': (ExpensePaymentForm, 'Registrar pagamento', 'Confirmar saída'),
        'proof': (ExpenseProofForm, 'Anexar comprovante', 'Salvar comprovante'),
        'void': (VoidExpenseForm, 'Anular lançamento pago', 'Confirmar anulação'),
    }
    form_class, title, submit = definitions[operation]
    kwargs = {'expense':expense} if operation=='edit' else {}
    if operation=='void': kwargs['initial']={'version':expense_version(expense)}
    form = form_class(request.POST if request.method=='POST' else None,
                      request.FILES if request.method=='POST' else None, **kwargs)
    if request.method=='POST' and form.is_valid():
        try:
            common = dict(event=event,expense_id=expense.pk,actor=request.user,request=request)
            if operation=='edit': update_expense(**common,data=form.cleaned_data)
            elif operation=='pay': settle_expense(**common,paid_at=form.cleaned_data['paid_at'],proof=form.cleaned_data.get('proof_asset'))
            elif operation=='proof': attach_expense_receipt(**common,proof=form.cleaned_data['proof_asset'])
            else: void_paid_expense(**common,**form.cleaned_data)
        except ValidationError as error:
            form.add_error(None,error)
        except OSError:
            form.add_error(None,'Não foi possível salvar o comprovante. Tente novamente; nenhum lançamento foi confirmado nesta operação.')
        else:
            messages.success(request,'Operação concluída. O extrato e o histórico foram atualizados.')
            return redirect('gestao:finance',event_id=event.pk)
    return render(request,'gestao/finance/expense_editor.html',{
        'event':event,'expense':expense,'form':form,'operation':operation,'title':title,'submit_label':submit,
    },status=400 if request.method=='POST' else 200)
