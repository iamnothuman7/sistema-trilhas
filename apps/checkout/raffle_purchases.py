"""Extra draw entries reuse the normal payment pipeline and the participant's QR."""
import hashlib,json,secrets
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.catalog.models import CapacityResource,CapacityHold
from apps.events.models import Event,MotorcycleRaffle
from apps.registrations.models import Registration
from .addons import eligible_registrations
from .models import Order,OrderLine,ReceivableDue,CheckoutSubmission
from apps.core.utils import log_audit


def available_numbers(raffle):
    resource=CapacityResource.objects.filter(event=raffle.event,type='RAFFLE_TOTAL',reference_id=str(raffle.pk)).first()
    return max(0,(raffle.quota_quantity or 0)-(resource.reserved_count+resource.committed_count if resource else 0))


@transaction.atomic
def reserve_raffle_numbers(*,registration_id,user,quantity,request_key,rules_accepted=False,request=None):
    if not user.is_authenticated or not user.is_active:raise ValidationError('Entre na sua conta para continuar.')
    if not isinstance(quantity,int) or isinstance(quantity,bool) or not 1<=quantity<=20:raise ValidationError('Escolha de 1 a 20 números extras por compra.')
    if not rules_accepted:raise ValidationError('Aceite o regulamento específico do sorteio antes de continuar.')
    digest=hashlib.sha256(json.dumps({'registration':str(registration_id),'quantity':quantity},sort_keys=True).encode()).hexdigest()
    previous=CheckoutSubmission.objects.select_related('order').filter(client_idempotency_key=request_key).first()
    if previous:
        if previous.request_hash!=digest or previous.order.purchaser_user_id!=user.pk:raise ValidationError('Este formulário já foi usado com outros dados.')
        return previous.order
    initial=eligible_registrations(user).filter(pk=registration_id).first()
    if not initial:raise ValidationError('Números extras exigem uma inscrição ativa e paga desta edição.')
    Event.objects.select_for_update().get(pk=initial.event_id)
    reg=Registration.objects.select_for_update(of=('self',)).select_related('person','event').filter(
        pk=registration_id,pk__in=eligible_registrations(user).values('pk')).first()
    if not reg:raise ValidationError('A inscrição não está mais disponível para esta compra.')
    # First-save configuration and concurrent reservations share the event lock.
    previous=CheckoutSubmission.objects.select_related('order').filter(client_idempotency_key=request_key).first()
    if previous:
        if previous.request_hash!=digest or previous.order.purchaser_user_id!=user.pk:raise ValidationError('Atualize o formulário antes de continuar.')
        return previous.order
    raffle=MotorcycleRaffle.objects.filter(event=reg.event).first()
    if not raffle or not raffle.purchase_available:raise ValidationError('A venda de números extras ainda não está aberta para este evento.')
    resource,_=CapacityResource.objects.select_for_update().get_or_create(event=reg.event,type='RAFFLE_TOTAL',reference_id=str(raffle.pk),defaults={'capacity_total':raffle.quota_quantity})
    resource.capacity_total=raffle.quota_quantity
    if resource.available_count<quantity:raise ValidationError(f'Quantidade indisponível. Restam {resource.available_count} números extras.')
    now=timezone.now();total=raffle.quota_price_cents*quantity
    order=Order.objects.create(event=reg.event,purchaser_user=user,purchaser_name=user.full_name or reg.person.full_name,
        purchaser_email=user.email,purchaser_phone=user.phone or reg.person.phone or '',purchaser_document=reg.participant_cpf or reg.person.document_number,
        merchant_reference='SO-'+secrets.token_hex(6).upper(),gross_cents=total,total_cents=total,
        expires_at=min(now+timedelta(minutes=35),raffle.draw_at))
    line=OrderLine.objects.create(order=order,raffle=raffle,linked_registration_id=reg.pk,quantity=quantity,unit_cents=raffle.quota_price_cents,
        net_cents=total,intended_person_name=reg.person.full_name,item_snapshot_json={'kind':'RAFFLE','product_name':f'Números extras — {raffle.title}',
            'price_cents':raffle.quota_price_cents,'rules_text':raffle.rules_text,'draw_at':raffle.draw_at.isoformat(),
            'authorization_reference':raffle.authorization_reference,'rules_accepted_at':now.isoformat(),
            'accepting_user_id':str(user.pk),'rules_sha256':hashlib.sha256(raffle.rules_text.encode()).hexdigest()})
    resource.reserved_count+=quantity;resource.save(update_fields=['capacity_total','reserved_count'])
    CapacityHold.objects.create(resource=resource,order_id=order.pk,order_line_id=line.pk,quantity=quantity,expires_at=order.expires_at,idempotency_key=f'RAFFLE-{order.pk}-{line.pk}')
    ReceivableDue.objects.create(order=order,due_at=order.expires_at,amount_cents=total)
    CheckoutSubmission.objects.create(order=order,client_idempotency_key=request_key,request_hash=digest)
    log_audit(actor=user,event_id=reg.event_id,action='RAFFLE_NUMBERS_RESERVED_RULES_ACCEPTED',entity_type='Order',entity_id=order.pk,
        diff={'registration_id':str(reg.pk),'quantity':quantity,'amount_cents':total,'rules_sha256':line.item_snapshot_json['rules_sha256']},request=request)
    return order
