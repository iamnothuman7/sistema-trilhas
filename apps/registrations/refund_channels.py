"""Explicit return channels. Provider reads never send money or run inside our atomic blocks."""
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from apps.checkout.models import Order
from apps.core.permissions import can_manage_event
from apps.core.utils import log_audit,cents_to_reais
from apps.finance.models import FinancialEntry,FinancialEntryKind
from apps.finance.receipts import save_receipt,with_receipt_cleanup
from apps.payments.gateway_config import charge_gateway
from apps.payments.models import ChargeAttempt,PaymentTransaction,Refund,RefundState,RefundChannel
from apps.payments.services import _validate,money_cents,reconcile_payment,PaymentValidationError


def provider_refund_evidence(*,event,order_id):
    """Only GET an existing, uniquely identifiable payment in its original account."""
    charges=list(ChargeAttempt.objects.filter(order_id=order_id,order__event=event,provider='mercadopago',
        transactions__posted_revenue_cents__gt=0).distinct().select_related('order'))
    if len(charges)!=1 or not charges[0].provider_payment_ref:
        raise ValidationError('Não há um único pagamento Mercado Pago identificado. Confira as transações individualmente com a administração financeira; nenhum estorno foi presumido.')
    charge=charges[0];gateway=charge_gateway(charge)
    payment=gateway.get_payment(charge.provider_payment_ref)
    _,amount,_=_validate(payment,charge,gateway)
    returned=money_cents(payment.get('transaction_amount_refunded',0))
    if payment.get('status') in ['refunded','charged_back']:returned=amount
    if returned<=0 or returned>amount or payment.get('status') not in ['approved','refunded','charged_back']:
        raise ValidationError('O Mercado Pago ainda não confirmou uma devolução desse pagamento. Nenhum lançamento manual foi criado. Aguarde a atualização ou confira a conta recebedora original.')
    return charge,payment,returned


@with_receipt_cleanup
@transaction.atomic
def _attach_provider_receipt(*,refund_id,proof,actor,event,_saved_receipts):
    refund=Refund.objects.select_for_update().get(pk=refund_id,order__event=event,channel=RefundChannel.MERCADO_PAGO)
    if save_receipt(refund,proof,_saved_receipts):
        log_audit(actor=actor,event_id=event.pk,action='PROVIDER_REFUND_RECEIPT_ATTACHED',entity_type='Refund',entity_id=refund.pk)


def confirm_provider_return(*,event,order_id,data,actor,request=None):
    if not can_manage_event(actor,event,'finance'):raise PermissionDenied
    charge,payment,returned=provider_refund_evidence(event=event,order_id=order_id)
    reconcile_payment(payment,charge_id=charge.pk)
    charge.refresh_from_db()
    if charge.error_code=='REFUND_CHANNEL_REVIEW':
        raise ValidationError('O Mercado Pago confirmou o estorno, mas há devolução antiga sem canal conferido. Use Conferir canal no financeiro para identificar se é o mesmo movimento. Não foi lançado outro valor e a credencial foi bloqueada.')
    if returned<data['amount']:
        raise ValidationError(f'O Mercado Pago confirmou R$ {cents_to_reais(returned)}, abaixo do valor informado. O financeiro foi atualizado somente pelo valor autenticado; nenhum complemento manual foi criado.')
    refund=Refund.objects.filter(order_id=order_id,channel=RefundChannel.MERCADO_PAGO,state=RefundState.SUCCEEDED,
        provider_ref=str(payment['id'])).order_by('-created_at').first()
    if not refund:raise ValidationError('A confirmação foi consultada. Confira o histórico deste pedido antes de adicionar qualquer registro.')
    if data.get('proof_asset'):
        try:_attach_provider_receipt(refund_id=refund.pk,proof=data['proof_asset'],actor=actor,event=event)
        except (OSError,ValidationError):
            raise ValidationError('O estorno do Mercado Pago já foi confirmado e conciliado, mas o comprovante não foi salvo. Preserve o arquivo e confira o histórico; não faça outra devolução.') from None
    log_audit(actor=actor,event_id=event.pk,action='PROVIDER_REFUND_CONFIRMED_BY_OPERATOR',entity_type='Refund',entity_id=refund.pk,
        reason=data['reason'],diff={'provider_payment_id':str(payment['id']),'returned_cents':returned},request=request)
    return refund


@transaction.atomic
def classify_legacy_return(*,event,refund_id,channel,reason,confirmed,actor,evidence=None,request=None):
    if not can_manage_event(actor,event,'finance'):raise PermissionDenied
    if not confirmed or not 10<=len(reason.strip())<=1000 or channel not in ['EXTERNAL','MERCADO_PAGO','TEST_NOTE']:
        raise ValidationError('Selecione a conclusão, descreva a conferência e confirme os comprovantes.')
    initial=Refund.objects.get(pk=refund_id,order__event=event)
    order=Order.objects.select_for_update().get(pk=initial.order_id,event=event)
    refund=Refund.objects.select_for_update().get(pk=initial.pk)
    if refund.channel!=RefundChannel.UNKNOWN or refund.state!=RefundState.MANUAL_COMPLETED:
        if channel==refund.channel:return refund,False
        raise ValidationError('O canal deste registro já foi conferido. Não reclassifique um histórico financeiro conciliado.')
    previous_reference=refund.provider_ref
    if channel=='TEST_NOTE':
        note='[Conferência: registro de teste, sem devolução real] '+reason.strip()
        if note in (refund.reason_notes or ''):return refund,False
        refund.reason_notes=((refund.reason_notes or '')+'\n'+note).strip()
        refund.save(update_fields=['reason_notes'])
        action='LEGACY_REFUND_TEST_NOTED'
    elif channel==RefundChannel.EXTERNAL:
        refund.channel=RefundChannel.EXTERNAL;refund.save(update_fields=['channel'])
        action='LEGACY_REFUND_EXTERNAL_CONFIRMED'
    else:
        if not evidence:raise ValidationError('Consulte o estorno autenticado antes de vincular o registro antigo.')
        original_charge,payment,_=evidence
        charge=ChargeAttempt.objects.select_for_update().select_related('order').get(pk=original_charge.pk,order=order)
        payment_id,_,_=_validate(payment,charge,charge_gateway(charge))
        returned=money_cents(payment.get('transaction_amount_refunded',0))
        if payment.get('status') in ['refunded','charged_back']:returned=charge.amount_cents
        if payment.get('status') not in ['approved','refunded','charged_back'] or not 0<returned<=charge.amount_cents:
            raise ValidationError('O Mercado Pago não confirmou um estorno válido para esse vínculo.')
        snapshot=PaymentTransaction.objects.select_for_update().filter(provider_payment_id=payment_id,charge_attempt=charge).first()
        if (not snapshot or snapshot.posted_revenue_cents!=charge.amount_cents or
                returned-snapshot.posted_refund_cents<refund.amount_cents):
            raise ValidationError('O estorno autenticado não tem saldo suficiente para vincular este registro. Confira o valor e possíveis vínculos anteriores.')
        entries=FinancialEntry.objects.filter(order=order,source_reference=f'MANUAL-REFUND-{refund.pk}',
            kind__in=[FinancialEntryKind.REEMBOLSO,FinancialEntryKind.ESTORNO_SORTEIO])
        if entries.count()!=1 or entries.first().amount_cents_signed!=-refund.amount_cents:
            raise ValidationError('O lançamento original precisa de conferência financeira antes de vincular o estorno.')
        # This negative entry already exists. Credit it to the provider snapshot,
        # never append it again when the webhook is replayed.
        snapshot.posted_refund_cents+=refund.amount_cents;snapshot.save(update_fields=['posted_refund_cents','updated_at'])
        refund.channel=RefundChannel.MERCADO_PAGO;refund.state=RefundState.SUCCEEDED
        refund.charge_attempt=charge;refund.provider_ref=payment_id
        refund.save(update_fields=['channel','state','charge_attempt','provider_ref'])
        action='LEGACY_REFUND_MATCHED_PROVIDER'
    log_audit(actor=actor,event_id=event.pk,action=action,entity_type='Refund',entity_id=refund.pk,reason=reason,
        diff={'channel':refund.channel,'previous_reference':previous_reference,'provider_reference':refund.provider_ref,
            'amount_cents':refund.amount_cents,'ledger_changed':False},request=request)
    return refund,True
