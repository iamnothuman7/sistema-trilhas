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
