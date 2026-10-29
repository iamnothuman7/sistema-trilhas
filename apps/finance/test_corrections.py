"""Explicit correction of identified test refund notes, never provider money."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.checkout.models import Order,OrderStatus
from apps.core.utils import log_audit
from apps.events.models import Event
from apps.payments.models import Refund,RefundState,RefundChannel,ChargeAttempt
from apps.payments.services import _validate,money_cents,_release_capacity,_suspend_registrations
from apps.payments.gateway_config import charge_gateway
from apps.registrations.models import Registration
from .models import FinancialEntry,FinancialEntryKind


@transaction.atomic
def correct_identified_test_refund(*,event,refund_id,charge_id,authenticated_payment,actor,reason):
    initial=Refund.objects.get(pk=refund_id,order__event=event)
    order=Order.objects.select_for_update().get(pk=initial.order_id)
    Event.objects.select_for_update().get(pk=event.pk)
    refund=Refund.objects.select_for_update().get(pk=refund_id)
    if refund.state==RefundState.VOIDED_TEST:return False
    charge=ChargeAttempt.objects.select_for_update().select_related('order').get(pk=charge_id,order=order)
    _validate(authenticated_payment,charge,charge_gateway(charge))
    if (authenticated_payment.get('status')!='approved' or money_cents(authenticated_payment.get('transaction_amount_refunded',0)) or
        refund.state!=RefundState.MANUAL_COMPLETED or refund.channel!=RefundChannel.UNKNOWN or len(reason.strip())<10):
        raise ValidationError('A correção exige anotação manual sem canal e pagamento autenticado aprovado, sem devolução.')
    entries=FinancialEntry.objects.filter(order=order,source_reference=f'MANUAL-REFUND-{refund.pk}',kind=FinancialEntryKind.REEMBOLSO)
    if entries.count()!=1 or entries.first().amount_cents_signed!=-refund.amount_cents:
        raise ValidationError('O lançamento original exige conferência; nenhuma correção foi aplicada.')
    original=entries.get()
    FinancialEntry.objects.create(event=event,order=order,kind=original.kind,amount_cents_signed=refund.amount_cents,
        recognized_at=original.recognized_at,source_reference=f'VOID-TEST-REFUND-{refund.pk}',posted_by=actor,
        description='Correção de anotação fictícia de devolução; recebimento real preservado')
    refund.state=RefundState.VOIDED_TEST
    refund.reason_notes=((refund.reason_notes or '')+'\nCorreção: '+reason).strip()
    refund.save(update_fields=['state','reason_notes'])
    order.is_test=True;order.status=OrderStatus.PAID_NEEDS_REVIEW
    order.save(update_fields=['is_test','status','updated_at'])
    _suspend_registrations(order,canceled=True)
    _release_capacity(order,include_committed=True)
    Registration.objects.filter(source_order_line__order=order).update(archived_at=timezone.now())
    log_audit(actor=actor,event_id=event.pk,action='TEST_REFUND_NOTE_CORRECTED',entity_type='Refund',entity_id=refund.pk,
        reason=reason,diff={'order_id':str(order.pk),'correction_cents':refund.amount_cents,'provider_money_preserved':True})
    return True
