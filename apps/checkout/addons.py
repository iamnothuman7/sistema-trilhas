"""Paid-registration-only extras, shared payment and inventory reconciliation."""
import hashlib
import json
import secrets
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.catalog.models import AddOn, CapacityResource, CapacityHold
from apps.events.models import Event
from apps.registrations.models import Registration, Entitlement
from .models import Order, OrderLine, OrderStatus, ReceivableDue, CheckoutSubmission


def eligible_registrations(user):
    return Registration.objects.filter(
        Q(person__user_links__user=user) | Q(source_order_line__order__purchaser_user=user),
        commercial_state='ACTIVE', source_order_line__order__status=OrderStatus.PAID,
        event__status__in=['PUBLISHED','SALES_CLOSED','IN_PROGRESS'], event__ends_at__gt=timezone.now(), archived_at__isnull=True).distinct()


def inventory_items(event, active=True):
    rows = list(AddOn.objects.filter(event=event, **({'active':True} if active else {})).order_by('title','variant_code','pk'))
    stock = {r.reference_id:r for r in CapacityResource.objects.filter(event=event, type='ADDON_TOTAL')}
    for item in rows:
        resource = stock.get(str(item.pk))
        item.available_quantity = max(0, item.max_quantity-(resource.reserved_count+resource.committed_count if resource else 0))
        item.sold_quantity = resource.committed_count if resource else 0
        item.reserved_quantity = resource.reserved_count if resource else 0
    return rows


@transaction.atomic
def reserve_addons(*, registration_id, user, items, request_key):
    request_hash = hashlib.sha256(json.dumps({'registration':str(registration_id),'items':sorted(items.items())},sort_keys=True).encode()).hexdigest()
    existing = CheckoutSubmission.objects.select_related('order').filter(client_idempotency_key=request_key).first()
    if existing:
        if existing.request_hash != request_hash or existing.order.purchaser_user_id != user.pk:
            raise ValidationError('Este formulário já foi utilizado com outros itens. Atualize a página.')
        return existing.order
    initial = eligible_registrations(user).filter(pk=registration_id).first()
    if not initial:
        raise ValidationError('Produtos extras exigem uma inscrição ativa e paga neste evento.')
    Event.objects.select_for_update().get(pk=initial.event_id)
    reg = Registration.objects.select_for_update(of=('self',)).select_related('person','event').filter(
        pk=registration_id, pk__in=eligible_registrations(user).values('pk')).first()
    if reg is None:
        raise ValidationError('A inscrição não está mais disponível para comprar extras.')
    # Serialize repeated clicks before touching stock.
    existing = CheckoutSubmission.objects.select_related('order').filter(client_idempotency_key=request_key).first()
    if existing:
        if existing.request_hash != request_hash or existing.order.purchaser_user_id != user.pk:
            raise ValidationError('Atualize o formulário antes de continuar.')
        return existing.order
    if not items or len(items)>20 or any(not isinstance(q,int) or not 1<=q<=20 for q in items.values()):
        raise ValidationError('Selecione entre 1 e 20 unidades de cada produto extra.')
    ids = list(items)
    addons = list(AddOn.objects.select_for_update().filter(event=reg.event, pk__in=ids, active=True).order_by('pk'))
    if len(addons)!=len(ids):
        raise ValidationError('Um dos produtos selecionados não está disponível para este evento.')
    now=timezone.now()
    order=Order.objects.create(event=reg.event,purchaser_user=user,purchaser_name=user.full_name,purchaser_email=user.email,
        purchaser_phone=user.phone or reg.person.phone or '', purchaser_document=reg.participant_cpf,
        merchant_reference='EX-'+secrets.token_hex(6).upper(),expires_at=now+timedelta(minutes=35))
    total=0
    for addon in addons:
        quantity=items[str(addon.pk)]
        resource,_=CapacityResource.objects.select_for_update().get_or_create(event=reg.event,type='ADDON_TOTAL',reference_id=str(addon.pk),defaults={'capacity_total':addon.max_quantity})
        resource.capacity_total=addon.max_quantity
        if resource.available_count<quantity:
            raise ValidationError(f'Estoque insuficiente para {addon.title}. Restam {resource.available_count} unidade(s).')
        line=OrderLine.objects.create(order=order,addon=addon,linked_registration_id=reg.pk,intended_person_name=reg.person.full_name,
            intended_variant_code=addon.variant_code,quantity=quantity,unit_cents=addon.price_cents,net_cents=addon.price_cents*quantity,
            item_snapshot_json={'kind':'ADDON','product_name':addon.title,'variant_code':addon.variant_code,'price_cents':addon.price_cents})
        resource.reserved_count+=quantity;resource.save(update_fields=['capacity_total','reserved_count'])
        CapacityHold.objects.create(resource=resource,order_id=order.pk,order_line_id=line.pk,quantity=quantity,expires_at=order.expires_at,
            idempotency_key=f'ADDON-{order.pk}-{line.pk}')
        total+=line.net_cents
    if total<=0:
        raise ValidationError('Os produtos extras precisam ter preço definido para compra online.')
    order.gross_cents=order.total_cents=total;order.save(update_fields=['gross_cents','total_cents','updated_at'])
    ReceivableDue.objects.create(order=order,due_at=order.expires_at,amount_cents=total)
    CheckoutSubmission.objects.create(order=order,client_idempotency_key=request_key,request_hash=request_hash)
    return order


def activate_addons(order):
    for line in order.lines.filter(addon__isnull=False):
        Entitlement.objects.get_or_create(source_order_line=line, defaults={'registration_id':line.linked_registration_id,
            'benefit_type':'ADICIONAL','title':(line.item_snapshot_json.get('product_name','Produto extra')+' '+line.intended_variant_code)[:150],
            'quantity_total':line.quantity,'state':'AVAILABLE'})
