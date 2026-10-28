from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required
from apps.core.utils import cents_to_reais
from .models import Registration
from .forms_management import RegistrationEditForm,RegistrationRefundForm
from .management import edit_registration,cancel_paid_order,refundable_amount


def _get_reg(event,reg_id):
    return get_object_or_404(Registration.objects.select_related('person','source_order_line__order'),event=event,pk=reg_id)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def registration_edit(request,event_id,reg_id):
    event=request.management_event;reg=_get_reg(event,reg_id)
    form=RegistrationEditForm(request.POST if request.method=='POST' else None,registration=reg)
    if request.method=='POST' and form.is_valid():
        try:edit_registration(event=event,reg_id=reg.pk,data=form.cleaned_data,actor=request.user,request=request)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'Cadastro corrigido. O CPF, a conta de acesso e o pagamento original foram preservados.')
            return redirect('gestao:registration_detail',event_id=event.pk,reg_id=reg.pk)
    return render(request,'gestao/registrations/editor.html',{'event':event,'reg':reg,'form':form,'title':'Editar inscrição'},status=400 if request.method=='POST' else 200)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def registration_refund(request,event_id,reg_id):
    event=request.management_event;reg=_get_reg(event,reg_id);order=reg.source_order_line.order
    return _refund_form(request,event,reg,order)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def raffle_order_refund(request,event_id,order_id):
    from apps.checkout.models import Order
    event=request.management_event
    order=get_object_or_404(Order.objects.filter(event=event,lines__raffle__isnull=False).distinct(),pk=order_id)
    reg=get_object_or_404(Registration.objects.select_related('person','source_order_line__order'),event=event,
        pk=order.lines.filter(raffle__isnull=False).values_list('linked_registration_id',flat=True).first())
    return _refund_form(request,event,reg,order,raffle_refund=True)


def _refund_form(request,event,reg,order,raffle_refund=False):
    amount=refundable_amount(order)
    form=RegistrationRefundForm(request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None,
        initial={'amount':cents_to_reais(amount)})
    if raffle_refund:
        form.fields['confirmed'].label='Confirmo que o valor integral JÁ FOI DEVOLVIDO ao comprador e que os números extras deste pedido serão cancelados.'
    if request.method=='POST' and form.is_valid():
        try:refund,created=cancel_paid_order(event=event,order_id=order.pk,data=form.cleaned_data,actor=request.user,request=request)
        except ValidationError as error:form.add_error(None,error)
        except OSError:form.add_error(None,'Não foi possível salvar o comprovante. Tente novamente; a operação não foi concluída.')
        else:
            messages.success(request,('Números extras cancelados. Inscrição e QR originais preservados.' if raffle_refund else 'Inscrições canceladas.')+' Devolução registrada no financeiro; nenhum reembolso foi enviado ao Mercado Pago por esta ação.' if created else 'Esta devolução já estava registrada. Nenhum valor foi duplicado.')
            return redirect('gestao:raffle_finance' if raffle_refund else 'gestao:registration_list',event_id=event.pk)
    return render(request,'gestao/registrations/editor.html',{'event':event,'reg':reg,'order':order,'form':form,
        'refunding':True,'raffle_refund':raffle_refund,'refund_amount':amount,'title':'Cancelar e registrar devolução',
        'affected_regs':Registration.objects.filter(source_order_line__order=order).select_related('person')},status=400 if request.method=='POST' else 200)
