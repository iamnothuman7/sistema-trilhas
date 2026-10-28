"""Audited corrections and explicit recording of an already completed refund."""
from datetime import datetime,time
import hashlib,json
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from apps.checkout.models import Order,OrderStatus,ReceivableDue
from apps.identity.models import Person,UserPersonLink
from apps.payments.models import Refund,RefundState
from apps.payments.services import _release_capacity,_suspend_registrations
from apps.finance.models import FinancialEntry,FinancialEntryKind
from apps.finance.receipts import save_receipt,with_receipt_cleanup,receipt_digest
from apps.core.utils import log_audit
from .models import Registration,BenefitType


def registration_version(reg):
    values={field:str(getattr(reg,field) or '') for field in ['person_id','updated_at','commercial_state','shirt_size','team_name','vehicle_type','archived_at']}
    values.update({field:str(getattr(reg.person,field) or '') for field in ['full_name','email','phone','city','state','updated_at']})
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


@transaction.atomic
def edit_registration(*,event,reg_id,data,actor,request=None):
    reg=Registration.objects.select_for_update().select_related('person','source_order_line__variant').get(pk=reg_id,event=event)
    if data['version']!=registration_version(reg):raise ValidationError('Esta inscrição mudou em outra aba. Atualize a página.')
    if reg.archived_at or reg.commercial_state in ['CANCELED','TRANSFERRED']:raise ValidationError('Não é possível editar uma inscrição removida, cancelada ou transferida.')
    if len(data.get('reason','').strip())<10:raise ValidationError('Descreva o motivo da correção.')
    if data['shirt_size']!=reg.shirt_size:
        if reg.entitlements.filter(benefit_type=BenefitType.CAMISETA,quantity_used__gt=0).exists():raise ValidationError('A camiseta já foi entregue. O tamanho não pode ser alterado.')
        from apps.catalog.models import ProductVariant
        original=reg.source_order_line.variant
        target=ProductVariant.objects.filter(product_id=reg.source_order_line.product_id,kind='SHIRT_SIZE',code=data['shirt_size']).first()
        if not target or target.extra_price_cents!=(original.extra_price_cents if original else 0):
            raise ValidationError('Escolha um tamanho cadastrado com o mesmo valor. Acréscimos exigem uma compra adicional.')
    person=Person.objects.select_for_update().get(pk=reg.person_id)
    person_fields=['full_name','email','phone','city','state']
    changed=any((getattr(person,key) or '')!=(data[key] or '') for key in person_fields)
    cloned=False
    if changed and person.registrations.exclude(pk=reg.pk).exists():
        links=list(person.user_links.values('user_id','method','verified_at'))
        old=person
        person=Person.objects.create(**{field.name:getattr(old,field.name) for field in Person._meta.fields if field.name not in ['id','created_at','updated_at']})
        UserPersonLink.objects.bulk_create([UserPersonLink(person=person,**link) for link in links])
        reg.person=person;cloned=True
    if changed:
        for key in person_fields:setattr(person,key,data[key])
        person.save(update_fields=[*person_fields,'updated_at'])
    previous={key:getattr(reg,key) for key in ['team_name','vehicle_type','shirt_size']}
    for key in previous:setattr(reg,key,data[key])
    reg.save(update_fields=['person','team_name','vehicle_type','shirt_size','updated_at'])
    if previous['shirt_size']!=reg.shirt_size:
        reg.entitlements.filter(benefit_type=BenefitType.CAMISETA,quantity_used=0).update(title=f'Camiseta Oficial — {reg.shirt_size}')
    log_audit(actor=actor,event_id=event.pk,action='REGISTRATION_EDITED',entity_type='Registration',entity_id=reg.pk,
        reason=data['reason'],diff={'fields':[key for key in person_fields if changed]+list(previous),'person_scoped_to_event':cloned},request=request)
    return reg


def refundable_amount(order):
    returned=order.refunds.filter(state__in=[RefundState.SUCCEEDED,RefundState.MANUAL_COMPLETED]).aggregate(n=Sum('amount_cents'))['n'] or 0
    return max(0,order.total_cents-returned)


@with_receipt_cleanup
@transaction.atomic
def cancel_paid_order(*,event,order_id,data,actor,request=None,_saved_receipts):
    order=Order.objects.select_for_update().get(pk=order_id,event=event)
    key=f"MANUAL-CANCEL-{data['request_key']}"
    existing=Refund.objects.filter(request_key=key).first()
    if existing:
        same=(existing.order_id==order.pk and existing.amount_cents==data['amount'] and existing.reason_notes==data['reason']
              and existing.provider_ref==data['reference'] and timezone.localtime(existing.completed_at).date()==data['returned_on'])
        if not same:raise ValidationError('Este formulário já foi usado com outros dados. Reabra a operação.')
        save_receipt(existing,data.get('proof_asset'),_saved_receipts)
        return existing,False
    if order.status not in [OrderStatus.PAID,OrderStatus.PARTIALLY_REFUNDED]:raise ValidationError('O pedido não está pago ou já foi devolvido. Confira a situação atual.')
    amount=refundable_amount(order)
    base_regs=Registration.objects.filter(source_order_line__order=order)
    if Order.objects.filter(lines__raffle__isnull=False,lines__linked_registration_id__in=base_regs.values('pk'),status='PAID').exists():
        raise ValidationError('Há números extras pagos vinculados a esta inscrição. Registre primeiro a devolução desses pedidos no financeiro do sorteio.')
    if not data.get('confirmed') or not amount or data['amount']!=amount:raise ValidationError('Confirme a devolução integral do saldo exibido, já realizada ao comprador.')
    if data['returned_on']>timezone.localdate() or not data['reference'].strip() or len(data['reason'].strip())<10:raise ValidationError('Revise a data, a referência e o motivo da devolução.')
    completed=timezone.make_aware(datetime.combine(data['returned_on'],time(hour=12)))
    refund=Refund.objects.create(order=order,request_key=key,amount_cents=amount,state=RefundState.MANUAL_COMPLETED,
        provider_ref=data['reference'],reason_code='ADMIN_CANCELLATION',reason_notes=data['reason'],approved_by=actor,completed_at=completed)
    save_receipt(refund,data.get('proof_asset'),_saved_receipts)
    raffle_id=order.lines.filter(raffle__isnull=False).values_list('raffle_id',flat=True).first()
    FinancialEntry.objects.create(event=event,order=order,raffle_id=raffle_id,kind=FinancialEntryKind.ESTORNO_SORTEIO if raffle_id else FinancialEntryKind.REEMBOLSO,amount_cents_signed=-amount,
        recognized_at=completed,posted_by=actor,source_reference=f'MANUAL-REFUND-{refund.pk}',description=f'Devolução já realizada — {order.merchant_reference}')
    order.status=OrderStatus.REFUNDED;order.save(update_fields=['status','updated_at'])
    _suspend_registrations(order,canceled=True)
    _release_capacity(order,include_committed=True)
    Registration.objects.filter(source_order_line__order=order).update(archived_at=timezone.now(),updated_at=timezone.now())
    ReceivableDue.objects.filter(order=order).update(status='CANCELED',allocated_paid_cents=0)
    log_audit(actor=actor,event_id=event.pk,action='PAID_ORDER_CANCELED_REFUND_RECORDED',entity_type='Order',entity_id=order.pk,
        reason=data['reason'],diff={'amount_cents':amount,'reference':data['reference'],'refund_id':str(refund.pk),
        'receipt_sha256':receipt_digest(data.get('proof_asset'))},request=request)
    return refund,True
