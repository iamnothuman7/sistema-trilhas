from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.core.permissions import management_required
from apps.core.utils import cents_to_reais
from .models import Registration
from .forms_management import RegistrationEditForm,RegistrationRefundForm,LegacyRefundChannelForm
from .refund_channels import confirm_provider_return,provider_refund_evidence,classify_legacy_return
from apps.payments.gateway import GatewayError
from apps.payments.models import Refund,RefundState,RefundChannel,ChargeAttempt
from apps.payments.services import reconcile_payment,PaymentValidationError
from .management import edit_registration,cancel_paid_order,refundable_amount,refundable_extra_review


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


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def addon_order_refund(request,event_id,order_id):
    from apps.checkout.models import Order
    from django.http import Http404
    event=request.management_event
    order=get_object_or_404(Order.objects.filter(event=event,lines__addon__isnull=False).distinct(),pk=order_id)
    if order.lines.filter(addon__isnull=True).exists():
        raise Http404('Este pedido não é uma compra separada de produtos extras.')
    reg=get_object_or_404(Registration.objects.select_related('person','source_order_line__order'),event=event,
        pk=order.lines.values_list('linked_registration_id',flat=True).first())
    return _refund_form(request,event,reg,order,addon_refund=True)


def _refund_form(request,event,reg,order,raffle_refund=False,addon_refund=False):
    from django.urls import reverse
    amount=refundable_amount(order)
    extra_review=(raffle_refund or addon_refund) and order.status=='PAID_NEEDS_REVIEW'
    review_refund_allowed=refundable_extra_review(order) if extra_review else False
    form=RegistrationRefundForm(request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None,
        initial={'amount':cents_to_reais(amount)})
    if raffle_refund:
        form.fields['confirmed'].label='Confirmo que o valor integral JÁ FOI DEVOLVIDO ao comprador e que os números extras deste pedido serão cancelados.'
    if addon_refund:
        form.fields['confirmed'].label='Confirmo que o saldo integral JÁ FOI DEVOLVIDO ao comprador e que os produtos extras deste pedido serão cancelados. A inscrição e seu QR serão preservados.'
    if request.method=='POST' and form.is_valid():
        provider_channel=form.cleaned_data['channel']==RefundChannel.MERCADO_PAGO
        try:
            if provider_channel:
                refund=confirm_provider_return(event=event,order_id=order.pk,data=form.cleaned_data,actor=request.user,request=request)
                created=True
            else:refund,created=cancel_paid_order(event=event,order_id=order.pk,data=form.cleaned_data,actor=request.user,request=request)
        except ValidationError as error:form.add_error(None,error)
        except GatewayError as error:form.add_error(None,'Não foi possível conferir o estorno no Mercado Pago. '+str(error))
        except PaymentValidationError:form.add_error(None,'O pagamento retornado não corresponde a este pedido, conta ou ambiente. Nenhuma devolução manual foi criada.')
        except OSError:form.add_error(None,'Não foi possível salvar o comprovante. Tente novamente; a operação não foi concluída.')
        else:
            result=('Produtos extras cancelados. Inscrição e QR originais preservados.' if addon_refund else 'Números extras cancelados. Inscrição e QR originais preservados.' if raffle_refund else 'Inscrições canceladas.')
            messages.success(request,'Estorno confirmado pela consulta ao Mercado Pago e conciliado. Nenhuma devolução manual adicional foi criada e nenhum dinheiro foi enviado por esta ação.' if provider_channel else result+' Devolução externa registrada no financeiro; nenhum reembolso foi enviado ao Mercado Pago por esta ação.' if created else 'Esta devolução já estava registrada. Nenhum valor foi duplicado.')
            return redirect('gestao:finance' if addon_refund else 'gestao:raffle_finance' if raffle_refund else 'gestao:registration_list',event_id=event.pk)
    return render(request,'gestao/registrations/editor.html',{'event':event,'reg':reg,'order':order,'form':form,
        'refunding':True,'raffle_refund':raffle_refund,'addon_refund':addon_refund,'refund_amount':amount,
        'return_url':reverse('gestao:finance',args=[event.pk]) if addon_refund else '',
        'extra_items':order.lines.all() if addon_refund else [],
        'unknown_refunds':order.refunds.filter(channel=RefundChannel.UNKNOWN,state=RefundState.MANUAL_COMPLETED),
        'extra_review':extra_review,'review_refund_allowed':review_refund_allowed,
        'review_refund_blocked':extra_review and not review_refund_allowed,
        'title':'Devolver produtos extras' if addon_refund else 'Cancelar e registrar devolução',
        'affected_regs':Registration.objects.filter(source_order_line__order=order).select_related('person')},status=400 if request.method=='POST' else 200)


@management_required
@never_cache
@require_http_methods(['GET','POST'])
def refund_channel_review(request,event_id,refund_id):
    event=request.management_event
    refund=get_object_or_404(Refund.objects.select_related('order'),pk=refund_id,order__event=event)
    form=LegacyRefundChannelForm(request.POST if request.method=='POST' else None)
    if request.method=='POST':
        if request.POST.get('action')=='refresh':
            try:
                charge,payment,returned=provider_refund_evidence(event=event,order_id=refund.order_id)
                reconcile_payment(payment,charge_id=charge.pk)
            except (GatewayError,ValidationError,PaymentValidationError):
                form.add_error(None,'Não foi possível concluir a consulta e conciliação. Confira o canal e a conta original; nenhum lançamento manual foi criado.')
            else:
                messages.info(request,'Consulta concluída. Devoluções com canal desconhecido continuam em conferência; os demais valores foram conciliados pela resposta autenticada.')
                return redirect('gestao:finance',event_id=event.pk)
        elif form.is_valid():
            try:
                channel=form.cleaned_data['channel']
                evidence=provider_refund_evidence(event=event,order_id=refund.order_id) if channel==RefundChannel.MERCADO_PAGO else None
                saved,changed=classify_legacy_return(event=event,refund_id=refund.pk,actor=request.user,request=request,
                    channel=channel,reason=form.cleaned_data['reason'],confirmed=form.cleaned_data['confirmed'],evidence=evidence)
            except (ValidationError,GatewayError) as error:form.add_error(None,error if isinstance(error,ValidationError) else str(error))
            except PaymentValidationError:form.add_error(None,'A resposta do Mercado Pago não corresponde ao registro original. O histórico foi preservado.')
            else:
                if channel=='TEST_NOTE':
                    messages.info(request,'Anotação de teste salva. Canal, valores, situação, financeiro e Mercado Pago não foram alterados. Qualquer ajuste financeiro exige uma conferência separada.')
                else:
                    messages.success(request,'Canal conferido com registro de auditoria. Nenhum lançamento existente foi apagado e nenhum dinheiro foi enviado.')
                    if evidence or ChargeAttempt.objects.filter(order=refund.order,error_code='REFUND_CHANNEL_REVIEW').exists():
                        try:
                            charge,payment,returned=evidence or provider_refund_evidence(event=event,order_id=refund.order_id)
                            reconcile_payment(payment,charge_id=charge.pk)
                        except (GatewayError,ValidationError,PaymentValidationError):
                            messages.warning(request,'O canal foi salvo, mas a nova consulta ainda precisa ser concluída. Use Consultar novamente para continuar a conciliação.')
                return redirect('gestao:finance',event_id=event.pk)
    return render(request,'gestao/registrations/refund_channel_review.html',{'event':event,'refund':refund,'form':form,
        'can_classify':refund.channel==RefundChannel.UNKNOWN and refund.state==RefundState.MANUAL_COMPLETED},status=400 if request.method=='POST' else 200)
